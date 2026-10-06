"""
archive_tools.py — ZIP / unzip and password engine for Office Axe.

Pure functions (no UI, no pywebview), unit-tested in tests/test_archive.py.

  zip_paths(paths, out_path=None, password=None, compression='deflate', level=6, progress=None)
  list_zip(path, password=None)
  extract_zip(path, dest_dir=None, password=None, members=None, progress=None, mode='folder')
  zip_set_password(path, new_password, old_password=None, out_path=None)
  zip_remove_password(path, password, out_path=None)
  pdf_is_encrypted(path) / pdf_set_password(...) / pdf_remove_password(...)

Guarantees shared by every function here:
  * Never overwrite an existing file or folder. New outputs get a free name
    ('a.zip' -> 'a (2).zip', folder 'a' -> 'a (2)') and files are created with
    exclusive-create ('xb').
  * Never modify or delete the source. Adding/removing a password writes a new
    file next to the original ('name_protected.zip', 'name_unlocked.pdf').
  * Nothing is ever deleted. If writing a new ZIP fails half-way, the
    incomplete file is renamed '.PENDING_DELETE_<name>' (never removed) and the
    error says so.
  * Errors are ArchiveError(message, code) with a readable message. Codes:
      need_password  — the archive/PDF needs a password and none was given
      wrong_password — the password does not match
      need_pyzipper  — creating password-protected (or reading AES) ZIPs needs
                       the optional 'pyzipper' package
      unsafe_path    — the ZIP contains absolute or '..' paths (zip-slip)
      not_zip / not_pdf / not_found / unsupported / not_encrypted / cancelled

Encryption: pyzipper (AES-256, WinZip AE-2) is used when importable. Without
it, plain zip/unzip still works and legacy ZipCrypto archives can still be
read (extracted, listed, or have their password removed) with the standard
library; only *creating* a password needs pyzipper.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import time
import zipfile
import zlib
from typing import Callable, Iterable, Optional

try:  # optional: AES-256 ZIP encryption
    import pyzipper  # type: ignore
    HAVE_PYZIPPER = True
except Exception:  # pragma: no cover - depends on the machine
    pyzipper = None  # type: ignore
    HAVE_PYZIPPER = False

ProgressFn = Optional[Callable[[int, int, str], None]]
CancelFn = Optional[Callable[[], bool]]

AES_COMPRESS_TYPE = 99          # WinZip AES marker in the local/central header
PROTECTED_SUFFIX = "_protected"
UNLOCKED_SUFFIX = "_unlocked"
PENDING_PREFIX = ".PENDING_DELETE_"
_CHUNK = 1024 * 256

NEED_PYZIPPER_MSG = ("Password-protected ZIPs need the 'pyzipper' component — "
                     "ask your administrator to install it")


class ArchiveError(Exception):
    """A user-facing error; ``code`` lets the UI react (e.g. ask for a password)."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def capabilities() -> dict:
    return {"zip_password": HAVE_PYZIPPER, "zip_aes_read": HAVE_PYZIPPER, "pdf_password": _has_fitz()}


# ─────────────────────────────────────────────────────────────────────────────
# Paths — never overwrite
# ─────────────────────────────────────────────────────────────────────────────

def unique_path(path: str, is_dir: bool = False) -> str:
    """Return *path* if free, else 'stem (2).ext' / 'folder (2)', 'stem (3).ext', …"""
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        return path
    if is_dir:
        stem, ext = path.rstrip("\\/"), ""
    else:
        stem, ext = os.path.splitext(path)
    m = re.match(r"^(.*) \((\d+)\)$", stem)
    if m:
        stem = m.group(1)
    i = 2
    while True:
        cand = f"{stem} ({i}){ext}"
        if not os.path.lexists(cand):
            return cand
        i += 1


