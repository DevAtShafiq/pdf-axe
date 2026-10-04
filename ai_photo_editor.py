# -*- coding: utf-8 -*-
"""
AI photo enhancement for the file workspace.
All actions use OpenAI ``gpt-image-1`` ``images.edit``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import tkinter as tk
import uuid
from tkinter import messagebox, ttk
from typing import Any, Callable

import file_ops as fo

_DEFAULT_AI_EDIT_USD_EST = 0.04
_AI_EDIT_MODEL = "gpt-image-1"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_IDENTITY_PREFIX = (
    "DO NOT change the person's face, head, or identity in any way. "
    "The face must remain completely unchanged — same skin tone, features, and expression. "
)

_ACTION_PROMPTS: dict[str, dict] = {
    "wear_suit": {
        "label": "Wear Suit & Tie",
        "prompt": (
            "Replace the clothing on the person's body with a formal dark business suit and tie. "
            "Keep the face, hair, and skin completely unchanged. "
            "Realistic fabric, natural lighting and shadows on the clothing."
        ),
        "mask_strategy": "body_only",   # protect face/head, edit body
        "background": "opaque",
        "quality": "high",
    },
    "remove_grain": {
        "label": "Remove Grain",
        "prompt": (
            "Remove all digital noise, grain, and compression artifacts from this image. "
            "Smooth skin tones naturally. Preserve all sharpness, edges, and fine detail. "
            "Do NOT change colors, composition, or any facial features."
        ),
        "mask_strategy": "full",        # edit everything (grain is everywhere)
        "background": "opaque",
        "quality": "high",
    },
    "professional_enhance": {
        "label": "Professional Enhance",
        "prompt": (
            "Apply professional portrait retouching: correct exposure, improve contrast, "
            "reduce skin blemishes slightly, enhance sharpness, balance white balance. "
            "Make this look like a professional studio portrait. "
            "Do NOT change facial structure or identity."
        ),
        "mask_strategy": "full",
        "background": "opaque",
        "quality": "high",
    },
    "white_background": {
        "label": "White Background",
        "prompt": (
            "Replace the entire background with a clean solid white background. "
            "Keep the person completely unchanged — same face, hair, clothing, and body. "
            "Preserve all hair and edge detail cleanly against the white background."
        ),
        "mask_strategy": "background_only",  # protect subject, edit background
        "background": "opaque",
        "quality": "high",
    },
    "passport_mode": {
        "label": "Full Passport Mode",
        "prompt": (
            "Convert to a professional passport photo: clean white background, "
            "person centered, professional even lighting, no shadows behind the subject. "
            "Keep the person's face, hair, and clothing exactly as they are."
        ),
        "mask_strategy": "background_only",
        "background": "opaque",
        "quality": "high",
    },
}


# ---------------------------------------------------------------------------
# Mask generation
# ---------------------------------------------------------------------------

def _make_mask_full(w: int, h: int) -> "Image":
    """All-white mask — entire image is editable."""
    from PIL import Image
    return Image.new("RGBA", (w, h), (255, 255, 255, 255))


def _make_mask_body_only(w: int, h: int) -> "Image":
    """
    Protect the face/head (top ~50% transparent = locked).
    Allow editing the body/clothing area (bottom ~50% white = editable).
    The transition is gradual to avoid a harsh seam.
    """
    from PIL import Image
    import struct, zlib

    mask = Image.new("RGBA", (w, h), (0, 0, 0, 0))  # start all transparent (protected)
    pixels = mask.load()

    # Face protection zone: top 0% → 45% of height = fully transparent (locked)
    # Transition zone: 45% → 60% = gradient from transparent to white
    # Editable zone: 60% → 100% = fully white (editable)
    face_end = int(h * 0.45)
    blend_end = int(h * 0.60)

    for y in range(h):
        if y <= face_end:
            alpha = 0          # fully transparent = protected
        elif y >= blend_end:
            alpha = 255        # fully white = editable
        else:
            t = (y - face_end) / max(1, blend_end - face_end)
            alpha = int(t * 255)
        for x in range(w):
            pixels[x, y] = (255, 255, 255, alpha)

    return mask


def _make_mask_background_only(w: int, h: int) -> "Image":
    """
    Protect the central subject area (transparent = locked).
    Allow editing the background around the edges (white = editable).
    Uses a simple elliptical subject region heuristic for portrait photos.
    """
    from PIL import Image, ImageDraw, ImageFilter

    mask = Image.new("RGBA", (w, h), (255, 255, 255, 255))  # all editable (white)
    draw = ImageDraw.Draw(mask)

    # Protect an ellipse covering ~60% width × ~85% height, centered
    # This roughly covers the head + shoulders in a passport-style portrait
    pad_x = int(w * 0.18)
    pad_y = int(h * 0.06)
    bbox = (pad_x, pad_y, w - pad_x, h - pad_y)
    draw.ellipse(bbox, fill=(0, 0, 0, 0))  # transparent = protected

    # Blur edges so the transition is smooth
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(2, min(w, h) // 30)))
    return mask


def _build_mask_for_action(action_name: str, w: int, h: int) -> "Image | None":
    strategy = _ACTION_PROMPTS.get(action_name, {}).get("mask_strategy", "full")
    try:
        if strategy == "full":
            return None       # no mask = whole image editable
        elif strategy == "body_only":
            return _make_mask_body_only(w, h)
        elif strategy == "background_only":
            return _make_mask_background_only(w, h)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ai_photo_cost_usd_per_edit_est() -> float:
    merge_dotenv_into_environ()
    raw = (os.environ.get("OPENAI_IMAGE_EDIT_USD_EST") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_AI_EDIT_USD_EST


def _ai_photo_cost_hint_text() -> str:
    usd = _ai_photo_cost_usd_per_edit_est()
    return (
        f"Cost (est.): ~${usd:.2f}+ USD per run  |  Model: {_AI_EDIT_MODEL}  "
        "| https://openai.com/api/pricing/"
    )


def _app_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _read_dotenv_pairs(path: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    continue
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                pairs.append((key, val))
    except OSError:
        pass
    return pairs


_OPENAI_ENV_ALWAYS_FROM_APP_ENV: frozenset[str] = frozenset(
    {"OPENAI_API_KEY", "OPENAI_PROJECT_ID", "OPENAI_ORG_ID", "OPENAI_BASE_URL"}
)


def _dotenv_paths_for_app() -> list[str]:
    exe_dir = _app_base_dir()
    paths = [os.path.join(exe_dir, ".env")]
    if getattr(sys, "frozen", False):
        parent = os.path.dirname(exe_dir)
        par_env = os.path.join(parent, ".env")
        if os.path.normcase(os.path.abspath(par_env)) != os.path.normcase(
            os.path.abspath(paths[0])
        ):
            paths.append(par_env)
    return paths


def merge_dotenv_into_environ() -> None:
    always = _OPENAI_ENV_ALWAYS_FROM_APP_ENV
    paths = _dotenv_paths_for_app()
    seen_file = False
    for env_path in paths:
        if not os.path.isfile(env_path):
            continue
        fill_only = seen_file
        seen_file = True
        for key, val in _read_dotenv_pairs(env_path):
            if not val:
                continue
            if key in always:
                if fill_only and (os.environ.get(key) or "").strip():
                    continue
                os.environ[key] = val
            elif not os.environ.get(key):
                os.environ[key] = val
    loaded_abs = {os.path.abspath(p) for p in paths if os.path.isfile(p)}
    cwd_path = os.path.join(os.getcwd(), ".env")
    if os.path.isfile(cwd_path) and os.path.abspath(cwd_path) not in loaded_abs:
        for key, val in _read_dotenv_pairs(cwd_path):
            if val and not os.environ.get(key):
                os.environ[key] = val


def _openai_key_looks_valid(key: str) -> bool:
    k = (key or "").strip()
    if len(k) < 20:
        return False
    return k.startswith("sk")


def _pil_image():
    try:
        from PIL import Image
        return Image
    except ImportError:
        return None


def _openai_client():
    try:
        from openai import OpenAI
        return OpenAI
    except ImportError:
        return None


def _unique_ai_sidecar_path(original_path: str) -> str:
    original_path = os.path.abspath(os.path.normpath(original_path))
    d, base = os.path.dirname(original_path), os.path.basename(original_path)
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = ".png"
    n = 0
    while True:
        suffix = "_ai" if n == 0 else f"_ai_{n + 1}"
        cand = os.path.join(d, f"{stem}{suffix}{ext}")
        if not os.path.exists(cand):
            return cand
        n += 1
        if n > 5000:
            return os.path.join(d, f"{stem}_ai_{uuid.uuid4().hex[:8]}{ext}")


# ---------------------------------------------------------------------------
# Main frame
# ---------------------------------------------------------------------------

class AIPhotoEditorFrame(tk.Frame):
    """Left: preview (max 400 px). Right: enhancement actions (ttk)."""

    PREVIEW_MAX = 400
    UPLOAD_MAX_EDGE = 1024

    def __init__(self, parent: tk.Misc, workspace: tk.Misc, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self._workspace = workspace
        self._session_dir: str | None = None
        self._temp_output_path: str | None = None
        self._session_source_path: str | None = None
        self._ai_photo_session: dict[str, Any] | None = None
        self._processing = False
        self._preview_photo: tk.PhotoImage | None = None
        self._op_buttons: list[ttk.Button] = []
        self._current_action: str | None = None

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)
        self.rowconfigure(0, weight=1)

        # ── Left: preview canvas ─────────────────────────────────────────
        left = ttk.Frame(self, padding=(0, 0, 12, 0))
        left.grid(row=0, column=0, sticky=(tk.N, tk.S, tk.W, tk.E))
        self._preview_canvas = tk.Canvas(
            left,
            width=self.PREVIEW_MAX,
            height=self.PREVIEW_MAX,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            bg="#f1f5f9",
        )
        self._preview_canvas.pack()

        # ── Right: controls ──────────────────────────────────────────────
        right = ttk.Frame(self, padding=(4, 0, 0, 0))
        right.grid(row=0, column=1, sticky=(tk.N, tk.W))

        self._status_var = tk.StringVar(value="Use Single preview (or tree), then open this tool.")
        ttk.Label(right, textvariable=self._status_var, wraplength=230, font=("Segoe UI", 9)).pack(
            anchor=tk.W, pady=(0, 4)
        )

        self._source_detail_var = tk.StringVar(value="")
        ttk.Label(
            right,
            textvariable=self._source_detail_var,
            wraplength=230,
            font=("Segoe UI", 8),
            foreground="#334155",
        ).pack(anchor=tk.W, pady=(0, 8))

        # Effect buttons
        ttk.Label(right, text="Effects (all use gpt-image-1):", font=("Segoe UI", 8, "bold")).pack(anchor=tk.W, pady=(0, 4))
        for action_key, info in _ACTION_PROMPTS.items():
            b = ttk.Button(
                right,
                text=info["label"],
                command=lambda ak=action_key: self.run_ai_photo_action(ak),
                width=24,
            )
            b.pack(anchor=tk.W, pady=(0, 3))
            self._op_buttons.append(b)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Button(right, text="Confirm & Replace", command=self.confirm_replace, width=24).pack(anchor=tk.W, pady=(0, 3))
        ttk.Button(right, text="Reset", command=self.reset_image, width=24).pack(anchor=tk.W, pady=(0, 3))

        # Progress
        self._processing_label = ttk.Label(right, text="", font=("Segoe UI", 9, "italic"), foreground="#64748b")
        self._processing_label.pack(anchor=tk.W, pady=(8, 0))
        self._progress_bar = ttk.Progressbar(right, mode="indeterminate", length=230)
        self._progress_bar.pack(anchor=tk.W, fill=tk.X, pady=(4, 0))

        # Cost / info
        ttk.Label(
            right,
            text=_ai_photo_cost_hint_text(),
            wraplength=230,
            font=("Segoe UI", 8),
            foreground="#0f172a",
        ).pack(anchor=tk.W, pady=(10, 0))

        ttk.Label(
            right,
            text=(
                ".env beside .exe: OPENAI_API_KEY=sk-…\n"
                "For sk-proj- keys also add: OPENAI_PROJECT_ID=proj_…\n"
                "Source: single preview tab or selected tree image."
            ),
            wraplength=230,
            font=("Segoe UI", 8),
            foreground="#64748b",
        ).pack(anchor=tk.W, pady=(6, 0))

    # ── Session management ───────────────────────────────────────────────

    def cleanup_session(self) -> None:
        d = self._session_dir
        if d and os.path.isdir(d):
            try:
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
        self._session_dir = None
        self._temp_output_path = None
        self._session_source_path = None
        self._ai_photo_session = None

    def open_session(self) -> None:
        if self._session_dir and os.path.isdir(self._session_dir):
            return
        self._session_dir = tempfile.mkdtemp(prefix="sfm_ai_photo_")
        self._temp_output_path = os.path.join(self._session_dir, "preview_out.png")

    def on_close(self) -> None:
        self.cleanup_session()

    # ── Logging ──────────────────────────────────────────────────────────

    def _log(self, line: str) -> None:
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            log_fn = getattr(self._workspace, "_append_app_log_line", None)
            if callable(log_fn):
                log_fn(line)
        except Exception:
            pass

    # ── UI state ─────────────────────────────────────────────────────────

    def _set_processing(self, on: bool, label: str = "Processing…") -> None:
        self._processing = on
        state = "disabled" if on else "normal"
        for b in self._op_buttons:
            try:
                b.configure(state=state)
            except tk.TclError:
                pass
        if on:
            self._processing_label.config(text=label)
            try:
                self._progress_bar.start(12)
            except tk.TclError:
                pass
        else:
            self._processing_label.config(text="")
            try:
                self._progress_bar.stop()
            except tk.TclError:
                pass

    def _ui_safe(self, fn: Callable[[], None]) -> None:
        try:
            self.after(0, fn)
        except tk.TclError:
            try:
                fn()
            except Exception:
                pass

    # ── Session sync ─────────────────────────────────────────────────────

    def _sync_session_from_preview(self, *, force: bool) -> bool:
        getter = getattr(self._workspace, "get_active_ai_image_source", None)
        if not callable(getter):
            return False
        try:
            ctx = getter()
        except Exception:
            return False
        if not ctx or not ctx.get("working_input_path"):
            return False
        wp = str(ctx["working_input_path"])
        sk = str(ctx.get("source_kind") or "")
        zp = ctx.get("zip_replace")
        if (
            not force
            and self._ai_photo_session is not None
            and self._ai_photo_session.get("working_input_path") == wp
            and self._ai_photo_session.get("source_kind") == sk
            and self._ai_photo_session.get("zip_replace") == zp
        ):
            return True
        self._ai_photo_session = {
            "working_input_path": wp,
            "display_name": str(ctx.get("display_name") or os.path.basename(wp)),
            "source_kind": sk,
            "zip_replace": zp,
            "original_path": ctx.get("original_path"),
            "is_temp": bool(ctx.get("is_temp")),
        }
        dn = (self._ai_photo_session or {}).get("display_name") or os.path.basename(wp)
        self._source_detail_var.set(f"Editing: {dn}\nSource: {sk.replace('_', ' ') if sk else 'unknown'}")
        return True

    def _bind_session_to_path(self, path: str) -> bool:
        sd = self._session_dir
        if not sd:
            return False
        try:
            self._temp_output_path = os.path.join(sd, "preview_out.png")
            self._session_source_path = path
            return True
        except Exception:
            return False

    # ── Preview ──────────────────────────────────────────────────────────

    def display_image(self, path: str) -> None:
        Image = _pil_image()
        if not Image:
            return
        try:
            im = Image.open(path)
            im.thumbnail((self.PREVIEW_MAX, self.PREVIEW_MAX), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            buf.seek(0)
            self._preview_photo = tk.PhotoImage(data=buf.read())
        except Exception:
            try:
                self._preview_photo = tk.PhotoImage(file=path)
            except Exception:
                return
        self._preview_canvas.delete("all")
        self._preview_canvas.create_image(
            self.PREVIEW_MAX // 2,
            self.PREVIEW_MAX // 2,
            image=self._preview_photo,
        )

    def reset_image(self) -> None:
        path = str((self._ai_photo_session or {}).get("working_input_path") or "")
        if path and os.path.isfile(path):
            self.display_image(path)
        self._status_var.set("Reset to original.")

    # ── Image prep ───────────────────────────────────────────────────────

    def _prepare_upload_png(self, path: str) -> tuple[bytes, int, int] | None:
        """Resize to max 1024px longest edge, RGBA PNG. Returns (bytes, width, height)."""
        Image = _pil_image()
        if not Image:
            return None
        try:
            im = Image.open(path).convert("RGBA")
            w, h = im.size
            edge = max(w, h)
            if edge > self.UPLOAD_MAX_EDGE:
                scale = self.UPLOAD_MAX_EDGE / float(edge)
                im = im.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            w, h = im.size
            buf = io.BytesIO()
            im.save(buf, format="PNG", compress_level=6)
            return buf.getvalue(), w, h
        except Exception:
            return None

    # ── Main action runner ───────────────────────────────────────────────

    def run_ai_photo_action(self, action_name: str) -> None:
        if action_name == "confirm_replace":
            self.confirm_replace()
            return
        if action_name == "reset_image":
            self.reset_image()
            return
        if self._processing:
            return

        if not _pil_image():
            messagebox.showerror("AI Error", "Pillow is required.\n  pip install pillow", parent=self.winfo_toplevel())
            return

        if not self._sync_session_from_preview(force=False):
            messagebox.showerror(
                "AI Error",
                "No valid image selected.\n\nUse the Single preview tab (or select an on-disk image in the tree), then try again.",
                parent=self.winfo_toplevel(),
            )
            return

        if action_name not in _ACTION_PROMPTS:
            messagebox.showerror("AI Error", f"Unknown action: {action_name}", parent=self.winfo_toplevel())
            return

        path = str(self._ai_photo_session.get("working_input_path") or "")
        if not path or not os.path.isfile(path):
            messagebox.showerror("AI Error", "No valid image file found. Select an image in the preview first.", parent=self.winfo_toplevel())
            return

        ext = os.path.splitext(path)[1].lower()
        if ext not in fo.IMAGE_EXT:
            messagebox.showerror("AI Error", f"Not a supported image format: {ext}", parent=self.winfo_toplevel())
            return

        if not self._bind_session_to_path(path):
            return

        sd = self._session_dir
        if not sd or not os.path.isdir(sd):
            messagebox.showerror("AI Error", "Session folder missing.", parent=self.winfo_toplevel())
            return

        # ── Validate API key ─────────────────────────────────────────────
        merge_dotenv_into_environ()

        OpenAI = _openai_client()
        if not OpenAI:
            messagebox.showerror(
                "AI Error",
                "OpenAI SDK not installed.\n\n  pip install openai",
                parent=self.winfo_toplevel(),
            )
            return

        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            base = _app_base_dir()
            messagebox.showerror(
                "AI Error",
                f"No API key found.\n\nCreate .env in:\n  {base}\n\nWith this line:\n  OPENAI_API_KEY=sk-...",
                parent=self.winfo_toplevel(),
            )
            return
        if not _openai_key_looks_valid(api_key):
            messagebox.showerror(
                "AI Error",
                "OPENAI_API_KEY does not look valid (should start with sk-).\n\n"
                "If using a project key (sk-proj-…) also add:\n  OPENAI_PROJECT_ID=proj_…",
                parent=self.winfo_toplevel(),
            )
            return

        # ── Prepare upload ───────────────────────────────────────────────
        result = self._prepare_upload_png(path)
        if not result:
            messagebox.showerror("AI Error", "Could not read or resize the image.", parent=self.winfo_toplevel())
            return
        png_bytes, img_w, img_h = result

        upload_path = os.path.join(sd, "upload.png")
        try:
            with open(upload_path, "wb") as wf:
                wf.write(png_bytes)
        except OSError as e:
            messagebox.showerror("AI Error", f"Could not write upload file:\n{e}", parent=self.winfo_toplevel())
            return

        # ── Build face-protection mask ───────────────────────────────────
        mask_path: str | None = None
        mask_img = _build_mask_for_action(action_name, img_w, img_h)
        if mask_img is not None:
            try:
                mask_path = os.path.join(sd, "mask.png")
                mask_img.save(mask_path, format="PNG")
            except Exception:
                mask_path = None

        action_info = _ACTION_PROMPTS[action_name]
        prompt = action_info["prompt"]
        quality = action_info.get("quality", "high")
        background = action_info.get("background", "opaque")

        project_id = (os.environ.get("OPENAI_PROJECT_ID") or "").strip() or None
        org_id = (os.environ.get("OPENAI_ORG_ID") or "").strip() or None
        base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None

        action_label = action_info["label"]
        self._current_action = action_name
        self._set_processing(True, f"Running: {action_label}…")
        self._status_var.set(f"Sending to API: {action_label}…")

        self._log(f"[AI Photo] === REQUEST action={action_name} ===")
        self._log(f"[AI Photo] model={_AI_EDIT_MODEL} quality={quality} background={background}")
        self._log(f"[AI Photo] source_path={path}")
        self._log(f"[AI Photo] upload_size={img_w}x{img_h} bytes={len(png_bytes)}")
        self._log(f"[AI Photo] mask={'YES (face protected)' if mask_path else 'NONE (full image editable)'}")

        def worker() -> None:
            err: str | None = None
            logs: list[str] = []
            out_bytes: bytes | None = None

            try:
                client_kw: dict = {"api_key": api_key}
                if project_id:
                    client_kw["project"] = project_id
                if org_id:
                    client_kw["organization"] = org_id
                if base_url:
                    client_kw["base_url"] = base_url
                client = OpenAI(**client_kw)

                call_kwargs: dict = dict(
                    model=_AI_EDIT_MODEL,
                    prompt=prompt,
                    n=1,
                    size="auto",
                    quality=quality,
                    background=background,
                )

                if mask_path and os.path.isfile(mask_path):
                    logs.append("[AI Photo] using face-protection mask")
                    with open(upload_path, "rb") as img_f, open(mask_path, "rb") as mask_f:
                        result = client.images.edit(image=img_f, mask=mask_f, **call_kwargs)
                else:
                    logs.append("[AI Photo] no mask (full image editable)")
                    with open(upload_path, "rb") as img_f:
                        result = client.images.edit(image=img_f, **call_kwargs)

                logs.append(f"[AI Photo] API call successful, n_items={len(result.data) if result.data else 0}")

                if not result or not result.data:
                    err = "Empty response from API."
                else:
                    item = result.data[0]
                    b64 = getattr(item, "b64_json", None)
                    url = getattr(item, "url", None)
                    logs.append(f"[AI Photo] has_b64={bool(b64)} has_url={bool(url)}")

                    if b64:
                        out_bytes = base64.b64decode(b64)
                        logs.append(f"[AI Photo] decoded b64 bytes={len(out_bytes)}")
                    elif url:
                        try:
                            import urllib.request
                            with urllib.request.urlopen(url, timeout=120) as resp:
                                out_bytes = resp.read()
                            logs.append(f"[AI Photo] downloaded url bytes={len(out_bytes)}")
                        except Exception as e:
                            err = f"Failed to download result image: {e}"
                    else:
                        err = "No image data in API response (no b64_json and no url)."

            except Exception as e:
                err = str(e)
                logs.append(f"[AI Photo] exception type={type(e).__name__} err={e!r}")

            # ── Process result and save ──────────────────────────────────
            if not err and out_bytes:
                try:
                    Image = _pil_image()
                    if not Image:
                        err = "Pillow required to decode result."
                    else:
                        result_img = Image.open(io.BytesIO(out_bytes)).convert("RGB")
                        # Resize result back to match original source dimensions
                        with Image.open(path) as ref_im:
                            orig_size = ref_im.convert("RGB").size
                        if result_img.size != orig_size:
                            result_img = result_img.resize(orig_size, Image.Resampling.LANCZOS)
                        outp = self._temp_output_path
                        if outp:
                            result_img.save(outp, format="PNG", compress_level=3)
                            logs.append(f"[AI Photo] saved preview to {outp}")
                except Exception as e:
                    err = f"Could not process API result image: {e}"
                    logs.append(f"[AI Photo] decode error={e!r}")

            def finish() -> None:
                for line in logs:
                    self._log(line)
                self._set_processing(False)
                if err:
                    self._status_var.set(f"Error: {err[:80]}")
                    self._log(f"[AI Photo] FAILED: {err}")
                    messagebox.showerror(
                        "AI photo error",
                        f"{action_label} failed:\n\n{err}",
                        parent=self.winfo_toplevel(),
                    )
                elif self._temp_output_path and os.path.isfile(self._temp_output_path):
                    self._log(f"[AI Photo] SUCCESS action={action_name}")
                    self.display_image(self._temp_output_path)
                    self._status_var.set(f"✓ {action_label} done — click Confirm & Replace to save.")
                else:
                    self._status_var.set("No output produced.")
                    self._log("[AI Photo] no output file produced")

            self._ui_safe(finish)

        threading.Thread(target=worker, daemon=True).start()

    # ── Confirm & Replace ────────────────────────────────────────────────

    def confirm_replace(self) -> None:
        s = self._ai_photo_session
        if not s:
            messagebox.showwarning("AI photo", "No session.", parent=self.winfo_toplevel())
            return
        outp = self._temp_output_path
        if not outp or not os.path.isfile(outp):
            messagebox.showinfo("AI photo", "Run an effect first, then Confirm & Replace.", parent=self.winfo_toplevel())
            return

        Image = _pil_image()
        if not Image:
            return
        try:
            im = Image.open(outp)
        except Exception as e:
            messagebox.showerror("AI Error", str(e), parent=self.winfo_toplevel())
            return

        # ZIP member replace
        zr = s.get("zip_replace")
        if zr and len(zr) == 2:
            zip_path, member = zr[0], zr[1]
            if not os.path.isfile(zip_path):
                messagebox.showerror("AI Error", f"ZIP not found:\n{zip_path}", parent=self.winfo_toplevel())
                return
            try:
                buf = io.BytesIO()
                ext_m = os.path.splitext(member)[1].lower()
                if ext_m in (".jpg", ".jpeg", ".jfif"):
                    im.convert("RGB").save(buf, format="JPEG", quality=92)
                else:
                    im.save(buf, format="PNG", compress_level=3)
                ok_z, zerr = fo.replace_zip_member_bytes(zip_path, member, buf.getvalue())
                if not ok_z:
                    messagebox.showerror("AI Error", zerr, parent=self.winfo_toplevel())
                    return
            except Exception as e:
                messagebox.showerror("AI Error", str(e), parent=self.winfo_toplevel())
                return
            self._status_var.set("ZIP member updated.")
            self._log(f"[AI Photo] confirm_replace zip member ok member={member!r}")
            return

        # On-disk replace
        working = str(self._session_source_path or s.get("working_input_path") or "")
        dest = working
        if s.get("is_temp") and s.get("original_path"):
            dest = str(s["original_path"])
        if not dest or not os.path.isfile(dest):
            dest = working
        try:
            _, ext_d = os.path.splitext(dest)
            ext_d = ext_d.lower()
            if ext_d in (".jpg", ".jpeg", ".jfif"):
                im.convert("RGB").save(dest, quality=95, format="JPEG")
            else:
                im.save(dest, format="PNG", compress_level=3)
        except Exception as e:
            messagebox.showerror("AI Error", str(e), parent=self.winfo_toplevel())
            return
        self._status_var.set(f"✓ Saved: {os.path.basename(dest)}")
        self._log(f"[AI Photo] confirm_replace saved path={dest!r}")


# ---------------------------------------------------------------------------
# Window entry point
# ---------------------------------------------------------------------------

def open_ai_photo_editor_window(workspace: tk.Misc) -> tk.Toplevel:
    top = tk.Toplevel(workspace)
    top.title("AI photo enhancement")
    top.minsize(560, 440)
    frame = AIPhotoEditorFrame(top, workspace)
    frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    frame.open_session()
    try:
        getter = getattr(workspace, "get_active_ai_image_source", None)
        if callable(getter):
            ctx = getter()
            if ctx:
                wp = str(ctx.get("working_input_path") or "")
                if wp and os.path.isfile(wp):
                    frame._bind_session_to_path(wp)
                    frame._sync_session_from_preview(force=True)
                    frame.display_image(wp)
    except Exception:
        pass

    def _on_close() -> None:
        try:
            frame.on_close()
        finally:
            top.destroy()

    top.protocol("WM_DELETE_WINDOW", _on_close)
    return top