# -*- coding: utf-8 -*-
"""
desktop.py — real mouse / keyboard input and window placement on Windows.

Used by record_demo.py so the video shows the real cursor doing real clicks.
Everything here is plain Win32 (ctypes + pywin32); nothing touches files.
"""
from __future__ import annotations

import ctypes
import math
import random
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

try:                                    # physical pixels everywhere
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

# ── SendInput structures ──────────────────────────────────────────────────────
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _IU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _IU)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, data=0):
    i = INPUT(type=0)
    i.u.mi = MOUSEINPUT(0, 0, data, flags, 0, 0)
    return i


def _key(vk=0, scan=0, flags=0):
    i = INPUT(type=1)
    i.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


MOUSE_LEFTDOWN, MOUSE_LEFTUP, MOUSE_RIGHTDOWN, MOUSE_RIGHTUP = 0x2, 0x4, 0x8, 0x10
KEY_UP, KEY_UNICODE = 0x2, 0x4

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
      "delete": 0x2E, "backspace": 0x08, "f2": 0x71, "home": 0x24, "end": 0x23,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "space": 0x20}


# ── mouse ─────────────────────────────────────────────────────────────────────
def cursor():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def move(x, y, duration=None, fps=120):
    """Glide the real cursor to (x, y) on a gentle curve with ease in/out."""
    x0, y0 = cursor()
    dist = math.hypot(x - x0, y - y0)
    if duration is None:
        duration = min(0.95, 0.28 + dist / 1900.0)
    steps = max(2, int(duration * fps))
    # slight arc: offset the midpoint perpendicular to the path
    bend = min(60.0, dist * 0.08) * (1 if random.random() < 0.5 else -1)
    nx, ny = (-(y - y0) / dist, (x - x0) / dist) if dist else (0, 0)
    for i in range(1, steps + 1):
        t = _ease(i / steps)
        arc = math.sin(math.pi * t) * bend
        user32.SetCursorPos(int(round(x0 + (x - x0) * t + nx * arc)),
                            int(round(y0 + (y - y0) * t + ny * arc)))
        time.sleep(duration / steps)
    user32.SetCursorPos(int(round(x)), int(round(y)))


def click(x=None, y=None, right=False, double=False, hold=0.06):
    if x is not None:
        move(x, y)
        time.sleep(0.08)
    down, up = (MOUSE_RIGHTDOWN, MOUSE_RIGHTUP) if right else (MOUSE_LEFTDOWN, MOUSE_LEFTUP)
    for n in range(2 if double else 1):
        _send(_mouse(down))
        time.sleep(hold)
        _send(_mouse(up))
        if double and n == 0:
            time.sleep(0.07)


def drag(x0, y0, x1, y1, duration=1.1):
    move(x0, y0)
    time.sleep(0.12)
    _send(_mouse(MOUSE_LEFTDOWN))
    time.sleep(0.15)
    move(x0 + 12, y0 + 6, duration=0.15)
    move(x1, y1, duration=duration)
    time.sleep(0.25)
    _send(_mouse(MOUSE_LEFTUP))


# ── keyboard ──────────────────────────────────────────────────────────────────
def key(name, *mods):
    """key('z', 'ctrl')  /  key('delete')  /  key('f2')"""
    vk = VK.get(name.lower()) if len(name) > 1 else ord(name.upper())
    seq = [_key(VK[m]) for m in mods] + [_key(vk), _key(vk, flags=KEY_UP)] + \
          [_key(VK[m], flags=KEY_UP) for m in reversed(mods)]
    for s in seq:
        _send(s)
        time.sleep(0.02)


def type_text(text, cps=14.0, jitter=0.35):
    """Type like a person: per-character Unicode input at ~cps chars/second."""
    for ch in text:
        if ch == "\n":
            key("enter")
        else:
            code = ord(ch)
            _send(_key(0, code, KEY_UNICODE), _key(0, code, KEY_UNICODE | KEY_UP))
        time.sleep(max(0.01, random.gauss(1.0 / cps, jitter / cps)))


# ── windows ───────────────────────────────────────────────────────────────────
def find_window(title: str, pid: int | None = None, timeout=30.0):
    import win32gui
    import win32process
    end = time.time() + timeout
    while time.time() < end:
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title:
                if pid is None or win32process.GetWindowThreadProcessId(h)[1] == pid:
                    found.append(h)
        win32gui.EnumWindows(cb, None)
        if found:
            return found[0]
        time.sleep(0.3)
    raise RuntimeError(f"window {title!r} not found")


def frame_bounds(hwnd):
    """Visible window rectangle (without the invisible resize borders)."""
    r = wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                               ctypes.byref(r), ctypes.sizeof(r))
    return r.left, r.top, r.right, r.bottom


def place_window(hwnd, x, y, w, h, topmost=True):
    """Make the *visible* window exactly w×h at (x, y)."""
    import win32con
    import win32gui
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.3)
    insert = win32con.HWND_TOPMOST if topmost else win32con.HWND_NOTOPMOST
    wx, wy, ww, wh = x, y, w, h
    for _ in range(4):
        win32gui.SetWindowPos(hwnd, insert, wx, wy, ww, wh, win32con.SWP_SHOWWINDOW)
        time.sleep(0.25)
        l, t, r, b = frame_bounds(hwnd)
        if (l, t, r - l, b - t) == (x, y, w, h):
            break
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        wx, wy = wx + (x - l), wy + (y - t)
        ww, wh = ww + (w - (r - l)), wh + (h - (b - t))
    return frame_bounds(hwnd)


def focus(hwnd):
    import win32gui
    key("alt")                              # lets SetForegroundWindow through the foreground lock
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def client_origin(hwnd):
    import win32gui
    return win32gui.ClientToScreen(hwnd, (0, 0))


def untopmost(hwnd):
    import win32con
    import win32gui
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
