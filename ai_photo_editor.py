# -*- coding: utf-8 -*-
"""
AI photo enhancement for the file workspace.
The only action is "Wear Suit & Tie", using OpenAI ``gpt-image-1`` ``images.edit``.
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
import uuid

try:  # Tk is only needed for the legacy desktop frame; run_ai_edit() is Tk-free.
    import tkinter as tk
    from tkinter import messagebox, ttk
except Exception:  # pragma: no cover - headless / tkinter-less environments
    import types as _types

    tk = _types.SimpleNamespace(Frame=object, Misc=object, Toplevel=object)  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
from typing import Any, Callable

import file_ops as fo

_DEFAULT_AI_EDIT_USD_EST = 0.04   # legacy Tk hint only; the web UI uses estimate_cost_usd()
_AI_EDIT_MODEL = "gpt-image-1"
_REQUEST_TIMEOUT_S = 180.0        # one images.edit call; the SDK default is 10 minutes
_MAX_SOURCE_PIXELS = 80_000_000   # refuse absurdly large inputs instead of eating RAM

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
        # default prompt (legacy Tk frame); the web UI builds it via build_prompt()
        "prompt": (
            _IDENTITY_PREFIX
            + "Replace the clothing on the person's body with a formal dark business suit and tie. "
            "Keep the face, hair, and skin completely unchanged. "
            "Realistic fabric, natural lighting and shadows on the clothing."
        ),
        "mask_strategy": "body_only",   # protect face/head, edit body
        "background": "opaque",
        "quality": "high",
        "suffix": "_suit",
    },
}

# Options shown in the UI for "Wear Suit & Tie" → prompt wording.
SUIT_COLORS: dict[str, str] = {
    "black": "black",
    "navy": "dark navy blue",
    "charcoal": "charcoal grey",
    "grey": "medium grey",
}
QUALITIES = ("high", "medium")
DEFAULT_OPTIONS = {"suit_color": "black", "tie": True, "quality": "high"}


def normalize_options(opts: dict | None) -> dict:
    """Clamp UI options to known values (unknown keys/values fall back to defaults)."""
    o = dict(DEFAULT_OPTIONS)
    opts = opts or {}
    c = str(opts.get("suit_color") or "").strip().lower()
    if c == "gray":
        c = "grey"
    if c in SUIT_COLORS:
        o["suit_color"] = c
    if "tie" in opts:
        t = opts.get("tie")
        o["tie"] = t if isinstance(t, bool) else str(t).strip().lower() not in ("0", "false", "no", "off", "")
    q = str(opts.get("quality") or "").strip().lower()
    if q in QUALITIES:
        o["quality"] = q
    return o


def build_prompt(action: str, opts: dict | None = None) -> str:
    """The images.edit prompt for *action* with the user's options applied."""
    if action != "wear_suit":
        return _ACTION_PROMPTS[action]["prompt"]
    o = normalize_options(opts)
    color = SUIT_COLORS[o["suit_color"]]
    if o["tie"]:
        neck = ("a crisp white dress shirt and a conservative solid-colour necktie "
                "in a dark shade that matches the suit, neatly knotted at the collar")
    else:
        neck = "a crisp white dress shirt with the collar neatly open and NO necktie"
    return (
        _IDENTITY_PREFIX
        + "Do not alter the hair, ears, neck skin, glasses, head position or head size. "
        f"Dress the person in a well-fitted, formal {color} business suit (jacket with lapels) "
        f"over {neck}. "
        "Only the clothing below the neck changes. Keep the background, framing, lighting and "
        "colour balance of the original photo; do not crop, zoom, rotate or reposition the person. "
        "Photorealistic fabric with natural folds and shadows that match the original light. "
        "No text, logos, badges, jewellery or accessories. Suitable for an official ID or passport photo."
    )


def output_suffix(action: str) -> str:
    return _ACTION_PROMPTS.get(action, {}).get("suffix", "_ai")


# ---------------------------------------------------------------------------
# Cost: estimate before a run, and the session total of real runs
# ---------------------------------------------------------------------------

# gpt-image-1 output tokens per image (OpenAI pricing page) and $/token.
_OUT_TOKENS = {
    ("high", "1024x1024"): 4160, ("high", "1024x1536"): 6240, ("high", "1536x1024"): 6208,
    ("medium", "1024x1024"): 1056, ("medium", "1024x1536"): 1584, ("medium", "1536x1024"): 1568,
}
_USD_TEXT_IN = 5.0 / 1_000_000
_USD_IMAGE_IN = 10.0 / 1_000_000
_USD_IMAGE_OUT = 40.0 / 1_000_000
_EST_INPUT_USD = 0.04             # source image at high input fidelity + mask + prompt

