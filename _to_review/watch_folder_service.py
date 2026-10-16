"""
watch_folder_service.py — StudentFolderMaker Watch Folder Background Service

Monitors a root directory for new student subfolders.  When a subfolder appears
and its contents have been stable (unchanged) for STABLE_SECS, every PDF inside
it is processed with pdf_smart_split_merge_rename + generate_checklist_report.

Thread model
------------
  _monitor_thread : polls watch_path every POLL_SECS, enqueues stable folders.
  _worker_thread  : serialises processing (one folder at a time) so GPT-4o
                    rate limits are not hit by parallel requests.

All UI callbacks are delivered via ui_after(0, fn) — safe to call from any
background thread (tkinter is not thread-safe).
"""

from __future__ import annotations

import json
import os
import time
import threading
import queue
from dataclasses import dataclass, field
from typing import Callable

import file_ops as fo

# ── tunables ─────────────────────────────────────────────────────────────────
STABLE_SECS: float = 30.0   # folder must be unchanged for this long before processing
POLL_SECS:   float  = 5.0   # how often the monitor thread scans the watch folder

_DONE_MARKER = "_sfm_done.json"   # written into each student folder after success


# ── queue entry ───────────────────────────────────────────────────────────────
@dataclass
class WatchEntry:
    path:        str
    status:      str   = "pending"   # pending | processing | done | error | skipped
    added_at:    float = field(default_factory=time.time)
    finished_at: float | None = None
    error_msg:   str   = ""
    n_pdfs:      int   = 0
    n_created:   int   = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def elapsed_str(self) -> str:
        end = self.finished_at if self.finished_at is not None else time.time()
        secs = int(end - self.added_at)
        m, s = divmod(secs, 60)
        return f"{m}m {s:02d}s" if m else f"{s}s"

    @property
    def status_icon(self) -> str:
        return {
            "pending":    "⏳",
            "processing": "⚙",
            "done":       "✅",
            "error":      "❌",
            "skipped":    "⏭",
        }.get(self.status, "?")


