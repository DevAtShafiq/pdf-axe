"""
office.py — Offices (shared workspaces) and the office shared drive.

An office is a team of users (roles: owner, admin, staff) sharing one file
tree, organised as

    depth 1  Country
    depth 2  Program
    depth 3  Student
    depth 4+ the student's sub-folders and files

A user belongs to at most one office. The monthly plan belongs to the office
(see the billing routes in app.py); members get the paid features while the
office plan is active.

Office settings (JSON on the office row):
    countries           ["India", "Nepal", …]           suggested Country names
    programs            ["BSc", …]  or  {"India": ["BSc", …], "*": [global …]}
    student_subfolders  ["Passport", "Offer letter/Scans", …] created in every new student
    enforce_hierarchy   true  → only owner/admin create/rename/move/trash Country and
                                Program folders, and files live inside a student (depth ≥ 4)

Every change is written to the office activity log and broadcast to every
connected member over /events as `shared_changed {office_id, paths, actor, action}`;
membership / settings / plan changes broadcast `office_changed {office_id, reason}`.

Trashing marks rows (restorable by any member). Purging the trash (owner/admin)
removes the rows and their content blobs from the server's own storage.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .db import escape_like
from .paths import clean_name, clean_remote_path, optional_path

ROLES = ("owner", "admin", "staff")
INVITE_ROLES = ("admin", "staff")
MANAGERS = ("owner", "admin")
LEVELS = {1: "country", 2: "program", 3: "student"}
STUDENT_DEPTH = 3
FILE_MIN_DEPTH = 4
DEFAULT_SETTINGS = {"countries": [], "programs": [], "student_subfolders": [],
                    "enforce_hierarchy": True}

LEVEL_MSG = ("Only the office owner or an admin can add, rename, move or remove "
             "Country and Program folders")
ASK_ADMIN_MSG = "Ask an admin to add the country/program first"
FILE_DEPTH_MSG = "Files go inside a student folder (Country › Program › Student)"
NOT_IN_OFFICE = "You are not in an office yet"
NEED_PLAN = "An active subscription is required"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L


# ── small helpers ────────────────────────────────────────────────────────────

def new_invite_code() -> str:
    pick = lambda n: "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))  # noqa: E731
    return f"OFX-{pick(4)}-{pick(4)}"


def normalise_code(code: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", str(code or "")).upper()
    if s.startswith("OFX"):
        s = s[3:]
    if len(s) != 8:
        return ""
    return f"OFX-{s[:4]}-{s[4:]}"


def level_for(depth: int, is_dir: bool = True):
    return LEVELS.get(int(depth)) if is_dir else None


def parent_of(path: str) -> str:
    return path.rpartition("/")[0]


def join(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def depth_of(path: str) -> int:
    return path.count("/") + 1 if path else 0


def load_settings(raw) -> dict:
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
    try:
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    except ValueError:
        pass
    return out


def _name_list(val, what: str, nested: bool = False) -> list:
    if val is None:
        return []
    if not isinstance(val, list):
        raise HTTPException(400, f"{what} must be a list of names")
    if len(val) > 1000:
        raise HTTPException(400, f"{what}: too many entries (1000 at most)")
    out, seen = [], set()
    for v in val:
        if not isinstance(v, str):
            raise HTTPException(400, f"{what} must be a list of names")
        v = v.strip()
        if not v:
            continue
        try:
            n = clean_remote_path(v) if nested else clean_name(v)
        except HTTPException as exc:
            raise HTTPException(400, f'{what}: "{v}": {exc.detail}')
        if nested and n.count("/") > 4:
            raise HTTPException(400, f'{what}: "{v}" is nested too deeply')
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def validate_settings(patch: dict, current: dict) -> dict:
    if not isinstance(patch, dict):
        raise HTTPException(400, "Settings must be an object")
    out = dict(current)
    if "countries" in patch:
        out["countries"] = _name_list(patch["countries"], "Countries")
    if "programs" in patch:
        p = patch["programs"]
        if isinstance(p, dict):
            progs = {}
            for k, v in p.items():
                key = "*" if str(k).strip() in ("*", "") else clean_name(str(k))
                progs[key] = _name_list(v, f"Programs for {key}")
            out["programs"] = progs
        else:
            out["programs"] = _name_list(p, "Programs")
    if "student_subfolders" in patch:
        out["student_subfolders"] = _name_list(patch["student_subfolders"], "Student sub-folders",
                                               nested=True)
    if "enforce_hierarchy" in patch:
        if not isinstance(patch["enforce_hierarchy"], bool):
            raise HTTPException(400, "enforce_hierarchy must be true or false")
        out["enforce_hierarchy"] = patch["enforce_hierarchy"]
    return out


def office_name(name) -> str:
    s = " ".join(str(name or "").split())
    if not s:
        raise HTTPException(400, "Enter an office name")
    if len(s) > 100:
        raise HTTPException(400, "Office name is too long (100 characters at most)")
    if any(ord(c) < 32 for c in s):
        raise HTTPException(400, "Office name contains invalid characters")
    return s


def _numbered(path: str, is_dir: bool, label: str, n: int) -> str:
    parent, name = parent_of(path), path.rpartition("/")[2]
    stem, ext = (name, "") if is_dir else os.path.splitext(name)
    tag = f" ({label})" if label and n == 1 else (f" ({label} {n})" if label else f" ({n})")
    return join(parent, f"{stem}{tag}{ext}")


# ── request bodies ───────────────────────────────────────────────────────────

class NameBody(BaseModel):
    name: str


class CodeBody(BaseModel):
    code: str


class InviteBody(BaseModel):
    email: str = ""
    role: str = "staff"


class RoleBody(BaseModel):
    role: str


class UserBody(BaseModel):
    user_id: int


class SettingsBody(BaseModel):
    settings: dict


class MkdirBody(BaseModel):
    parent: str = ""
    name: str


class PathBody(BaseModel):
    path: str


class StudentBody(BaseModel):
    country: str
    program: str
    student: str


class RenameBody(BaseModel):
    path: str
    new_name: str


class MoveBody(BaseModel):
    paths: list[str]
    dest: str = ""


class PathsBody(BaseModel):
    paths: list[str]


class IdsBody(BaseModel):
    ids: list[int]


# ── routes ───────────────────────────────────────────────────────────────────

def install(app, *, settings, db, hub, limiter, billing, current_user, is_active,
            row_active, grace_until, sub_json) -> None:
    """Add the /office and /shared routes to app."""
    office_quota = settings.office_quota_mb * 1024 * 1024
    max_upload_bytes = settings.max_upload_mb * 1024 * 1024
    invite_ttl = settings.invite_days * 86400

    # ── dependencies ─────────────────────────────────────────────────────────

    def membership(user=Depends(current_user)):
        office = db.office_of_user(user["id"])
        if office is None:
            raise HTTPException(404, NOT_IN_OFFICE)
        return user, office

    def manager(m=Depends(membership)):
        if m[1]["member_role"] not in MANAGERS:
            raise HTTPException(403, "Only the office owner or an admin can do this")
        return m

    def drive(m=Depends(membership)):
        user, office = m
        if not is_active(user, office):
            raise HTTPException(402, NEED_PLAN)
        return user, office

    # ── shared helpers ───────────────────────────────────────────────────────

    def office_dir(oid: int) -> str:
        d = os.path.join(settings.storage_dir, f"office_{int(oid)}")
        os.makedirs(d, exist_ok=True)
        return d

    def publish_office(oid: int, event: str, data: dict, also=()) -> None:
        for uid in set(db.office_member_ids(oid)) | {int(u) for u in also if u}:
            hub.publish(uid, event, data)

    def office_changed(oid: int, reason: str, also=()) -> None:
        publish_office(oid, "office_changed", {"office_id": oid, "reason": reason}, also)

    def shared_changed(oid: int, user, action: str, paths: list) -> None:
        publish_office(oid, "shared_changed", {"office_id": oid, "paths": list(paths)[:200],
                                               "actor": user["email"], "action": action})

    def log(con, oid: int, user, action: str, path: str = "", detail: str = "") -> None:
        con.execute(
            "INSERT INTO office_activity(office_id, ts, user_id, email, action, path, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (oid, time.time(), user["id"] if user is not None else None,
             user["email"] if user is not None else "", action, path or "", str(detail or "")[:500]),
        )
        con.execute(
            "DELETE FROM office_activity WHERE office_id=? AND id <= (SELECT id FROM office_activity "
            "WHERE office_id=? ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (oid, oid, max(10, int(settings.office_activity_keep))),
        )

    def plan_json(office) -> dict:
        return {
            "active": bool(settings.free_plan or row_active(office)),
            "status": office["subscription_status"],
            "plan_label": settings.plan_label,
            "billing_available": billing.name != "none",
            "free_plan": bool(settings.free_plan),
            "current_period_end": office["current_period_end"],
            "grace_until": grace_until(office),
        }

    def office_json(office, role: str) -> dict:
        with db.connect() as con:
            n = con.execute("SELECT COUNT(*) AS n FROM office_members WHERE office_id=?",
                            (office["id"],)).fetchone()["n"]
        return {"id": office["id"], "name": office["name"], "role": role,
                "member_count": int(n), "owner_id": office["owner_id"],
                "created_at": office["created_at"], "plan": plan_json(office),
                "settings": load_settings(office["settings"])}

    def current_office_json(user):
        o = db.office_of_user(user["id"])
        return office_json(o, o["member_role"]) if o else None

    def invite_json(r) -> dict:
        keys = r.keys()
        return {"id": r["id"], "code": r["code"], "email": r["email"], "role": r["role"],
                "expires_at": r["expires_at"], "created_at": r["created_at"],
                "status": r["status"],
                "invited_by_email": r["invited_by_email"] if "invited_by_email" in keys else ""}

    def restricted(office) -> bool:
        return (office["member_role"] == "staff"
                and bool(load_settings(office["settings"])["enforce_hierarchy"]))

    def enforced(office) -> bool:
        return bool(load_settings(office["settings"])["enforce_hierarchy"])

    def get_live(con, oid: int, path: str):
        return con.execute(
            "SELECT * FROM shared_items WHERE office_id=? AND path_key=? AND trashed=0",
            (oid, path.lower())).fetchone()

    def insert_item(con, oid: int, path: str, is_dir: bool, user,
                    blob: str = "", size: int = 0, sha: str = "") -> int:
        now = time.time()
        cur = con.execute(
            "INSERT INTO shared_items(office_id, path, path_key, parent_key, name, depth, is_dir, "
            "blob, size, sha256, created_at, created_by, updated_at, updated_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, path, path.lower(), parent_of(path).lower(), path.rpartition("/")[2],
             depth_of(path), 1 if is_dir else 0, blob, int(size), sha, now, user["email"],
             now, user["email"]))
        return int(cur.lastrowid)

    def item_json(r, child_count: int = 0) -> dict:
        return {"id": r["id"], "name": r["name"], "path": r["path"], "is_dir": bool(r["is_dir"]),
                "size": r["size"], "sha256": r["sha256"], "updated_at": r["updated_at"],
                "updated_by": r["updated_by"], "created_at": r["created_at"],
                "created_by": r["created_by"], "depth": r["depth"],
                "level": level_for(r["depth"], bool(r["is_dir"])), "child_count": int(child_count)}

    def child_counts(con, oid: int, rows) -> dict:
        keys = [r["path_key"] for r in rows if r["is_dir"]]
        out: dict = {}
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            for r in con.execute(
                    f"SELECT parent_key, COUNT(*) AS n FROM shared_items WHERE office_id=? AND trashed=0 "
                    f"AND parent_key IN ({','.join('?' * len(chunk))}) GROUP BY parent_key",
                    (oid, *chunk)):
                out[r["parent_key"]] = int(r["n"])
        return out

    def items_json(con, oid: int, rows) -> list:
        counts = child_counts(con, oid, rows)
        return [item_json(r, counts.get(r["path_key"], 0)) for r in rows]

    def used_bytes(con, oid: int) -> int:
        return int(con.execute(
            "SELECT COALESCE(SUM(size),0) AS n FROM shared_items WHERE office_id=? AND trashed=0 "
            "AND is_dir=0", (oid,)).fetchone()["n"])

    def subtree(con, oid: int, row, trashed: int = 0):
        """Live (or trashed) rows strictly below a folder row."""
        return con.execute(
            "SELECT * FROM shared_items WHERE office_id=? AND trashed=? AND path_key LIKE ? ESCAPE '\\' "
            "ORDER BY depth, path_key",
            (oid, trashed, escape_like(row["path_key"]) + "/%")).fetchall()

    def ensure_dirs(con, office, user, path: str, created: list, force: bool = False) -> str:
        """Make sure every folder along `path` (itself included) exists.
        Returns the path spelled as stored (existing folders keep their case)."""
        if not path:
            return ""
        oid = office["id"]
        limited = restricted(office) and not force
        canon = ""
        for i, part in enumerate(path.split("/"), start=1):
            p = join(canon, part)
            row = get_live(con, oid, p)
            if row is not None:
                if not row["is_dir"]:
                    raise HTTPException(409, f'"{row["path"]}" is a file, not a folder')
                canon = row["path"]
                continue
            if limited and i <= 2:
                raise HTTPException(403, LEVEL_MSG)
            insert_item(con, oid, p, True, user)
            created.append(p)
            canon = p
        return canon

    def add_student_subfolders(con, office, user, student_path: str, created: list) -> None:
        for sub in load_settings(office["settings"])["student_subfolders"]:
            ensure_dirs(con, office, user, join(student_path, sub), created, force=True)

    def move_subtree(con, oid: int, row, new_path: str, user) -> None:
        old = row["path"]
        desc = subtree(con, oid, row) if row["is_dir"] else []
        now = time.time()
        con.execute(
            "UPDATE shared_items SET path=?, path_key=?, parent_key=?, name=?, depth=?, "
            "updated_at=?, updated_by=? WHERE id=?",
            (new_path, new_path.lower(), parent_of(new_path).lower(), new_path.rpartition("/")[2],
             depth_of(new_path), now, user["email"], row["id"]))
        for d in desc:
            np = new_path + d["path"][len(old):]
            con.execute("UPDATE shared_items SET path=?, path_key=?, parent_key=?, depth=? WHERE id=?",
                        (np, np.lower(), parent_of(np).lower(), depth_of(np), d["id"]))

    def free_name(con, oid: int, path: str, is_dir: bool, label: str = "") -> str:
        n = 1 if label else 2
        cand = _numbered(path, is_dir, label, n)
        while get_live(con, oid, cand) is not None:
            n += 1
            cand = _numbered(path, is_dir, label, n)
        return cand

    def many(paths, fn) -> tuple[list, list]:
        """Run fn(path) per path; collect results and per-path errors.
        Raises the first error when nothing succeeded."""
        done, errors = [], []
        for p in list(paths or [])[:2000]:
            try:
                done.append(fn(p))
            except HTTPException as exc:
                errors.append({"path": p, "error": exc.detail, "status": exc.status_code})
        if errors and not done:
            raise HTTPException(errors[0]["status"], errors[0]["error"])
        return done, errors

    # ── office lifecycle ─────────────────────────────────────────────────────

    def pending_invites(user, office=None) -> list:
        with db.connect() as con:
            rows = con.execute(
                "SELECT i.id, i.role, i.expires_at, i.office_id, o.name AS office_name, "
                "u.email AS invited_by_email FROM office_invites i "
                "JOIN offices o ON o.id=i.office_id LEFT JOIN users u ON u.id=i.invited_by "
                "WHERE i.email=? AND i.status='pending' AND i.expires_at>? AND o.archived_at=0 "
                "ORDER BY i.id DESC", (user["email"], time.time())).fetchall()
        return [{"id": r["id"], "office_name": r["office_name"], "role": r["role"],
                 "invited_by_email": r["invited_by_email"] or "", "expires_at": r["expires_at"]}
                for r in rows if not office or r["office_id"] != office["id"]]

    @app.get("/office")
    def office_state(user=Depends(current_user)):
        office = db.office_of_user(user["id"])
        return {"office": office_json(office, office["member_role"]) if office else None,
                "pending_invites": pending_invites(user, office)}

    @app.post("/office")
    def office_create(body: NameBody, user=Depends(current_user)):
        name = office_name(body.name)
        now = time.time()
        with db.connect() as con:
            if con.execute("SELECT 1 FROM office_members WHERE user_id=?", (user["id"],)).fetchone():
                raise HTTPException(409, "You are already in an office. Leave it first.")
            cur = con.execute("INSERT INTO offices(name, owner_id, created_at, settings) VALUES (?,?,?,?)",
                              (name, user["id"], now, json.dumps(DEFAULT_SETTINGS)))
            oid = int(cur.lastrowid)
            con.execute("INSERT INTO office_members(user_id, office_id, role, joined_at) VALUES (?,?,?,?)",
                        (user["id"], oid, "owner", now))
            log(con, oid, user, "office_created", "", name)
        office_changed(oid, "created")
        hub.publish(user["id"], "subscription_updated", sub_json(db.user_by_id(user["id"])))
        return {"office": current_office_json(user)}

    @app.post("/office/rename")
    def office_rename(body: NameBody, m=Depends(manager)):
        user, office = m
        name = office_name(body.name)
        with db.connect() as con:
            con.execute("UPDATE offices SET name=? WHERE id=?", (name, office["id"]))
            log(con, office["id"], user, "office_renamed", "", name)
        office_changed(office["id"], "renamed")
        return {"office": current_office_json(user)}

    def _join(user, invite_where: str, args: tuple, not_found: str):
        now = time.time()
        with db.connect() as con:
            if con.execute("SELECT 1 FROM office_members WHERE user_id=?", (user["id"],)).fetchone():
                raise HTTPException(409, "You are already in an office. Leave it first.")
            inv = con.execute(
                "SELECT i.* FROM office_invites i JOIN offices o ON o.id=i.office_id "
                f"WHERE {invite_where} AND i.status='pending' AND i.expires_at>? AND o.archived_at=0",
                (*args, now)).fetchone()
            if inv is None:
                raise HTTPException(404, not_found)
            con.execute("INSERT INTO office_members(user_id, office_id, role, joined_at) VALUES (?,?,?,?)",
                        (user["id"], inv["office_id"], inv["role"], now))
            con.execute("UPDATE office_invites SET status='accepted', used_by=?, used_at=? WHERE id=?",
                        (user["id"], now, inv["id"]))
            log(con, inv["office_id"], user, "member_joined", "", inv["role"])
        office_changed(inv["office_id"], "member_joined")
        hub.publish(user["id"], "subscription_updated", sub_json(db.user_by_id(user["id"])))
        return {"office": current_office_json(user)}

    @app.post("/office/join")
    def office_join(body: CodeBody, user=Depends(current_user)):
        key = f"office-join:{user['id']}"
        wait = limiter.retry_after(key, 10, 900)
        if wait:
            raise HTTPException(429, "Too many wrong invite codes. Try again in a few minutes.",
                                headers={"Retry-After": str(int(wait))})
        code = normalise_code(body.code)
        bad = "That invite code is not valid or has expired"
        if not code:
            limiter.hit(key, 900)
            raise HTTPException(404, bad)
        try:
            return _join(user, "i.code=?", (code,), bad)
        except HTTPException as exc:
            if exc.status_code == 404:
                limiter.hit(key, 900)
            raise

    @app.post("/office/invites/{invite_id}/accept")
    def office_accept(invite_id: int, user=Depends(current_user)):
        return _join(user, "i.id=? AND i.email=?", (invite_id, user["email"]),
                     "That invitation is no longer available")

    @app.post("/office/invites/{invite_id}/decline")
    def office_decline(invite_id: int, user=Depends(current_user)):
        with db.connect() as con:
            inv = con.execute("SELECT * FROM office_invites WHERE id=? AND email=? AND status='pending'",
                              (invite_id, user["email"])).fetchone()
            if inv is None:
                raise HTTPException(404, "That invitation is no longer available")
            con.execute("UPDATE office_invites SET status='declined', used_by=?, used_at=? WHERE id=?",
                        (user["id"], time.time(), invite_id))
        office_changed(inv["office_id"], "invite_declined", also=[user["id"]])
        return {"ok": True}

    @app.post("/office/leave")
    def office_leave(m=Depends(membership)):
        user, office = m
        oid = office["id"]
        with db.connect() as con:
            others = con.execute("SELECT COUNT(*) AS n FROM office_members WHERE office_id=? AND user_id<>?",
                                 (oid, user["id"])).fetchone()["n"]
            if office["member_role"] == "owner" and others:
                raise HTTPException(409, "The owner can't leave while other members are in the office. "
                                         "Transfer ownership to another member first.")
            con.execute("DELETE FROM office_members WHERE user_id=?", (user["id"],))
            if not others:   # last one out: the office (and its files) is kept, but archived
                con.execute("UPDATE offices SET archived_at=? WHERE id=?", (time.time(), oid))
                con.execute("UPDATE office_invites SET status='revoked' WHERE office_id=? AND status='pending'",
                            (oid,))
            log(con, oid, user, "member_left")
        office_changed(oid, "member_left", also=[user["id"]])
        hub.publish(user["id"], "subscription_updated", sub_json(db.user_by_id(user["id"])))
        return {"ok": True}

    @app.post("/office/transfer")
    def office_transfer(body: UserBody, m=Depends(membership)):
        user, office = m
        if office["member_role"] != "owner":
            raise HTTPException(403, "Only the office owner can transfer ownership")
        if int(body.user_id) == int(user["id"]):
            raise HTTPException(400, "You already own this office")
        with db.connect() as con:
            t = con.execute("SELECT m.*, u.email FROM office_members m JOIN users u ON u.id=m.user_id "
                            "WHERE m.user_id=? AND m.office_id=?", (body.user_id, office["id"])).fetchone()
            if t is None:
                raise HTTPException(404, "That person is not a member of this office")
            con.execute("UPDATE office_members SET role='owner' WHERE user_id=?", (body.user_id,))
            con.execute("UPDATE office_members SET role='admin' WHERE user_id=?", (user["id"],))
            con.execute("UPDATE offices SET owner_id=? WHERE id=?", (body.user_id, office["id"]))
            log(con, office["id"], user, "ownership_transferred", "", t["email"])
        office_changed(office["id"], "owner_changed")
        return {"office": current_office_json(user)}

    @app.get("/office/members")
    def office_members(m=Depends(membership)):
        user, office = m
        with db.connect() as con:
            rows = con.execute(
                "SELECT m.user_id, m.role, m.joined_at, u.email, "
                "(SELECT MAX(s.last_seen) FROM sessions s WHERE s.user_id=m.user_id) AS last_active "
                "FROM office_members m JOIN users u ON u.id=m.user_id WHERE m.office_id=? "
                "ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.email",
                (office["id"],)).fetchall()
        return {"members": [{"user_id": r["user_id"], "email": r["email"], "role": r["role"],
                             "joined_at": r["joined_at"], "last_active": float(r["last_active"] or 0),
                             "is_you": int(r["user_id"]) == int(user["id"])} for r in rows]}

    @app.post("/office/invites")
    def office_invite(body: InviteBody, m=Depends(manager)):
        user, office = m
        email = str(body.email or "").strip().lower()
        role = str(body.role or "staff").strip().lower()
        if role not in INVITE_ROLES:
            raise HTTPException(400, "Role must be admin or staff")
        if email and (len(email) > 254 or not _EMAIL_RE.match(email)):
            raise HTTPException(400, "Enter a valid email address")
        now = time.time()
        invitee = db.user_by_email(email) if email else None
        with db.connect() as con:
            if invitee is not None:
                mem = con.execute("SELECT office_id FROM office_members WHERE user_id=?",
                                  (invitee["id"],)).fetchone()
                if mem is not None and mem["office_id"] == office["id"]:
                    raise HTTPException(409, f"{email} is already a member of this office")
            if email:   # one live invite per address: a new one replaces the old
                con.execute("UPDATE office_invites SET status='revoked' WHERE office_id=? AND email=? "
                            "AND status='pending'", (office["id"], email))
            for _ in range(20):
                code = new_invite_code()
                try:
                    cur = con.execute(
                        "INSERT INTO office_invites(office_id, code, email, role, invited_by, created_at, "
                        "expires_at) VALUES (?,?,?,?,?,?,?)",
                        (office["id"], code, email, role, user["id"], now, now + invite_ttl))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise HTTPException(500, "Could not create an invite code")
            inv = con.execute("SELECT i.*, u.email AS invited_by_email FROM office_invites i "
                              "LEFT JOIN users u ON u.id=i.invited_by WHERE i.id=?",
                              (cur.lastrowid,)).fetchone()
            log(con, office["id"], user, "member_invited", "", f"{email or 'code only'} as {role}")
        office_changed(office["id"], "invite_created", also=[invitee["id"]] if invitee is not None else [])
        return {"invite": invite_json(inv)}

    @app.get("/office/invites")
    def office_invites(m=Depends(manager)):
        _user, office = m
        with db.connect() as con:
            rows = con.execute("SELECT i.*, u.email AS invited_by_email FROM office_invites i "
                               "LEFT JOIN users u ON u.id=i.invited_by WHERE i.office_id=? "
                               "AND i.status='pending' AND i.expires_at>? ORDER BY i.id DESC",
                               (office["id"], time.time())).fetchall()
        return {"invites": [invite_json(r) for r in rows]}

    @app.post("/office/invites/{invite_id}/revoke")
    def office_revoke(invite_id: int, m=Depends(manager)):
        user, office = m
        with db.connect() as con:
            inv = con.execute("SELECT * FROM office_invites WHERE id=? AND office_id=? AND status='pending'",
                              (invite_id, office["id"])).fetchone()
            if inv is None:
                raise HTTPException(404, "Invite not found")
            con.execute("UPDATE office_invites SET status='revoked' WHERE id=?", (invite_id,))
            log(con, office["id"], user, "invite_revoked", "", inv["email"] or inv["code"])
        invitee = db.user_by_email(inv["email"]) if inv["email"] else None
        office_changed(office["id"], "invite_revoked", also=[invitee["id"]] if invitee is not None else [])
        return {"ok": True}

    def _target(con, office, user_id: int):
        t = con.execute("SELECT m.*, u.email FROM office_members m JOIN users u ON u.id=m.user_id "
                        "WHERE m.user_id=? AND m.office_id=?", (user_id, office["id"])).fetchone()
        if t is None:
            raise HTTPException(404, "That person is not a member of this office")
        return t

    @app.post("/office/members/{user_id}/role")
    def office_set_role(user_id: int, body: RoleBody, m=Depends(manager)):
        user, office = m
        role = str(body.role or "").strip().lower()
        if role not in INVITE_ROLES:
            raise HTTPException(400, "Role must be admin or staff (use transfer to change the owner)")
        with db.connect() as con:
            t = _target(con, office, user_id)
            if t["role"] == "owner":
                raise HTTPException(400, "The owner's role can't be changed. Transfer ownership instead.")
            if t["role"] != role:
                con.execute("UPDATE office_members SET role=? WHERE user_id=?", (role, user_id))
                log(con, office["id"], user, "role_changed", "", f"{t['email']} → {role}")
        office_changed(office["id"], "role_changed")
        return {"ok": True}

    @app.post("/office/members/{user_id}/remove")
    def office_remove(user_id: int, m=Depends(manager)):
        user, office = m
        with db.connect() as con:
            t = _target(con, office, user_id)
            if t["role"] == "owner":
                raise HTTPException(403, "The office owner can't be removed")
            if int(user_id) == int(user["id"]):
                raise HTTPException(400, "Use Leave office to remove yourself")
            if t["role"] == "admin" and office["member_role"] != "owner":
                raise HTTPException(403, "Only the owner can remove an admin")
            con.execute("DELETE FROM office_members WHERE user_id=?", (user_id,))
            log(con, office["id"], user, "member_removed", "", t["email"])
        office_changed(office["id"], "member_removed")
        # The removed person is told separately (they are no longer a member)
        hub.publish(user_id, "office_changed", {"office_id": office["id"], "reason": "removed"})
        hub.publish(user_id, "subscription_updated", sub_json(db.user_by_id(user_id)))
        return {"ok": True}

    @app.get("/office/settings")
    def office_settings_get(m=Depends(membership)):
        return {"settings": load_settings(m[1]["settings"])}

    @app.post("/office/settings")
    def office_settings_set(body: SettingsBody, m=Depends(manager)):
        user, office = m
        new = validate_settings(body.settings, load_settings(office["settings"]))
        with db.connect() as con:
            con.execute("UPDATE offices SET settings=? WHERE id=?", (json.dumps(new), office["id"]))
            log(con, office["id"], user, "settings_changed")
        office_changed(office["id"], "settings")
        return {"settings": new}

    # ── shared drive ─────────────────────────────────────────────────────────

    @app.get("/shared/list")
    def shared_list(path: str = "", m=Depends(drive)):
        user, office = m
        oid = office["id"]
        p = optional_path(path)
        with db.connect() as con:
            if p:
                row = get_live(con, oid, p)
                if row is None:
                    raise HTTPException(404, "Folder not found")
                if not row["is_dir"]:
                    raise HTTPException(400, f'"{row["path"]}" is a file, not a folder')
                p = row["path"]
            rows = con.execute(
                "SELECT * FROM shared_items WHERE office_id=? AND parent_key=? AND trashed=0 "
                "ORDER BY is_dir DESC, name COLLATE NOCASE", (oid, p.lower())).fetchall()
            items = items_json(con, oid, rows)
        parts = p.split("/") if p else []
        crumbs = [{"name": parts[i], "path": "/".join(parts[:i + 1]), "level": level_for(i + 1)}
                  for i in range(len(parts))]
        d = depth_of(p)
        return {"path": p, "level": level_for(d) if p else None, "items": items, "breadcrumb": crumbs,
                "child_level": LEVELS.get(d + 1),
                "can_create_folder": not (restricted(office) and d + 1 <= 2),
                "can_upload": not enforced(office) or d >= STUDENT_DEPTH}

    @app.get("/shared/tree")
    def shared_tree(path: str = "", m=Depends(drive)):
        """Every live item at and below path ('' = the whole drive), for downloads."""
        _user, office = m
        oid = office["id"]
        p = optional_path(path)
        with db.connect() as con:
            if not p:
                rows = con.execute("SELECT * FROM shared_items WHERE office_id=? AND trashed=0 "
                                   "ORDER BY depth, path_key", (oid,)).fetchall()
                return {"items": [item_json(r) for r in rows]}
            row = get_live(con, oid, p)
            if row is None:
                raise HTTPException(404, "Not found")
            rows = [row] + (subtree(con, oid, row) if row["is_dir"] else [])
            return {"items": [item_json(r) for r in rows]}

    @app.post("/shared/mkdir")
    def shared_mkdir(body: MkdirBody, m=Depends(drive)):
        user, office = m
        oid = office["id"]
        parent = optional_path(body.parent)
        name = clean_name(body.name)
        created: list = []
        with db.connect() as con:
            if parent:
                prow = get_live(con, oid, parent)
                if prow is None or not prow["is_dir"]:
                    raise HTTPException(404, "Folder not found")
                parent = prow["path"]
            path = join(parent, name)
            d = depth_of(path)
            if restricted(office) and d <= 2:
                raise HTTPException(403, LEVEL_MSG)
            if get_live(con, oid, path) is not None:
                raise HTTPException(409, f'"{name}" already exists here')
            insert_item(con, oid, path, True, user)
            created.append(path)
            if d == STUDENT_DEPTH:
                add_student_subfolders(con, office, user, path, created)
            log(con, oid, user, "create_folder", path, level_for(d) or "")
        shared_changed(oid, user, "create_folder", created)
        return {"path": path, "created": created}

    @app.post("/shared/ensure-dir")
    def shared_ensure_dir(body: PathBody, m=Depends(drive)):
        """Create a folder and any missing parents (used when uploading folder trees)."""
        user, office = m
        oid = office["id"]
        path = clean_remote_path(body.path)
        created: list = []
        with db.connect() as con:
            canon = ensure_dirs(con, office, user, path, created)
            for c in created:
                log(con, oid, user, "create_folder", c, level_for(depth_of(c)) or "")
        if created:
            shared_changed(oid, user, "create_folder", created)
        return {"path": canon, "created": created}

    @app.post("/shared/new-student")
    def shared_new_student(body: StudentBody, m=Depends(drive)):
        user, office = m
        oid = office["id"]
        country, program, student = clean_name(body.country), clean_name(body.program), clean_name(body.student)
        created: list = []
        with db.connect() as con:
            canon = ""
            for name in (country, program):
                p = join(canon, name)
                row = get_live(con, oid, p)
                if row is not None:
                    if not row["is_dir"]:
                        raise HTTPException(409, f'"{row["path"]}" is a file, not a folder')
                    canon = row["path"]
                    continue
                if restricted(office):
                    raise HTTPException(403, ASK_ADMIN_MSG)
                insert_item(con, oid, p, True, user)
                created.append(p)
                canon = p
            path = join(canon, student)
            row = get_live(con, oid, path)
            existed = row is not None
            if existed:
                if not row["is_dir"]:
                    raise HTTPException(409, f'"{row["path"]}" is a file, not a folder')
                path = row["path"]
            else:
                insert_item(con, oid, path, True, user)
                created.append(path)
                add_student_subfolders(con, office, user, path, created)
                log(con, oid, user, "new_student", path)
        if created:
            shared_changed(oid, user, "new_student", created)
        return {"path": path, "created": created, "existed": existed}

    @app.post("/shared/upload")
    def shared_upload(path: str = Form(...), file: UploadFile = File(...), base_sha256: str = Form(default=""),
                      on_conflict: str = Form(default="replace"), m=Depends(drive)):
        """Upload one file. on_conflict: replace (default; 409 if base_sha256 is given and the
        stored file changed since), rename (keep both: "name (2).ext"), or error (409)."""
        user, office = m
        oid = office["id"]
        rel = clean_remote_path(path)
        if on_conflict not in ("replace", "rename", "error"):
            raise HTTPException(400, "on_conflict must be replace, rename or error")
        if enforced(office) and depth_of(rel) < FILE_MIN_DEPTH:
            raise HTTPException(400, FILE_DEPTH_MSG)
        with db.connect() as con:   # early checks before receiving the body
            existing = get_live(con, oid, rel)
            if existing is not None and existing["is_dir"]:
                raise HTTPException(409, f'A folder named "{existing["name"]}" already exists here')
            if existing is not None and on_conflict == "error":
                raise HTTPException(409, f'"{existing["name"]}" already exists here')
            if (existing is not None and on_conflict == "replace" and base_sha256
                    and existing["sha256"] != base_sha256):
                raise HTTPException(409, "The file changed on the shared drive since you opened it")
            room = office_quota - used_bytes(con, oid)
            if existing is not None and on_conflict == "replace":
                room += existing["size"]
        odir = office_dir(oid)
        h = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(dir=odir, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_upload_bytes:
                        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
                    if size > room:
                        raise HTTPException(507, "The office's shared storage is full")
                    h.update(chunk)
                    out.write(chunk)
            sha = h.hexdigest()
            blob = uuid.uuid4().hex
            created: list = []
            with db.connect() as con:
                parent = ensure_dirs(con, office, user, parent_of(rel), created)
                final = join(parent, rel.rpartition("/")[2])
                existing = get_live(con, oid, final)
                replaced = 0
                if existing is not None:
                    if existing["is_dir"]:
                        raise HTTPException(409, f'A folder named "{existing["name"]}" already exists here')
                    if on_conflict == "error":
                        raise HTTPException(409, f'"{existing["name"]}" already exists here')
                    if on_conflict == "rename":
                        final = free_name(con, oid, final, False)
                        existing = None
                    elif base_sha256 and existing["sha256"] != base_sha256:
                        raise HTTPException(409, "The file changed on the shared drive since you opened it")
                    else:
                        replaced = int(existing["size"])
                if used_bytes(con, oid) - replaced + size > office_quota:
                    raise HTTPException(507, "The office's shared storage is full")
                os.replace(tmp, os.path.join(odir, blob))
                if existing is not None:   # the old blob stays on disk
                    con.execute("UPDATE shared_items SET blob=?, size=?, sha256=?, updated_at=?, updated_by=? "
                                "WHERE id=?", (blob, size, sha, time.time(), user["email"], existing["id"]))
                    fid = int(existing["id"])
                else:
                    fid = insert_item(con, oid, final, False, user, blob, size, sha)
                for c in created:
                    log(con, oid, user, "create_folder", c, level_for(depth_of(c)) or "")
                log(con, oid, user, "replace" if existing is not None else "upload", final, f"{size} bytes")
                row = con.execute("SELECT * FROM shared_items WHERE id=?", (fid,)).fetchone()
        except BaseException:
            if os.path.exists(tmp):   # keep a failed upload aside rather than removing it
                os.replace(tmp, tmp + ".failed")
            raise
        shared_changed(oid, user, "upload", created + [final])
        return {"file": item_json(row), "created": created, "renamed": final != rel}

    @app.get("/shared/download")
    def shared_download(path: str, m=Depends(drive)):
        _user, office = m
        oid = office["id"]
        rel = clean_remote_path(path)
        with db.connect() as con:
            row = get_live(con, oid, rel)
        if row is None or row["is_dir"]:
            raise HTTPException(404, "File not found")
        odir = os.path.realpath(office_dir(oid))
        blob = os.path.realpath(os.path.join(odir, row["blob"]))
        if os.path.dirname(blob) != odir:
            raise HTTPException(404, "File not found")
        if not os.path.isfile(blob):
            raise HTTPException(410, "File content is missing on the server")
        return FileResponse(blob, filename=row["name"], media_type="application/octet-stream",
                            headers={"X-Content-SHA256": row["sha256"]})

    @app.post("/shared/rename")
    def shared_rename(body: RenameBody, m=Depends(drive)):
        user, office = m
        oid = office["id"]
        rel = clean_remote_path(body.path)
        name = clean_name(body.new_name)
        with db.connect() as con:
            row = get_live(con, oid, rel)
            if row is None:
                raise HTTPException(404, "Not found")
            if row["is_dir"] and row["depth"] <= 2 and restricted(office):
                raise HTTPException(403, LEVEL_MSG)
            old = row["path"]
            new = join(parent_of(old), name)
            if new == old:
                return {"path": new}
            clash = get_live(con, oid, new)
            if clash is not None and clash["id"] != row["id"]:
                raise HTTPException(409, f'"{name}" already exists here')
            move_subtree(con, oid, row, new, user)
            log(con, oid, user, "rename", new, f"from {row['name']}")
        shared_changed(oid, user, "rename", [old, new])
        return {"path": new, "old_path": old}

    @app.post("/shared/move")
    def shared_move(body: MoveBody, m=Depends(drive)):
        user, office = m
        oid = office["id"]
        dest = optional_path(body.dest)

        def one(p):
            rel = clean_remote_path(p)
            with db.connect() as con:
                row = get_live(con, oid, rel)
                if row is None:
                    raise HTTPException(404, "Not found")
                dpath = ""
                if dest:
                    drow = get_live(con, oid, dest)
                    if drow is None or not drow["is_dir"]:
                        raise HTTPException(404, "Destination folder not found")
                    dpath = drow["path"]
                if row["is_dir"] and (dpath.lower() == row["path_key"]
                                      or dpath.lower().startswith(row["path_key"] + "/")):
                    raise HTTPException(400, "A folder cannot be moved into itself")
                new = join(dpath, row["name"])
                if new.lower() == row["path_key"]:
                    return {"from": row["path"], "to": row["path"]}
                nd = depth_of(new)
                if restricted(office) and (row["depth"] <= 2 or (row["is_dir"] and nd <= 2)):
                    raise HTTPException(403, LEVEL_MSG)
                if enforced(office):
                    if row["is_dir"]:
                        fmin = con.execute(
                            "SELECT MIN(depth) AS d FROM shared_items WHERE office_id=? AND trashed=0 "
                            "AND is_dir=0 AND path_key LIKE ? ESCAPE '\\'",
                            (oid, escape_like(row["path_key"]) + "/%")).fetchone()["d"]
                        lowest = (int(fmin) - row["depth"] + nd) if fmin is not None else None
                    else:
                        lowest = nd
                    if lowest is not None and lowest < FILE_MIN_DEPTH:
                        raise HTTPException(400, FILE_DEPTH_MSG)
                if get_live(con, oid, new) is not None:
                    raise HTTPException(409, f'"{row["name"]}" already exists in the destination')
                move_subtree(con, oid, row, new, user)
                log(con, oid, user, "move", new, f"from {row['path']}")
            return {"from": row["path"], "to": new}

        moved, errors = many(body.paths, one)
        if moved:
            shared_changed(oid, user, "move", [x for mv in moved for x in (mv["from"], mv["to"])])
        return {"moved": moved, "errors": errors}

    @app.post("/shared/trash")
    def shared_trash(body: PathsBody, m=Depends(drive)):
        user, office = m
        oid = office["id"]

        def one(p):
            rel = clean_remote_path(p)
            with db.connect() as con:
                row = get_live(con, oid, rel)
                if row is None:
                    raise HTTPException(404, "Not found")
                if row["is_dir"] and row["depth"] <= 2 and restricted(office):
                    raise HTTPException(403, LEVEL_MSG)
                con.execute(
                    "UPDATE shared_items SET trashed=1, trash_root=?, deleted_at=?, deleted_by=? "
                    "WHERE office_id=? AND trashed=0 AND (id=? OR path_key LIKE ? ESCAPE '\\')",
                    (row["id"], time.time(), user["email"], oid, row["id"],
                     escape_like(row["path_key"]) + "/%" if row["is_dir"] else "\x00"))
                log(con, oid, user, "trash", row["path"])
            return row["path"]

        done, errors = many(body.paths, one)
        if done:
            shared_changed(oid, user, "trash", done)
        return {"trashed": done, "errors": errors}

    @app.get("/shared/trash")
    def shared_trash_list(m=Depends(drive)):
        _user, office = m
        oid = office["id"]
        with db.connect() as con:
            tops = con.execute("SELECT * FROM shared_items WHERE office_id=? AND trashed=1 AND trash_root=id "
                               "ORDER BY deleted_at DESC LIMIT 2000", (oid,)).fetchall()
            agg = {int(r["trash_root"]): (int(r["n"]), int(r["s"])) for r in con.execute(
                "SELECT trash_root, COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM shared_items "
                "WHERE office_id=? AND trashed=1 GROUP BY trash_root", (oid,))}
        return {"items": [{"id": r["id"], "name": r["name"], "path": r["path"], "is_dir": bool(r["is_dir"]),
                           "level": level_for(r["depth"], bool(r["is_dir"])),
                           "deleted_at": r["deleted_at"], "deleted_by": r["deleted_by"],
                           "count": agg.get(int(r["id"]), (1, 0))[0],
                           "size": agg.get(int(r["id"]), (1, 0))[1]} for r in tops]}

    @app.post("/shared/restore")
    def shared_restore(body: IdsBody, m=Depends(drive)):
        user, office = m
        oid = office["id"]

        def one(tid):
            with db.connect() as con:
                top = con.execute("SELECT * FROM shared_items WHERE office_id=? AND id=? AND trashed=1 "
                                  "AND trash_root=id", (oid, int(tid))).fetchone()
                if top is None:
                    raise HTTPException(404, "Not in the trash")
                rows = con.execute("SELECT * FROM shared_items WHERE office_id=? AND trashed=1 AND trash_root=? "
                                   "ORDER BY depth", (oid, top["id"])).fetchall()
                size = sum(int(r["size"]) for r in rows if not r["is_dir"])
                if used_bytes(con, oid) + size > office_quota:
                    raise HTTPException(507, "The office's shared storage is full")
                created: list = []
                parent = ensure_dirs(con, office, user, parent_of(top["path"]), created, force=True)
                new_top = join(parent, top["name"])
                if get_live(con, oid, new_top) is not None:
                    new_top = free_name(con, oid, new_top, bool(top["is_dir"]), "restored")
                old_top = top["path"]
                for r in rows:
                    np = new_top + r["path"][len(old_top):]
                    con.execute(
                        "UPDATE shared_items SET trashed=0, trash_root=0, deleted_at=0, deleted_by='', "
                        "path=?, path_key=?, parent_key=?, depth=?, name=? WHERE id=?",
                        (np, np.lower(), parent_of(np).lower(), depth_of(np), np.rpartition("/")[2], r["id"]))
                log(con, oid, user, "restore", new_top,
                    f"was {old_top}" if new_top != old_top else "")
            return {"id": int(tid), "path": new_top, "created": created}

        done, errors = many(body.ids, one)
        if done:
            shared_changed(oid, user, "restore", [d["path"] for d in done])
        return {"restored": done, "errors": [{"id": e["path"], "error": e["error"], "status": e["status"]} for e in errors]}

    @app.post("/shared/purge")
    def shared_purge(body: IdsBody, m=Depends(manager)):
        """Permanently erase trashed items (owner/admin). Removes their content from server storage."""
        user, office = m
        oid = office["id"]
        if not is_active(user, office):
            raise HTTPException(402, NEED_PLAN)
        odir = os.path.realpath(office_dir(oid))

        def one(tid):
            with db.connect() as con:
                top = con.execute("SELECT * FROM shared_items WHERE office_id=? AND id=? AND trashed=1 "
                                  "AND trash_root=id", (oid, int(tid))).fetchone()
                if top is None:
                    raise HTTPException(404, "Not in the trash")
                rows = con.execute("SELECT id, blob FROM shared_items WHERE office_id=? AND trashed=1 "
                                   "AND trash_root=?", (oid, top["id"])).fetchall()
                con.execute("DELETE FROM shared_items WHERE office_id=? AND trashed=1 AND trash_root=?",
                            (oid, top["id"]))
                blobs = {r["blob"] for r in rows if r["blob"]}
                still = {r["blob"] for r in con.execute(
                    f"SELECT blob FROM shared_items WHERE office_id=? AND blob IN ({','.join('?' * len(blobs))})",
                    (oid, *blobs))} if blobs else set()
                log(con, oid, user, "purge", top["path"])
            for b in blobs - still:
                p = os.path.realpath(os.path.join(odir, b))
                if os.path.dirname(p) == odir and os.path.isfile(p):
                    try:
                        os.remove(p)   # server-side storage of erased shared-drive content
                    except OSError:
                        pass
            return {"id": int(tid), "path": top["path"]}

        done, errors = many(body.ids, one)
        return {"purged": done, "errors": [{"id": e["path"], "error": e["error"], "status": e["status"]} for e in errors]}

    @app.get("/shared/search")
    def shared_search(q: str = Query(default=""), m=Depends(drive)):
        _user, office = m
        oid = office["id"]
        q = " ".join(str(q or "").split())
        if not q:
            return {"items": []}
        with db.connect() as con:
            rows = con.execute(
                "SELECT * FROM shared_items WHERE office_id=? AND trashed=0 AND name LIKE ? ESCAPE '\\' "
                "ORDER BY depth, path_key LIMIT 200", (oid, "%" + escape_like(q[:200]) + "%")).fetchall()
            items = items_json(con, oid, rows)
        return {"items": items}

    @app.get("/shared/activity")
    def shared_activity(limit: int = 50, m=Depends(membership)):
        _user, office = m
        limit = max(1, min(int(limit), 500))
        with db.connect() as con:
            rows = con.execute("SELECT * FROM office_activity WHERE office_id=? ORDER BY id DESC LIMIT ?",
                               (office["id"], limit)).fetchall()
        return {"events": [{"ts": r["ts"], "user": r["email"], "action": r["action"], "path": r["path"],
                            "detail": r["detail"]} for r in rows]}

    @app.get("/shared/usage")
    def shared_usage(m=Depends(membership)):
        with db.connect() as con:
            used = used_bytes(con, m[1]["id"])
        return {"used": used, "quota": office_quota, "max_upload": max_upload_bytes}