_COST_LOCK = threading.Lock()
_SESSION_COST_USD = 0.0


def edit_size_for(w: int, h: int) -> str:
    """The images.edit output size closest to the photo's aspect ratio."""
    r = (w / float(h)) if h else 1.0
    if r < 0.83:
        return "1024x1536"
    if r > 1.2:
        return "1536x1024"
    return "1024x1024"


def estimate_cost_usd(w: int = 0, h: int = 0, quality: str = "high") -> float:
    """Rough USD for one Wear-Suit run (portrait assumed when the size is unknown)."""
    size = edit_size_for(w, h) if (w and h) else "1024x1536"
    q = quality if quality in QUALITIES else "high"
    return round(_OUT_TOKENS[(q, size)] * _USD_IMAGE_OUT + _EST_INPUT_USD, 3)


def _cost_from_usage(usage) -> float | None:
    """Actual USD from an images.edit ``usage`` block (None when absent)."""
    if usage is None:
        return None

    def g(o, k):
        return o.get(k) if isinstance(o, dict) else getattr(o, k, None)
    try:
        out_t = int(g(usage, "output_tokens") or 0)
        det = g(usage, "input_tokens_details")
        if det is not None:
            txt = int(g(det, "text_tokens") or 0)
            img = int(g(det, "image_tokens") or 0)
        else:
            txt, img = 0, int(g(usage, "input_tokens") or 0)
        if not (out_t or txt or img):
            return None
        return txt * _USD_TEXT_IN + img * _USD_IMAGE_IN + out_t * _USD_IMAGE_OUT
    except Exception:
        return None


def _add_session_cost(usd: float) -> None:
    global _SESSION_COST_USD
    with _COST_LOCK:
        _SESSION_COST_USD += max(0.0, float(usd or 0.0))


def session_cost_usd() -> float:
    with _COST_LOCK:
        return _SESSION_COST_USD


def reset_session_cost() -> None:
    global _SESSION_COST_USD
    with _COST_LOCK:
        _SESSION_COST_USD = 0.0


# ---------------------------------------------------------------------------
# Mask generation
#
# OpenAI images.edit: FULLY TRANSPARENT mask pixels (alpha 0) mark the area to
# EDIT; opaque pixels are kept. (The masks here used to be the other way round,
# which asked the model to repaint the face and keep the clothes.)
# ---------------------------------------------------------------------------

_FACE_KEEP_END = 0.48      # top 48 % of the photo: opaque = protected (face/head)
_BODY_EDIT_START = 0.62    # from 62 % down: transparent = clothing may be edited


def _make_mask_full(w: int, h: int) -> "Image":
    """Fully transparent mask — the entire image is editable."""
    from PIL import Image
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _make_mask_body_only(w: int, h: int) -> "Image":
    """
    Protect the face/head (top part opaque = kept) and let the model repaint
    the body/clothing (bottom part transparent = edited), with a soft ramp in
    between to avoid a seam at the collar.
    """
    from PIL import Image
    keep_end = int(h * _FACE_KEEP_END)
    edit_start = max(keep_end + 1, int(h * _BODY_EDIT_START))
    col = Image.new("L", (1, h), 255)
    px = col.load()
    for y in range(h):
        if y <= keep_end:
            a = 255
        elif y >= edit_start:
            a = 0
        else:
            a = int(round(255 * (1.0 - (y - keep_end) / float(edit_start - keep_end))))
        px[0, y] = a
    alpha = col.resize((w, h), Image.Resampling.NEAREST)
    mask = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    mask.putalpha(alpha)
    return mask


def _build_mask_for_action(action_name: str, w: int, h: int) -> "Image | None":
    strategy = _ACTION_PROMPTS.get(action_name, {}).get("mask_strategy", "full")
    try:
        if strategy == "full":
            return None       # no mask = whole image editable
        elif strategy == "body_only":
            return _make_mask_body_only(w, h)
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


