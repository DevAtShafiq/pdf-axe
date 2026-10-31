# -*- coding: utf-8 -*-
"""
releases.py — the OfficeAxe release manager, merged into the portal.

Reads and writes the same Firestore documents, with the same field names and
types, as the earlier release-manager site, so installed copies that check
``meta/current`` for "Update Now" keep working unchanged:

    releases/{id}   version, versionCode, releaseNotes, downloadUrl, fileName,
                    fileSize, storagePath, sha256, mandatory, status
                    ("draft" | "published"), isCurrent, createdAt, updatedAt,
                    publishedAt, updatedBy
    meta/current    product "OfficeAxe", version, versionCode, releaseNotes,
                    downloadUrl, fileName, fileSize, sha256, mandatory,
                    releaseId, publishedAt
    docs/{id}       title, body, createdAt, updatedAt, updatedBy   (guides)
    activity/{id}   kind "download" | "problem", version, releaseId, detail,
                    createdAt                                 (public downloads)

Writes happen on the server, behind the portal's admin login.
"""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

PRODUCT = "OfficeAxe"
_ID_CHARS = string.ascii_letters + string.digits


class ReleaseError(ValueError):
    pass


def new_id() -> str:
    """20 random letters and digits, like the earlier site's ids."""
    return "".join(secrets.choice(_ID_CHARS) for _ in range(20))


def parse_version(text: str) -> Optional[dict]:
    """'1.4.2' / 'v1.4.2.0' → {version, versionCode}; None when not valid."""
    t = str(text or "").strip()
    t = re.sub(r"^v", "", t, flags=re.I)
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){1,3}", t):
        return None
    parts = [int(x) for x in t.split(".")]
    if any(p < 0 or p > 999 for p in parts):
        return None
    a, b, c, d = (parts + [0, 0, 0])[:4]
    return {"version": ".".join(str(p) for p in parts), "versionCode": a * 10**9 + b * 10**6 + c * 10**3 + d}


def file_name_from_url(url: str) -> str:
    try:
        name = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
        if name.lower().endswith(".exe"):
            return name[:180]
    except Exception:
        pass
    return "installer.exe"


def check_url(url: str, required: bool) -> Optional[str]:
    u = str(url or "").strip()
    if not u:
        return "Add a download URL." if required else None
    if len(u) > 2000:
        return "That download URL is too long."
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        return "Use a full link that starts with https://"
    return None