# ── service ───────────────────────────────────────────────────────────────────
class WatchFolderService:
    """
    Background monitor + serial queue processor.

    Usage::

        svc = WatchFolderService(
            ui_after   = root.after,
            log        = app.log_message,
            on_changed = panel.refresh,
        )
        svc.start(watch_path="D:/students", api_key="sk-…")
        …
        svc.stop()
    """

    def __init__(
        self,
        ui_after:   Callable,               # widget.after — marshals to Tk main thread
        log:        Callable[[str], None],
        on_changed: Callable[[], None],     # called on main thread whenever entries change
    ) -> None:
        self._ui_after   = ui_after
        self._log_fn     = log
        self._on_changed = on_changed

        self.watch_path: str  = ""
        self.api_key:    str  = ""
        self.enabled:    bool = False

        self._entries:      list[WatchEntry] = []
        self._entries_lock  = threading.Lock()

        # path → (fingerprint, first_stable_timestamp)
        self._seen:     dict[str, tuple[int, float]] = {}
        self._enqueued: set[str] = set()   # paths already added to queue or done

        self._work_q:        queue.Queue[str | None] = queue.Queue()
        self._stop_event     = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._worker_thread:  threading.Thread | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self, watch_path: str, api_key: str) -> None:
        """(Re-)start monitoring watch_path.  Safe to call while already running."""
        self.stop()
        self.watch_path = os.path.abspath(watch_path)
        self.api_key    = api_key
        self.enabled    = True
        self._stop_event.clear()
        self._work_q    = queue.Queue()   # fresh queue — discards leftover poison pill

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="wf-monitor"
        )
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="wf-worker"
        )
        self._monitor_thread.start()
        self._worker_thread.start()
        self._ui_log(f"[Watch Folder] Started — monitoring: {self.watch_path}")
        self._notify()

    def stop(self) -> None:
        """Stop the monitor + worker threads gracefully."""
        if not self.enabled:
            return
        self.enabled = False
        self._stop_event.set()
        self._work_q.put(None)   # unblock worker so it can exit
        self._ui_log("[Watch Folder] Stopped.")
        self._notify()

    # ── monitor loop ──────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(POLL_SECS):
            try:
                self._scan_once()
            except Exception as exc:
                self._ui_log(f"[Watch Folder] Scan error: {exc}")

    def _scan_once(self) -> None:
        if not self.watch_path or not os.path.isdir(self.watch_path):
            return
        try:
            dir_entries = list(os.scandir(self.watch_path))
        except OSError:
            return

        now = time.time()
        for de in dir_entries:
            if not de.is_dir(follow_symlinks=False):
                continue
            path = os.path.abspath(de.path)

            with self._entries_lock:
                already = path in self._enqueued
            if already:
                continue

            # Skip folders that were already processed in a previous session
            if os.path.isfile(os.path.join(path, _DONE_MARKER)):
                with self._entries_lock:
                    self._enqueued.add(path)
                continue

            fp = self._fingerprint(path)
            prev = self._seen.get(path)

            if prev is None or prev[0] != fp:
                # New or changed since last poll — reset stability timer
                self._seen[path] = (fp, now)
            else:
                # Fingerprint unchanged — check whether stable long enough
                stable_secs = now - prev[1]
                if stable_secs >= STABLE_SECS:
                    pdfs = self._collect_pdfs(path)
                    if pdfs:
                        self._enqueue(path, len(pdfs))

    @staticmethod
    def _fingerprint(path: str) -> int:
        """Cheap stability fingerprint: sum of (mtime_ns + size) for all files."""
        total = 0
        try:
            for root, _dirs, files in os.walk(path):
                for fn in files:
                    if fn == _DONE_MARKER:
                        continue
                    try:
                        st = os.stat(os.path.join(root, fn))
                        total += st.st_mtime_ns + st.st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    @staticmethod
    def _collect_pdfs(path: str) -> list[str]:
        try:
            return sorted(
                os.path.join(path, fn)
                for fn in os.listdir(path)
                if fn.lower().endswith(".pdf")
            )
        except OSError:
            return []

    def _enqueue(self, path: str, n_pdfs: int) -> None:
        with self._entries_lock:
            if path in self._enqueued:
                return
            self._enqueued.add(path)
            entry = WatchEntry(path=path, n_pdfs=n_pdfs)
            self._entries.append(entry)
        self._work_q.put(path)
        self._ui_log(
            f"[Watch Folder] Queued: {os.path.basename(path)} ({n_pdfs} PDF(s))"
        )
        self._notify()

    # ── worker loop ───────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while True:
            item = self._work_q.get()
            if item is None:
                break
            try:
                self._process(item)
            except Exception as exc:
                self._ui_log(f"[Watch Folder] Unexpected error [{os.path.basename(item)}]: {exc}")
                self._set_status(item, "error", str(exc))
            finally:
                self._work_q.task_done()

    def _process(self, folder_path: str) -> None:
        name = os.path.basename(folder_path)
        self._set_status(folder_path, "processing")
        self._ui_log(f"[Watch Folder] Processing: {name}")

        pdfs = self._collect_pdfs(folder_path)
        if not pdfs:
            self._set_status(folder_path, "error", "No PDFs found")
            return

        # Thread-safe log — marshal fo.* log calls through the Tk main thread
        def _tlog(msg: str) -> None:
            try:
                self._ui_after(0, lambda m=msg: self._log_fn(f"  {m}"))
            except Exception:
                pass

        # Load bilingual template pairs (English–Korean) for accurate renaming
        pairs = None
        try:
            # Import lazily to avoid circular import at module level
            from student_folder_maker import document_rename_merged_pairs  # type: ignore
            pairs = document_rename_merged_pairs()
        except Exception:
            pass

        all_out:     list[str] = []
        page_counts: list[int] = []
        ok_count = 0

        for pdf in pdfs:
            try:
                n = fo.pdf_page_count(pdf)
            except Exception:
                n = 1
            page_counts.append(n)

            stem    = os.path.splitext(os.path.basename(pdf))[0]
            out_dir = folder_path

            ok, out_paths = fo.pdf_smart_split_merge_rename(
                pdf,
                out_dir,
                _tlog,
                fallback_prefix=stem,
                gpt4o_api_key=self.api_key,
                template_pairs=pairs,
            )
            if ok:
                ok_count += 1
                all_out.extend(out_paths)

        # Generate checklist Word report once all PDFs are done
        report_path = ""
        if all_out:
            try:
                report_path = fo.generate_checklist_report(
                    all_out,
                    self.api_key,
                    _tlog,
                    original_page_counts=page_counts,
                    source_pdf_paths=pdfs,
                    metadata={},
                )
            except Exception as rpt_err:
                _tlog(f"Report error: {rpt_err}")

        # Write done marker so this folder is skipped on future app launches
        try:
            with open(os.path.join(folder_path, _DONE_MARKER), "w", encoding="utf-8") as mf:
                json.dump(
                    {
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source_pdfs":  len(pdfs),
                        "output_files": len(all_out),
                        "report":       os.path.basename(report_path) if report_path else "",
                    },
                    mf,
                    indent=2,
                )
        except OSError:
            pass

        # Update entry
        with self._entries_lock:
            for e in self._entries:
                if e.path == folder_path:
                    e.n_created = len(all_out)
                    break

        if ok_count:
            self._set_status(folder_path, "done")
            self._ui_log(
                f"[Watch Folder] ✅ Done: {name} — "
                f"{len(all_out)} file(s) created"
                + (f"  |  Report: {os.path.basename(report_path)}" if report_path else "")
            )
        else:
            self._set_status(folder_path, "error", "All PDFs failed — check log")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, path: str, status: str, error: str = "") -> None:
        with self._entries_lock:
            for e in self._entries:
                if e.path == path:
                    e.status    = status
                    e.error_msg = error
                    if status in ("done", "error"):
                        e.finished_at = time.time()
        self._notify()

    def _notify(self) -> None:
        """Deliver on_changed callback on the Tk main thread."""
        try:
            self._ui_after(0, self._on_changed)
        except Exception:
            pass

    def _ui_log(self, msg: str) -> None:
        try:
            self._ui_after(0, lambda m=msg: self._log_fn(m))
        except Exception:
            pass

    # ── public accessors ──────────────────────────────────────────────────────

    def get_entries(self) -> list[WatchEntry]:
        with self._entries_lock:
            return list(self._entries)

    @property
    def active_count(self) -> int:
        with self._entries_lock:
            return sum(1 for e in self._entries if e.status in ("pending", "processing"))

    def clear_completed(self) -> None:
        """Remove done/error entries from the list (keeps pending/processing)."""
        with self._entries_lock:
            self._entries  = [e for e in self._entries if e.status not in ("done", "error", "skipped")]
            self._enqueued = {e.path for e in self._entries}
        self._notify()

    def requeue_errors(self) -> None:
        """Re-submit failed entries for another processing attempt."""
        to_retry: list[str] = []
        with self._entries_lock:
            for e in self._entries:
                if e.status == "error":
                    e.status      = "pending"
                    e.error_msg   = ""
                    e.added_at    = time.time()
                    e.finished_at = None
                    to_retry.append(e.path)
        for p in to_retry:
            self._work_q.put(p)
        self._notify()