def _unique_ai_sidecar_path(original_path: str, suffix: str = "_ai") -> str:
    """``<stem><suffix><ext>`` next to *original_path*; ``-2``, ``-3`` ... when taken."""
    original_path = os.path.abspath(os.path.normpath(original_path))
    d, base = os.path.dirname(original_path), os.path.basename(original_path)
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = ".png"
    n = 1
    while True:
        tag = suffix if n == 1 else f"{suffix}-{n}"
        cand = os.path.join(d, f"{stem}{tag}{ext}")
        if not os.path.exists(cand):
            return cand
        n += 1
        if n > 5000:
            return os.path.join(d, f"{stem}{suffix}_{uuid.uuid4().hex[:8]}{ext}")


def ai_output_path(path: str, action: str) -> str:
    """Where *action* on *path* will be saved, e.g. ``photo_suit.jpg``."""
    return _unique_ai_sidecar_path(path, output_suffix(action))


UPLOAD_MAX_EDGE = 1024


def ai_photo_actions() -> list[dict]:
    """Return the available AI photo actions as [{key, label}, ...]."""
    return [{"key": k, "label": v["label"]} for k, v in _ACTION_PROMPTS.items()]


def ai_photo_options() -> dict:
    """Choices for the UI (suit colours, qualities, defaults)."""
    return {
        "suit_colors": [{"key": k, "label": k.capitalize() if k != "navy" else "Navy"}
                        for k in SUIT_COLORS],
        "qualities": list(QUALITIES),
        "defaults": dict(DEFAULT_OPTIONS),
        "model": _AI_EDIT_MODEL,
    }


def _prepare_upload(path: str):
    """Open (EXIF-upright), flatten and downscale *path* for upload.

    Returns (rgb_image_full, upload_png_bytes, (up_w, up_h)).
    """
    Image = _pil_image()
    from PIL import ImageOps
    with Image.open(path) as src:
        if src.width * src.height > _MAX_SOURCE_PIXELS:
            raise ValueError(f"Image is too large ({src.width}×{src.height}); "
                             "please resize it below 80 megapixels first")
        try:
            im = ImageOps.exif_transpose(src)
        except Exception:
            im = src.copy()
        im.load()
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    full = im
    w, h = im.size
    edge = max(w, h)
    if edge > UPLOAD_MAX_EDGE:
        scale = UPLOAD_MAX_EDGE / float(edge)
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                       Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=6)
    return full, buf.getvalue(), im.size