def as_dt(v) -> Optional[datetime]:
    """Firestore timestamp / datetime / ISO string → aware datetime."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if hasattr(v, "timestamp"):
        try:
            return datetime.fromtimestamp(v.timestamp(), timezone.utc)
        except Exception:
            return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


class ReleaseManager:
    def __init__(self, db, now: Optional[Callable[[], object]] = None):
        self.db = db
        # On Firestore the server stamps the time (firestore.SERVER_TIMESTAMP)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _r(self, rid):
        return self.db.collection("releases").document(rid)

    def _cur(self):
        return self.db.collection("meta").document("current")

    # ── reading ──────────────────────────────────────────────────────────────
    def current(self) -> dict:
        snap = self._cur().get()
        return snap.to_dict() if snap.exists else {}

    def get(self, rid: str) -> Optional[dict]:
        snap = self._r(rid).get()
        if not snap.exists:
            return None
        d = snap.to_dict()
        d["id"] = rid
        return d

    def list(self) -> list:
        rows = []
        for s in self.db.collection("releases").stream():
            d = s.to_dict()
            if not str(d.get("version") or ""):
                continue
            d["id"] = s.id
            rows.append(d)
        rows.sort(key=lambda r: as_dt(r.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return rows

    def guides(self) -> list:
        rows = []
        for s in self.db.collection("docs").stream():
            d = s.to_dict()
            if str(d.get("title") or "").strip() and str(d.get("body") or "").strip():
                d["id"] = s.id
                rows.append(d)
        rows.sort(key=lambda r: as_dt(r.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc))
        return rows

    def download_stats(self, days: int = 30) -> dict:
        since = datetime.now(timezone.utc).timestamp() - days * 86400
        downloads = problems = 0
        for s in self.db.collection("activity").stream():
            d = s.to_dict()
            at = as_dt(d.get("createdAt"))
            if not at or at.timestamp() < since:
                continue
            if d.get("kind") == "download":
                downloads += 1
            elif d.get("kind") == "problem":
                problems += 1
        return {"downloads": downloads, "problems": problems, "days": days}

    # ── writing ──────────────────────────────────────────────────────────────
    def _current_payload(self, r: dict, rid: str, published_at) -> dict:
        return {"product": PRODUCT, "version": r["version"], "versionCode": int(r["versionCode"]),
                "releaseNotes": r.get("releaseNotes", ""), "downloadUrl": r.get("downloadUrl", ""),
                "fileName": r.get("fileName", ""), "fileSize": int(r.get("fileSize") or 0),
                "sha256": r.get("sha256", ""), "mandatory": r.get("mandatory") is True,
                "releaseId": rid, "publishedAt": published_at}

    def create(self, version: str, notes: str, url: str, mandatory: bool, email: str,
               publish: bool = False, allow_older: bool = False) -> dict:
        v = parse_version(version)
        if not v:
            raise ReleaseError("Use a numeric version such as 1.4.2 or 1.4.2.0.")
        notes = str(notes or "").strip()
        if not notes:
            raise ReleaseError("Write a few words about what's new in this version.")
        if len(notes) > 8000:
            raise ReleaseError("Release notes must be 8,000 characters or fewer.")
        url = str(url or "").strip()
        bad = check_url(url, required=publish)
        if bad:
            raise ReleaseError(bad)
        if any(True for _ in self.db.collection("releases").where("version", "==", v["version"]).stream()):
            raise ReleaseError(f"Version {v['version']} already exists. Edit that release or choose a new version.")
        cur = self.current()
        if publish and cur and v["versionCode"] <= int(cur.get("versionCode") or 0) and not allow_older:
            raise ReleaseError(f"The live version is {cur.get('version')}. Publishing {v['version']} would offer an "
                               "older or equal version — tick “publish anyway” to do it on purpose.")
        rid = new_id()
        now = self._now()
        self._r(rid).set({
            "version": v["version"], "versionCode": v["versionCode"], "releaseNotes": notes,
            "downloadUrl": url, "fileName": file_name_from_url(url) if url else "", "fileSize": 0,
            "storagePath": "", "sha256": "", "mandatory": bool(mandatory), "status": "draft",
            "isCurrent": False, "createdAt": now, "updatedAt": now, "publishedAt": None,
            "updatedBy": str(email or "")[:320]})
        if publish:
            self.publish(rid, email)
        return self.get(rid)

    def edit(self, rid: str, notes: str, url: str, mandatory: bool, email: str) -> dict:
        r = self.get(rid)
        if not r:
            raise ReleaseError("This release no longer exists.")
        cur = self.current()
        live = r.get("isCurrent") is True or cur.get("releaseId") == rid
        notes, url = str(notes or "").strip(), str(url or "").strip()
        if not notes:
            raise ReleaseError("Release notes can't be empty.")
        bad = check_url(url, required=live)
        if bad:
            raise ReleaseError("The live release needs a download URL." if live and not url else bad)
        same_file = url == r.get("downloadUrl")
        upd = {"releaseNotes": notes, "downloadUrl": url, "mandatory": bool(mandatory),
               "fileName": r.get("fileName") if same_file else (file_name_from_url(url) if url else ""),
               "fileSize": int(r.get("fileSize") or 0) if same_file else 0,
               "storagePath": r.get("storagePath", "") if same_file else r.get("storagePath", ""),
               "sha256": r.get("sha256", "") if same_file else "",
               "updatedAt": self._now(), "updatedBy": str(email or "")[:320]}
        b = self.db.batch()
        b.update(self._r(rid), upd)
        if live:
            merged = dict(r, **upd)
            b.set(self._cur(), self._current_payload(merged, rid, cur.get("publishedAt") or self._now()))
        b.commit()
        return self.get(rid)

    def publish(self, rid: str, email: str) -> dict:
        r = self.get(rid)
        if not r:
            raise ReleaseError("This release no longer exists.")
        if not str(r.get("downloadUrl") or "").strip():
            raise ReleaseError("Add a download URL before publishing.")
        cur = self.current()
        now = self._now()
        b = self.db.batch()
        old = str(cur.get("releaseId") or "")
        if old and old != rid and self._r(old).get().exists:
            b.update(self._r(old), {"isCurrent": False, "updatedAt": now, "updatedBy": email})
        b.update(self._r(rid), {"status": "published", "isCurrent": True, "publishedAt": now,
                                "updatedAt": now, "updatedBy": email})
        b.set(self._cur(), self._current_payload(r, rid, now))
        b.commit()
        return self.get(rid)

    def delete(self, rid: str) -> str:
        """Remove a release record (and meta/current if it was live). An installer
        in Firebase Storage is left there; returns a note about it."""
        r = self.get(rid)
        if not r:
            raise ReleaseError("This release no longer exists.")
        b = self.db.batch()
        b.delete(self._r(rid))
        if self.current().get("releaseId") == rid:
            b.delete(self._cur())
        b.commit()
        return ("The release record was removed. Its installer file is still in Firebase Storage "
                f"({r['storagePath']}) and can be deleted from the Firebase console.") if r.get("storagePath") else ""

    def add_guide(self, title: str, body: str, email: str) -> dict:
        title, body = str(title or "").strip(), str(body or "").strip()
        if not title or not body:
            raise ReleaseError("A guide needs a title and some text.")
        if len(title) > 200 or len(body) > 20000:
            raise ReleaseError("That guide is too long.")
        gid = new_id()
        now = self._now()
        self.db.collection("docs").document(gid).set({"title": title, "body": body, "createdAt": now,
                                                      "updatedAt": now, "updatedBy": str(email or "")[:320]})
        return {"id": gid, "title": title}

    def delete_guide(self, gid: str) -> None:
        self.db.collection("docs").document(gid).delete()

    def log_download(self, cur: dict) -> None:
        """The same public download record the earlier site wrote."""
        self.db.collection("activity").add({"kind": "download", "version": str(cur.get("version") or "")[:32],
                                            "releaseId": str(cur.get("releaseId") or "")[:20], "detail": "",
                                            "createdAt": self._now()})