def _create_exclusive(path: str):
    """Open a brand-new file for writing; picks the next free name on a race."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for _ in range(1000):
        final = unique_path(path)
        try:
            return final, open(final, "xb")
        except FileExistsError:
            continue
    raise ArchiveError(f"Could not find a free file name for {os.path.basename(path)}")


def _set_aside(path: str) -> str:
    """Rename an incomplete output to '.PENDING_DELETE_<name>' (never deleted)."""
    try:
        target = unique_path(os.path.join(os.path.dirname(path), PENDING_PREFIX + os.path.basename(path)))
        os.rename(path, target)
        return target
    except OSError:
        return path


_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


def _win_safe(part: str) -> str:
    """Make one path component valid on Windows."""
    p = _WIN_BAD.sub("_", part).rstrip(" .")
    if not p:
        return "_"
    if p.split(".")[0].upper() in _WIN_RESERVED:
        p = "_" + p
    return p


def safe_member_parts(name: str) -> list:
    """Split a ZIP member name into Windows-safe parts; raise on zip-slip."""
    n = str(name).replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        raise ArchiveError(f"Unsafe path in ZIP (absolute path): {name}", "unsafe_path")
    raw = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in raw):
        raise ArchiveError(f"Unsafe path in ZIP (goes outside the folder): {name}", "unsafe_path")
    return [_win_safe(p) for p in raw]


def _clean_out_name(name: str, ext: str) -> str:
    n = _WIN_BAD.sub("_", str(name or "").strip()).replace("/", "_").replace("\\", "_").strip(" .")
    if n.lower().endswith(ext):
        n = n[: -len(ext)].rstrip(" .")
    return n


def _strip_suffixes(stem: str) -> str:
    for suf in (PROTECTED_SUFFIX, UNLOCKED_SUFFIX):
        if stem.lower().endswith(suf):
            return stem[: -len(suf)] or stem
    return stem


def _sibling_out(path: str, suffix: str, ext: str, out_path: Optional[str]) -> str:
    if out_path:
        out = os.path.abspath(out_path)
    else:
        stem = _strip_suffixes(os.path.splitext(os.path.basename(path))[0])
        out = os.path.join(os.path.dirname(os.path.abspath(path)), stem + suffix + ext)
    out = unique_path(out)
    if os.path.normcase(out) == os.path.normcase(os.path.abspath(path)):
        raise ArchiveError("The new file must not replace the original")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Names / passwords
# ─────────────────────────────────────────────────────────────────────────────

def display_name(info) -> str:
    """Member name as text; fixes names stored in the local code page (Korean
    Windows zips store cp949 bytes without the UTF-8 flag)."""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            raw = name.encode("cp437")
        except UnicodeEncodeError:
            return name
        for enc in ("utf-8", "cp949"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return name


def _pw_candidates(password) -> list:
    if password is None or password == "":
        return []
    if isinstance(password, bytes):
        return [password]
    out = [password.encode("utf-8")]
    for enc in ("cp949", "cp437"):
        try:
            b = password.encode(enc)
        except UnicodeEncodeError:
            continue
        if b not in out:
            out.append(b)
    return out


def _is_encrypted(info) -> bool:
    return bool(info.flag_bits & 0x1)


def _is_aes(info) -> bool:
    return info.compress_type == AES_COMPRESS_TYPE


def _open_reader(path: str):
    if not os.path.isfile(path):
        raise ArchiveError(f"File not found: {os.path.basename(path)}", "not_found")
    try:
        if HAVE_PYZIPPER:
            return pyzipper.AESZipFile(path, "r")
        return zipfile.ZipFile(path, "r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        if isinstance(exc, PermissionError):
            raise ArchiveError(f"Access denied — is the file open in another program? ({os.path.basename(path)})")
        raise ArchiveError(f"“{os.path.basename(path)}” is not a valid ZIP archive", "not_zip")
    except Exception as exc:  # pyzipper.BadZipFile is its own class
        if "zip" in type(exc).__name__.lower():
            raise ArchiveError(f"“{os.path.basename(path)}” is not a valid ZIP archive", "not_zip")
        raise


def _check_readable(infos) -> None:
    if not HAVE_PYZIPPER and any(_is_aes(i) for i in infos):
        raise ArchiveError("This ZIP uses AES encryption. " + NEED_PYZIPPER_MSG, "need_pyzipper")


def _read_fully(zf, info, pwd) -> None:
    with zf.open(info, "r", pwd=pwd) as f:
        while f.read(_CHUNK):
            pass


def _find_password(zf, infos, password) -> Optional[bytes]:
    """Return the working password bytes (None if nothing is encrypted)."""
    enc = [i for i in infos if _is_encrypted(i) and not i.is_dir()]
    if not enc:
        return None
    if not password:
        raise ArchiveError("This ZIP is password-protected", "need_password")
    probe = min(enc, key=lambda i: i.file_size)
    for pw in _pw_candidates(password):
        try:
            _read_fully(zf, probe, pw)       # full read => CRC / HMAC verified
            return pw
        except NotImplementedError:
            raise ArchiveError("This ZIP uses a compression method that is not supported", "unsupported")
        except Exception:
            continue
    raise ArchiveError("Wrong password", "wrong_password")


def _read_error(exc: Exception, encrypted: bool, name: str) -> ArchiveError:
    if isinstance(exc, ArchiveError):
        return exc
    if isinstance(exc, NotImplementedError):
        return ArchiveError(f"“{name}” uses a compression method that is not supported", "unsupported")
    msg = str(exc)
    if encrypted and (isinstance(exc, (RuntimeError, zlib.error)) or "password" in msg.lower()
                      or "crc" in msg.lower()):
        return ArchiveError("Wrong password", "wrong_password")
    if isinstance(exc, PermissionError):
        return ArchiveError(f"Access denied — is a file open in another program? ({exc.filename or name})")
    if "crc" in msg.lower() or isinstance(exc, (zipfile.BadZipFile, zlib.error, EOFError)):
        return ArchiveError(f"The ZIP is damaged (“{name}” could not be read)", "not_zip")
    return ArchiveError(f"Could not read “{name}”: {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────────────────────

def _iso(dt) -> str:
    try:
        return "%04d-%02d-%02d %02d:%02d" % dt[:5]
    except Exception:
        return ""


def list_zip(path: str, password=None) -> dict:
    """Entries + summary. *password* (optional) is verified when given:
    the result then carries password_ok True/False."""
    zf = _open_reader(path)
    with zf:
        infos = zf.infolist()
        entries = []
        total = comp = 0
        n_files = n_dirs = 0
        any_enc = any_aes = False
        for idx, i in enumerate(infos):
            name = display_name(i).replace("\\", "/")
            enc, aes = _is_encrypted(i), _is_aes(i)
            any_enc |= enc
            any_aes |= aes
            is_dir = i.is_dir() or name.endswith("/")
            if is_dir:
                n_dirs += 1
            else:
                n_files += 1
                total += i.file_size
                comp += i.compress_size
            entries.append({
                "index": idx, "name": name, "size": i.file_size, "compressed": i.compress_size,
                "encrypted": enc, "date": _iso(i.date_time), "is_dir": is_dir,
            })
        out = {
            "path": os.path.abspath(path), "entries": entries, "count": len(entries),
            "files": n_files, "dirs": n_dirs, "total_size": total, "compressed_size": comp,
            "encrypted": any_enc, "aes": any_aes, "can_read_aes": HAVE_PYZIPPER,
            "size": os.path.getsize(path),
        }
        if password and any_enc:
            try:
                _check_readable(infos)
                _find_password(zf, infos, password)
                out["password_ok"] = True
            except ArchiveError as exc:
                if exc.code != "wrong_password":
                    raise
                out["password_ok"] = False
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Zip
# ─────────────────────────────────────────────────────────────────────────────

_COMPRESSION = {
    "deflate": zipfile.ZIP_DEFLATED, "deflated": zipfile.ZIP_DEFLATED,
    "store": zipfile.ZIP_STORED, "stored": zipfile.ZIP_STORED, "none": zipfile.ZIP_STORED,
    "bzip2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA,
}


def default_zip_name(paths: list) -> str:
    """'report.pdf' -> 'report.zip'; 'Docs' (folder) -> 'Docs.zip';
    several items -> '<parent folder>.zip'."""
    paths = [os.path.abspath(p) for p in paths]
    if len(paths) == 1:
        p = paths[0]
        base = os.path.basename(p.rstrip("\\/")) or "Archive"
        stem = base if os.path.isdir(p) else (os.path.splitext(base)[0] or base)
        return stem + ".zip"
    parent = os.path.basename(os.path.dirname(paths[0]).rstrip("\\/"))
    return (parent or "Archive") + ".zip"


def _collect(paths: list, exclude: str):
    """[(abs_path, arcname, is_dir)] for files/folders, recursively."""
    items, seen = [], set()
    excl = os.path.normcase(os.path.abspath(exclude))

    def add(abs_p, arc, is_dir):
        key = arc.lower()
        if key in seen:
            stem, ext = (arc, "") if is_dir else os.path.splitext(arc)
            i = 2
            while f"{stem} ({i}){ext}".lower() in seen:
                i += 1
            arc = f"{stem} ({i}){ext}"
            key = arc.lower()
        seen.add(key)
        items.append((abs_p, arc, is_dir))
        return arc

    for p in paths:
        p = os.path.abspath(p)
        base = os.path.basename(p.rstrip("\\/")) or "drive"
        if os.path.isdir(p):
            top = add(p, base, True)
            for root, dirs, files in os.walk(p):
                dirs.sort(key=str.lower)
                rel_root = os.path.relpath(root, p)
                arc_root = top if rel_root == "." else top + "/" + rel_root.replace(os.sep, "/")
                if rel_root != ".":
                    add(root, arc_root, True)
                for f in sorted(files, key=str.lower):
                    fp = os.path.join(root, f)
                    if os.path.normcase(fp) == excl:
                        continue
                    add(fp, arc_root + "/" + f, False)
        elif os.path.isfile(p):
            if os.path.normcase(p) != excl:
                add(p, base, False)
        else:
            raise ArchiveError(f"Not found: {base}", "not_found")
    return items


def zip_paths(paths, out_path: Optional[str] = None, password: Optional[str] = None,
              compression: str = "deflate", level: int = 6, progress: ProgressFn = None,
              cancel: CancelFn = None) -> dict:
    """Zip files and folders (recursively) into a new ZIP. Returns
    {out_path, files, dirs, bytes, size, encrypted}."""
    paths = [str(p) for p in (paths or []) if str(p).strip()]
    if not paths:
        raise ArchiveError("Nothing selected to zip")
    for p in paths:
        if not os.path.exists(p):
            raise ArchiveError(f"Not found: {os.path.basename(p)}", "not_found")
    if password and not HAVE_PYZIPPER:
        raise ArchiveError(NEED_PYZIPPER_MSG, "need_pyzipper")
    comp = _COMPRESSION.get(str(compression or "deflate").lower())
    if comp is None:
        raise ArchiveError(f"Unknown compression: {compression}")
    level = max(0, min(9, int(level if level is not None else 6)))

    parent = os.path.dirname(os.path.abspath(paths[0].rstrip("\\/"))) or os.getcwd()
    if out_path:
        out_path = str(out_path)
        if not os.path.dirname(out_path):
            out_path = os.path.join(parent, out_path)
        d, b = os.path.split(os.path.abspath(out_path))
        b = _clean_out_name(b, ".zip") or os.path.splitext(default_zip_name(paths))[0]
        out_path = os.path.join(d, b + ".zip")
    else:
        out_path = os.path.join(parent, default_zip_name(paths))
    out_path = unique_path(out_path)
    items = _collect(paths, out_path)
    total = sum(os.path.getsize(a) for a, _, d in items if not d) or 0

    final, fh = _create_exclusive(out_path)
    done = 0
    n_files = n_dirs = 0
    kw = {"compression": comp}
    if comp in (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2):
        kw["compresslevel"] = level if comp == zipfile.ZIP_DEFLATED else max(1, level)
    try:
        with fh:
            if password:
                zf = pyzipper.AESZipFile(fh, "w", encryption=pyzipper.WZ_AES, **kw)
                zf.setpassword(password.encode("utf-8"))
                try:
                    zf.setencryption(pyzipper.WZ_AES, nbits=256)
                except Exception:
                    pass
            else:
                zf = zipfile.ZipFile(fh, "w", allowZip64=True, strict_timestamps=False, **kw)
            with zf:
                if progress:
                    progress(0, total, "")
                for abs_p, arc, is_dir in items:
                    if cancel and cancel():
                        raise ArchiveError("Cancelled", "cancelled")
                    if is_dir:
                        zf.write(abs_p, arc + "/")
                        n_dirs += 1
                        continue
                    try:
                        zf.write(abs_p, arc)
                    except PermissionError as exc:
                        raise ArchiveError(f"Access denied — is “{os.path.basename(abs_p)}” open in another program?") from exc
                    n_files += 1
                    try:
                        done += os.path.getsize(abs_p)
                    except OSError:
                        pass
                    if progress:
                        progress(done, total, arc)
    except BaseException as exc:
        aside = _set_aside(final)
        if isinstance(exc, ArchiveError):
            if exc.code == "cancelled":
                raise ArchiveError(f"Cancelled — the incomplete file was renamed {os.path.basename(aside)}", "cancelled")
            raise ArchiveError(f"{exc} (the incomplete file was renamed {os.path.basename(aside)})", exc.code)
        if isinstance(exc, Exception):
            raise ArchiveError(f"Zip failed: {exc} (the incomplete file was renamed {os.path.basename(aside)})") from exc
        raise
    return {"out_path": final, "files": n_files, "dirs": n_dirs, "bytes": total,
            "size": os.path.getsize(final), "encrypted": bool(password)}


# ─────────────────────────────────────────────────────────────────────────────
# Extract
# ─────────────────────────────────────────────────────────────────────────────

def _select(infos, members) -> list:
    if members is None or members == []:
        return list(range(len(infos)))
    names = [display_name(i).replace("\\", "/") for i in infos]
    picked = set()
    for m in members:
        if isinstance(m, int) or (isinstance(m, str) and m.isdigit() and int(m) < len(infos)
                                   and m not in names):
            picked.add(int(m))
            continue
        m = str(m).replace("\\", "/")
        prefix = m if m.endswith("/") else m + "/"
        hit = False
        for idx, n in enumerate(names):
            if n == m or n.startswith(prefix) or n == prefix:
                picked.add(idx)
                hit = True
        if not hit:
            raise ArchiveError(f"Not in the ZIP: {m}", "not_found")
    return sorted(i for i in picked if 0 <= i < len(infos))


def _to_epoch(dt) -> Optional[float]:
    try:
        return time.mktime(tuple(dt) + (0, 0, -1))
    except Exception:
        return None


def extract_zip(path: str, dest_dir: Optional[str] = None, password=None, members=None,
                progress: ProgressFn = None, mode: str = "folder", cancel: CancelFn = None) -> dict:
    """Extract a ZIP without ever overwriting anything.

    mode='folder' (default): into a NEW folder — '<dest_dir or zip folder>/<zip name>/'
                             ('name (2)/' if taken). A ZIP whose only top-level item is a
                             folder of the same name is not nested twice.
    mode='here':             straight into dest_dir (default: the ZIP's folder); a
                             top-level file/folder that already exists gets a free name.
    Returns {out_dir, select, files, dirs, bytes, renamed}.
    """
    zpath = os.path.abspath(path)
    zf = _open_reader(zpath)
    with zf:
        infos = zf.infolist()
        chosen = _select(infos, members)
        sel = [infos[i] for i in chosen]
        _check_readable(sel)
        parts_of = {}
        for i in sel:
            parts_of[id(i)] = safe_member_parts(display_name(i))   # zip-slip check first
        pwd = _find_password(zf, sel, password)

        base_dir = os.path.abspath(dest_dir) if dest_dir else os.path.dirname(zpath)
        if os.path.exists(base_dir) and not os.path.isdir(base_dir):
            raise ArchiveError(f"Not a folder: {base_dir}")
        stem = _win_safe(os.path.splitext(os.path.basename(zpath))[0])
        tops = {parts_of[id(i)][0] for i in sel if parts_of[id(i)]}
        single_top_dir = (len(tops) == 1 and next(iter(tops)).lower() == stem.lower()
                          and all(len(parts_of[id(i)]) > 1 or i.is_dir() for i in sel if parts_of[id(i)]))

        renamed = []
        top_map = {}
        if mode == "here" or single_top_dir:
            root = base_dir
            os.makedirs(root, exist_ok=True)
            # Top-level names that already exist get a free name (computed once, so a
            # folder's children all follow it).
            for t in sorted(tops):
                is_dir = any(len(parts_of[id(i)]) > 1 or i.is_dir() for i in sel
                             if parts_of[id(i)] and parts_of[id(i)][0] == t)
                cand = unique_path(os.path.join(root, t), is_dir=is_dir)
                top_map[t] = os.path.basename(cand)
                if top_map[t] != t:
                    renamed.append({"from": t, "to": top_map[t]})
            out_dir = os.path.join(root, top_map[next(iter(tops))]) if single_top_dir and mode != "here" else root
        else:
            root = unique_path(os.path.join(base_dir, stem), is_dir=True)
            os.makedirs(root)
            out_dir = root

        total = sum(i.file_size for i in sel if not i.is_dir())
        done = n_files = n_dirs = 0
        first = None
        if progress:
            progress(0, total, "")
        for i in sel:
            if cancel and cancel():
                raise ArchiveError("Cancelled — files extracted so far were kept", "cancelled")
            parts = list(parts_of[id(i)])
            if not parts:
                continue
            if parts[0] in top_map:
                parts[0] = top_map[parts[0]]
            target = os.path.join(root, *parts)
            if first is None:
                first = os.path.join(root, parts[0])
            if i.is_dir():
                os.makedirs(target, exist_ok=True)
                n_dirs += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                final, fh = _create_exclusive(target)
                if final != os.path.abspath(target):
                    renamed.append({"from": "/".join(parts), "to": os.path.relpath(final, root).replace(os.sep, "/")})
                with fh, zf.open(i, "r", pwd=pwd if _is_encrypted(i) else None) as src:
                    while True:
                        if cancel and cancel():
                            raise ArchiveError("Cancelled — files extracted so far were kept", "cancelled")
                        buf = src.read(_CHUNK)
                        if not buf:
                            break
                        fh.write(buf)
                        done += len(buf)
                        if progress:
                            progress(done, total, display_name(i))
            except ArchiveError:
                raise
            except Exception as exc:
                raise _read_error(exc, _is_encrypted(i), display_name(i)) from exc
            ts = _to_epoch(i.date_time)
            if ts:
                try:
                    os.utime(final, (ts, ts))
                except OSError:
                    pass
            n_files += 1
        if progress:
            progress(total, total, "")
    select = out_dir if (mode != "here" or single_top_dir) else (first or out_dir)
    return {"out_dir": out_dir, "select": select, "files": n_files, "dirs": n_dirs,
            "bytes": total, "renamed": renamed}


# ─────────────────────────────────────────────────────────────────────────────
# ZIP passwords (always into a NEW file)
# ─────────────────────────────────────────────────────────────────────────────

def _repack(path: str, out_path: str, read_password, write_password: Optional[str],
            progress: ProgressFn = None) -> dict:
    zf = _open_reader(path)
    with zf:
        infos = zf.infolist()
        _check_readable(infos)
        pwd = _find_password(zf, infos, read_password)
        total = sum(i.file_size for i in infos if not i.is_dir())
        final, fh = _create_exclusive(out_path)
        done = 0
        try:
            with fh:
                if write_password:
                    w = pyzipper.AESZipFile(fh, "w", compression=zipfile.ZIP_DEFLATED,
                                            encryption=pyzipper.WZ_AES)
                    w.setpassword(write_password.encode("utf-8"))
                    try:
                        w.setencryption(pyzipper.WZ_AES, nbits=256)
                    except Exception:
                        pass
                    zi_cls = getattr(w, "zipinfo_cls", None) or pyzipper.ZipInfo
                else:
                    w = zipfile.ZipFile(fh, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
                    zi_cls = zipfile.ZipInfo
                with w:
                    for i in infos:
                        name = display_name(i).replace("\\", "/")
                        safe_member_parts(name)
                        zi = zi_cls(name, date_time=i.date_time)
                        zi.external_attr = i.external_attr
                        if i.is_dir() or name.endswith("/"):
                            zi.compress_type = zipfile.ZIP_STORED
                            w.writestr(zi, b"")
                            continue
                        zi.compress_type = zipfile.ZIP_DEFLATED
                        rp = pwd if _is_encrypted(i) else None
                        try:
                            if write_password:
                                w.writestr(zi, zf.read(i, pwd=rp))
                            else:
                                zi.file_size = i.file_size
                                with zf.open(i, "r", pwd=rp) as src, \
                                        w.open(zi, "w", force_zip64=i.file_size > 0x7FFFFFFF) as dst:
                                    shutil.copyfileobj(src, dst, _CHUNK)
                        except Exception as exc:
                            raise _read_error(exc, _is_encrypted(i), name) from exc
                        done += i.file_size
                        if progress:
                            progress(done, total, name)
        except BaseException as exc:
            aside = _set_aside(final)
            if isinstance(exc, ArchiveError):
                raise ArchiveError(f"{exc} (the incomplete file was renamed {os.path.basename(aside)})", exc.code)
            if isinstance(exc, Exception):
                raise ArchiveError(f"Failed: {exc} (the incomplete file was renamed {os.path.basename(aside)})") from exc
            raise
    return {"out_path": final, "files": sum(1 for i in infos if not i.is_dir()),
            "size": os.path.getsize(final)}


def zip_set_password(path: str, new_password: str, old_password=None,
                     out_path: Optional[str] = None, progress: ProgressFn = None) -> dict:
    """Add (or change) a ZIP password → new 'name_protected.zip' (AES-256)."""
    if not new_password:
        raise ArchiveError("Enter a password")
    if not HAVE_PYZIPPER:
        raise ArchiveError(NEED_PYZIPPER_MSG, "need_pyzipper")
    out = _sibling_out(path, PROTECTED_SUFFIX, ".zip", out_path)
    r = _repack(path, out, old_password, new_password, progress)
    r["encrypted"] = True
    return r


def zip_remove_password(path: str, password, out_path: Optional[str] = None,
                        progress: ProgressFn = None) -> dict:
    """Write an unprotected copy → new 'name_unlocked.zip'. The password is required."""
    zf = _open_reader(path)
    with zf:
        if not any(_is_encrypted(i) for i in zf.infolist()):
            raise ArchiveError("This ZIP has no password", "not_encrypted")
    if not password:
        raise ArchiveError("This ZIP is password-protected", "need_password")
    out = _sibling_out(path, UNLOCKED_SUFFIX, ".zip", out_path)
    r = _repack(path, out, password, None, progress)
    r["encrypted"] = False
    return r


# ─────────────────────────────────────────────────────────────────────────────
# PDF passwords (PyMuPDF, AES-256)
# ─────────────────────────────────────────────────────────────────────────────

def _has_fitz() -> bool:
    try:
        _fitz()
        return True
    except ArchiveError:
        return False


def _fitz():
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            raise ArchiveError("PyMuPDF is not installed", "unsupported")
    return fitz


def _open_pdf(path: str):
    fitz = _fitz()
    if not os.path.isfile(path):
        raise ArchiveError(f"File not found: {os.path.basename(path)}", "not_found")
    try:
        return fitz.open(path)
    except Exception:
        raise ArchiveError(f"“{os.path.basename(path)}” is not a valid PDF", "not_pdf")


def pdf_is_encrypted(path: str) -> dict:
    """{encrypted, needs_password, method}. 'encrypted' is also true for PDFs that
    open without a password but carry an owner password (restrictions)."""
    doc = _open_pdf(path)
    try:
        needs = bool(doc.needs_pass)
        method = None if needs else (doc.metadata or {}).get("encryption")
        return {"encrypted": needs or bool(method), "needs_password": needs, "method": method or ""}
    finally:
        doc.close()


def pdf_check_password(path: str, password: str) -> bool:
    doc = _open_pdf(path)
    try:
        return bool(doc.authenticate(password or ""))
    finally:
        doc.close()


def _permission_bits(fitz, permissions) -> tuple:
    p = {"print": True, "copy": True, "edit": True, "annotate": True}
    if isinstance(permissions, dict):
        for k in p:
            if k in permissions:
                p[k] = bool(permissions[k])
    bits = fitz.PDF_PERM_ACCESSIBILITY
    if p["print"]:
        bits |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
    if p["copy"]:
        bits |= fitz.PDF_PERM_COPY
    if p["edit"]:
        bits |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ASSEMBLE
    if p["annotate"]:
        bits |= fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM
    return bits, all(p.values())


def pdf_set_password(path: str, user_password: str, owner_password: Optional[str] = None,
                     permissions: Optional[dict] = None, out_path: Optional[str] = None,
                     current_password: Optional[str] = None) -> dict:
    """Protect a PDF with AES-256 → new 'name_protected.pdf'.

    user_password  — needed to open the file (may be empty when only restricting).
    owner_password — unlocks full rights. When omitted it equals the user password,
                     unless some permissions are restricted: then a random owner
                     password is used so the restrictions really apply.
    permissions    — {print, copy, edit, annotate}: False to forbid.
    current_password — needed when the source PDF is already password-protected.
    """
    fitz = _fitz()
    user_password = user_password or ""
    owner_password = owner_password or ""
    if not user_password and not owner_password:
        raise ArchiveError("Enter a password")
    bits, all_allowed = _permission_bits(fitz, permissions)
    if not owner_password:
        owner_password = user_password if all_allowed else secrets.token_urlsafe(24)
    doc = _open_pdf(path)
    try:
        if doc.needs_pass:
            if not current_password:
                raise ArchiveError("This PDF is password-protected", "need_password")
            if not doc.authenticate(current_password):
                raise ArchiveError("Wrong password", "wrong_password")
        out = unique_path(_sibling_out(path, PROTECTED_SUFFIX, ".pdf", out_path))
        try:
            doc.save(out, garbage=1, deflate=True, encryption=fitz.PDF_ENCRYPT_AES_256,
                     user_pw=user_password, owner_pw=owner_password, permissions=bits)
        except PermissionError:
            raise ArchiveError("Access denied — cannot write next to the original")
        return {"out_path": out, "size": os.path.getsize(out), "encrypted": True}
    finally:
        doc.close()


def pdf_remove_password(path: str, password: str, out_path: Optional[str] = None) -> dict:
    """Write an unprotected copy → new 'name_unlocked.pdf'. The password (open or
    owner password) is required."""
    fitz = _fitz()
    info = pdf_is_encrypted(path)
    if not info["encrypted"]:
        raise ArchiveError("This PDF has no password", "not_encrypted")
    if not password:
        raise ArchiveError("This PDF is password-protected", "need_password")
    # A failed authenticate() can leave the document unusable, so check on a
    # throw-away handle and save from a fresh one.
    if not pdf_check_password(path, password):
        raise ArchiveError("Wrong password", "wrong_password")
    doc = _open_pdf(path)
    try:
        doc.authenticate(password)
        out = unique_path(_sibling_out(path, UNLOCKED_SUFFIX, ".pdf", out_path))
        doc.save(out, garbage=1, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
        return {"out_path": out, "size": os.path.getsize(out), "encrypted": False}
    finally:
        doc.close()
