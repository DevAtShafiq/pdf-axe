# -*- coding: utf-8 -*-
"""
sfx.py — sound effects and a music bed, synthesized from scratch (numpy).

No samples or third-party recordings are used, so there is nothing to
license. All functions return float32 mono arrays at SR (48 kHz) in -1..1.

    click()     soft UI click (mouse button)
    key()       quiet key tick (typing)
    whoosh()    airy sweep for chapter changes
    pop()       small pop when a callout appears
    chime()     two-note bell for "done"
    music(sec)  calm chord pad + light plucks, loopable, any length
    duck(music, voice)  lowers the music under the narration
"""
from __future__ import annotations

import numpy as np

SR = 48000
_rng = np.random.default_rng(7)


def _t(sec):
    return np.arange(int(sec * SR)) / SR


def _env(n, attack, release, curve=4.0):
    e = np.ones(n, dtype=np.float32)
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    e[:a] = np.linspace(0, 1, a) ** 1.5
    if r < n:
        e[-r:] *= np.exp(-curve * np.linspace(0, 1, r))
    return e


def _lowpass(x, cutoff):
    """One-pole low-pass (cutoff Hz, scalar or per-sample array)."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=np.float64), x.shape)
    a = 1 - np.exp(-2 * np.pi * c / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def _highpass(x, cutoff):
    return x - _lowpass(x, cutoff)


def click():
    n = int(0.045 * SR)
    noise = _rng.standard_normal(n).astype(np.float32)
    body = _highpass(noise, 1800) * np.exp(-np.linspace(0, 18, n))
    tick = np.sin(2 * np.pi * 2300 * _t(0.045)) * np.exp(-np.linspace(0, 30, n))
    out = 0.55 * body + 0.35 * tick
    return (out / np.max(np.abs(out)) * 0.5).astype(np.float32)


def key():
    n = int(0.03 * SR)
    noise = _rng.standard_normal(n).astype(np.float32)
    f = 2600 + _rng.uniform(-500, 500)
    out = _highpass(noise, f) * np.exp(-np.linspace(0, 26, n))
    return (out / np.max(np.abs(out)) * 0.22).astype(np.float32)


def whoosh(sec=0.9):
    n = int(sec * SR)
    noise = _rng.standard_normal(n).astype(np.float32)
    x = np.linspace(0, 1, n)
    cutoff = 300 + 5200 * np.sin(np.pi * x) ** 2
    out = _lowpass(noise, cutoff)
    out = _highpass(out, 180) * (np.sin(np.pi * x) ** 1.6)
    return (out / np.max(np.abs(out)) * 0.42).astype(np.float32)


def pop():
    t = _t(0.12)
    f = 520 + 520 * (t / t[-1])
    ph = 2 * np.pi * np.cumsum(f) / SR
    out = np.sin(ph) * _env(len(t), 0.004, 0.11, 6)
    return (out * 0.28).astype(np.float32)


def chime():
    t = _t(1.3)
    out = np.zeros_like(t)
    for f, delay, amp in ((1318.5, 0.0, 0.5), (1975.5, 0.11, 0.42)):
        d = int(delay * SR)
        tt = t[: len(t) - d]
        tone = (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * 2 * f * tt)) * np.exp(-tt * 4.2)
        out[d:] += amp * tone
    return (out / np.max(np.abs(out)) * 0.30).astype(np.float32)


def music(sec, bpm=92):
    """Calm, unobtrusive bed: soft pad chords and gentle plucked arpeggios
    (C – Am – F – G, major-seventh colour), slow fade in/out."""
    n = int(sec * SR)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    bar = beat * 4
    chords = [[261.6, 329.6, 392.0, 493.9],   # Cmaj7
              [220.0, 261.6, 329.6, 392.0],   # Am7
              [174.6, 220.0, 261.6, 329.6],   # Fmaj7
              [196.0, 246.9, 293.7, 392.0]]   # G6
    out = np.zeros(n, dtype=np.float64)
    nbars = int(np.ceil(sec / bar)) + 1
    for b in range(nbars):
        ch = chords[b % 4]
        s = int(b * bar * SR)
        e = min(n, int((b + 1) * bar * SR) + int(0.6 * SR))
        if s >= n:
            break
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 0.9) * np.exp(-np.maximum(0, tt - bar) * 3)
        pad = sum(np.sin(2 * np.pi * f * 0.5 * tt + i) + 0.6 * np.sin(2 * np.pi * f * 0.5 * 1.003 * tt)
                  for i, f in enumerate(ch))
        out[s:e] += 0.08 * pad * env
        # plucks on 8th notes
        for k in range(8):
            ps = s + int(k * beat / 2 * SR)
            pe = min(n, ps + int(0.9 * SR))
            if ps >= n:
                break
            f = ch[[0, 2, 1, 3, 2, 1, 3, 2][k]] * 2
            pt = t[ps:pe] - t[ps]
            out[ps:pe] += 0.05 * np.sin(2 * np.pi * f * pt) * np.exp(-pt * 6) * (0.8 if k % 2 else 1.0)
    out = _lowpass(out.astype(np.float32), 3200)
    fade = np.ones(n, dtype=np.float32)
    fi, fo = int(2.0 * SR), int(3.0 * SR)
    fade[:fi] = np.linspace(0, 1, fi)
    fade[-fo:] = np.linspace(1, 0, fo)
    out = out * fade
    return (out / (np.max(np.abs(out)) + 1e-9) * 0.9).astype(np.float32)


def duck(bed, voice, amount=0.32, attack=0.15, release=0.6):
    """Music level × (1 − amount) while the narration is speaking."""
    hop = int(0.02 * SR)
    m = len(bed)
    v = np.zeros(m, dtype=np.float32)
    v[: min(m, len(voice))] = np.abs(voice[:m])
    frames = v[: m - m % hop].reshape(-1, hop).max(axis=1)
    active = (frames > 0.02).astype(np.float32)
    g = np.empty_like(active)
    acc = 0.0
    ka, kr = 1 - np.exp(-0.02 / attack), 1 - np.exp(-0.02 / release)
    for i, a in enumerate(active):
        acc += (ka if a > acc else kr) * (a - acc)
        g[i] = acc
    gain = 1 - amount * np.repeat(g, hop)
    gain = np.concatenate([gain, np.full(m - len(gain), gain[-1] if len(gain) else 1.0)])
    return (bed * gain).astype(np.float32)