def run_ai_edit(
    path: str,
    action: str,
    out_path: str = "",
    api_key: str = "",
    options: dict | None = None,
) -> tuple[bool, str]:
    """Run an OpenAI image edit on *path* and save the result as a NEW file.

    Headless (no Tk). Returns ``(True, out_path)`` on success or
    ``(False, error_message)`` on failure. The original file is never
    modified; when *out_path* is empty (or already exists) a unique
    ``<stem>_suit<ext>`` sidecar path is chosen next to the original.
    *options*: {suit_color: black|navy|charcoal|grey, tie: bool, quality: high|medium}.
    """
    Image = _pil_image()
    if not Image:
        return False, "Pillow is required (pip install pillow)."
    if action not in _ACTION_PROMPTS:
        return False, f"Unknown AI photo action: {action}"
    path = os.path.abspath(os.path.normpath(path or ""))
    if not path or not os.path.isfile(path):
        return False, f"Image not found: {path}"
    ext = os.path.splitext(path)[1].lower()
    if ext not in fo.IMAGE_EXT:
        return False, f"Not a supported image format: {ext}"
    opts = normalize_options(options)

    # ── API key ──────────────────────────────────────────────────────────
    merge_dotenv_into_environ()
    key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return False, (
            "No OpenAI API key found. Set it in Settings, or create .env in "
            f"{_app_base_dir()} with OPENAI_API_KEY=sk-..."
        )
    if not _openai_key_looks_valid(key):
        return False, "OPENAI_API_KEY does not look valid (should start with sk-)."
    OpenAI = _openai_client()
    if not OpenAI:
        return False, "OpenAI SDK not installed (pip install openai)."

    # ── Output path (never the original, never an existing file) ─────────
    suffix = output_suffix(action)
    if out_path:
        out_path = os.path.abspath(os.path.normpath(out_path))
        if os.path.normcase(out_path) == os.path.normcase(path) or os.path.exists(out_path):
            out_path = _unique_ai_sidecar_path(out_path, "")
    else:
        out_path = _unique_ai_sidecar_path(path, suffix)

    info = _ACTION_PROMPTS[action]
    try:
        full_img, upload_png, (up_w, up_h) = _prepare_upload(path)
        orig_size = full_img.size
    except Exception as e:
        return False, f"Could not read or resize the image: {e}"

    mask_bytes: bytes | None = None
    mask_img = _build_mask_for_action(action, up_w, up_h)
    if mask_img is not None:
        try:
            mbuf = io.BytesIO()
            mask_img.save(mbuf, format="PNG")
            mask_bytes = mbuf.getvalue()
        except Exception:
            mask_bytes = None

    # ── API call ─────────────────────────────────────────────────────────
    try:
        client_kw: dict = {"api_key": key, "timeout": _REQUEST_TIMEOUT_S, "max_retries": 1}
        for env, kw in (
            ("OPENAI_PROJECT_ID", "project"),
            ("OPENAI_ORG_ID", "organization"),
            ("OPENAI_BASE_URL", "base_url"),
        ):
            val = (os.environ.get(env) or "").strip()
            if val:
                client_kw[kw] = val
        client = OpenAI(**client_kw)
        call_kwargs: dict = dict(
            model=_AI_EDIT_MODEL,
            prompt=build_prompt(action, opts),
            n=1,
            size=edit_size_for(up_w, up_h),
            quality=opts.get("quality") or info.get("quality", "high"),
            background=info.get("background", "opaque"),
        )
        image_file = ("upload.png", upload_png, "image/png")
        if mask_bytes is not None:
            call_kwargs["mask"] = ("mask.png", mask_bytes, "image/png")

        def _edit(fidelity: bool):
            kw = dict(call_kwargs)
            if fidelity:
                # keeps faces much closer to the source (sent as a raw body field so
                # older SDKs that lack the keyword still work)
                kw["extra_body"] = {"input_fidelity": "high"}
            return client.images.edit(image=image_file, **kw)

        try:
            result = _edit(True)
        except Exception as e:
            if "input_fidelity" not in str(e):
                raise
            result = _edit(False)   # endpoint without input_fidelity support (400, not billed)
    except Exception as e:
        return False, f"OpenAI request failed: {_short_error(e)}"

    cost = _cost_from_usage(getattr(result, "usage", None))
    _add_session_cost(cost if cost is not None else estimate_cost_usd(up_w, up_h, opts["quality"]))

    data = getattr(result, "data", None) if result is not None else None
    if not data:
        return False, "Empty response from API."
    item = data[0]
    b64 = getattr(item, "b64_json", None)
    url = getattr(item, "url", None)
    try:
        if b64:
            out_bytes = base64.b64decode(b64)
        elif url:
            import urllib.request
            with urllib.request.urlopen(url, timeout=120) as resp:
                out_bytes = resp.read()
        else:
            return False, "No image data in API response."
    except Exception as e:
        return False, f"Failed to get result image: {e}"

    # ── Decode, resize back, save ────────────────────────────────────────
    try:
        result_img = Image.open(io.BytesIO(out_bytes)).convert("RGB")
        if result_img.size != orig_size:
            result_img = result_img.resize(orig_size, Image.Resampling.LANCZOS)
        out_ext = os.path.splitext(out_path)[1].lower()
        fmt = {
            ".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".png": "PNG", ".bmp": "BMP",
            ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF", ".gif": "GIF",
        }.get(out_ext)
        if fmt is None:
            out_path = os.path.splitext(out_path)[0] + ".png"
            if os.path.exists(out_path):
                out_path = _unique_ai_sidecar_path(out_path, "")
            fmt = "PNG"
        save_kw: dict = {"quality": 95} if fmt in ("JPEG", "WEBP") else {}
        result_img.save(out_path, format=fmt, **save_kw)
    except Exception as e:
        return False, f"Could not process API result image: {e}"
    return True, out_path


def _short_error(e: Exception) -> str:
    """Readable one-line message for common OpenAI failures."""
    msg = str(e) or e.__class__.__name__
    name = e.__class__.__name__
    low = msg.lower()
    if name in ("APITimeoutError",) or "timed out" in low:
        return "the request timed out — please try again"
    if name == "AuthenticationError" or "invalid_api_key" in low or "incorrect api key" in low:
        return "the API key was rejected — check it in Settings"
    if name == "RateLimitError" or "insufficient_quota" in low:
        return "rate limit or quota reached on your OpenAI account"
    if name == "APIConnectionError":
        return "could not reach OpenAI — check your internet connection"
    if "must be verified" in low or "organization must be verified" in low:
        return "your OpenAI organisation must be verified to use gpt-image-1"
    if "safety" in low or "moderation" in low:
        return "OpenAI's safety system rejected this image"
    return msg[:400]


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