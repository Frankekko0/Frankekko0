"""Procedurally synthesised sound effects (no third-party samples, no licensing questions).
Writes 48 kHz stereo WAVs to build/sfx/<name>.wav
"""
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

SR = 48000
OUT = Path(__file__).resolve().parent.parent / "build" / "sfx"
rng = np.random.default_rng(7)


def t(d):
    return np.arange(int(d * SR)) / SR


def env_exp(d, tau):
    return np.exp(-t(d) / tau)


def bandpass(x, lo, hi, order=4):
    sos = signal.butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def lowpass(x, f, order=4):
    return signal.sosfilt(signal.butter(order, f, btype="low", fs=SR, output="sos"), x)


def highpass(x, f, order=4):
    return signal.sosfilt(signal.butter(order, f, btype="high", fs=SR, output="sos"), x)


def reverb(x, secs=1.6, wet=0.25, bright=4000):
    ir = rng.standard_normal(int(secs * SR)) * np.exp(-t(secs) / (secs / 5))
    ir = lowpass(ir, bright, 2)
    ir /= np.sqrt(np.sum(ir ** 2))
    y = signal.fftconvolve(x, ir)[: len(x) + int(secs * SR)]
    dry = np.pad(x, (0, len(y) - len(x)))
    return dry * (1 - wet) + y * wet


def stereo(x, width=0.0, pan=0.0):
    """Mono -> stereo with optional decorrelated width and constant-power pan."""
    l, r = x.copy(), x.copy()
    if width:
        d = int(0.011 * SR)
        r = np.concatenate([np.zeros(d), x])[: len(x)] * width + x * (1 - width)
    a = (pan + 1) * np.pi / 4
    return np.stack([l * np.cos(a) * 1.414, r * np.sin(a) * 1.414], 1)


def norm(x, peak=0.9):
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def hit():
    d = 3.0
    f = 62 * np.exp(-t(d) * 1.4) + 30
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(d, 0.9)
    sub = np.sin(2 * np.pi * 38 * t(d)) * env_exp(d, 1.3) * 0.6
    click = lowpass(rng.standard_normal(len(body)), 900) * env_exp(d, 0.02) * 0.9
    x = body + sub + click
    return stereo(norm(reverb(x, 2.2, 0.22, 2500), 0.95), width=0.4)


def whoosh():
    d = 0.9
    n = rng.standard_normal(int(d * SR))
    out = np.zeros_like(n)
    seg = 512
    for i in range(0, len(n) - seg, seg // 2):
        p = i / len(n)
        fc = 300 + 5200 * np.sin(np.pi * p) ** 2
        chunk = bandpass(n[i:i + seg] * np.hanning(seg), max(80, fc * 0.6), min(20000, fc * 1.4), 2)
        out[i:i + seg] += chunk
    amp = np.sin(np.pi * np.clip(t(d) / d, 0, 1)) ** 1.6
    x = out * amp
    pan = np.linspace(-0.7, 0.7, len(x))
    st = np.stack([x * np.cos((pan + 1) * np.pi / 4), x * np.sin((pan + 1) * np.pi / 4)], 1) * 1.4
    return norm(st, 0.8)


def riser():
    d = 2.2
    n = highpass(rng.standard_normal(int(d * SR)), 1500, 2)
    f = 180 * np.exp(t(d) * 1.1)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
    amp = (t(d) / d) ** 2.2
    x = (n * 0.6 + tone) * amp
    x[-int(0.02 * SR):] *= np.linspace(1, 0, int(0.02 * SR))
    return stereo(norm(reverb(x, 1.0, 0.2), 0.75), width=0.5)


def rewind():
    d = 1.9
    x = np.zeros(int(d * SR))
    pos = 0
    k = 0
    while pos < len(x) - 3000:
        L = int(SR * (0.045 + 0.03 * np.sin(k)))
        f0 = 2400 - 1400 * pos / len(x)
        tt = np.arange(L) / SR
        ch = np.sin(2 * np.pi * (f0 * tt + 3000 * tt ** 2)) * np.hanning(L)
        x[pos:pos + L] += ch * 0.5
        pos += int(L * 0.8)
        k += 1
    x += bandpass(rng.standard_normal(len(x)), 2000, 7000) * 0.12
    x *= np.minimum(1, t(d) / 0.1) * np.minimum(1, (d - t(d)) / 0.25)
    return stereo(norm(x, 0.5), width=0.3)


def paper():
    d = 0.8
    x = np.zeros(int(d * SR))
    for _ in range(9):
        s = int(rng.uniform(0, d - 0.12) * SR)
        L = int(rng.uniform(0.03, 0.11) * SR)
        x[s:s + L] += bandpass(rng.standard_normal(L), 1500, 7000, 2) * np.hanning(L) * rng.uniform(0.3, 1)
    return stereo(norm(x, 0.6), width=0.6)


def key_click(strength=1.0):
    L = int(0.05 * SR)
    n = rng.standard_normal(L) * np.exp(-np.arange(L) / (0.004 * SR))
    body = bandpass(n, 1200, 5000, 2) + 0.6 * bandpass(n, 300, 900, 2)
    thunk = np.sin(2 * np.pi * 180 * np.arange(L) / SR) * np.exp(-np.arange(L) / (0.008 * SR)) * 0.4
    return (body + thunk) * strength


def type_burst(n=7, d=0.55):
    x = np.zeros(int((d + 0.1) * SR))
    times = np.sort(rng.uniform(0, d, n))
    for tt in times:
        c = key_click(rng.uniform(0.6, 1.0))
        s = int(tt * SR)
        x[s:s + len(c)] += c
    return stereo(norm(reverb(x, 0.4, 0.12), 0.7), width=0.2)


def typelong():
    return type_burst(38, 3.9)


def stamp():
    d = 0.9
    thud = np.sin(2 * np.pi * 85 * t(d)) * env_exp(d, 0.07)
    slap = bandpass(rng.standard_normal(int(d * SR)), 400, 4000, 2) * env_exp(d, 0.015)
    x = thud * 1.0 + slap * 0.8
    return stereo(norm(reverb(x, 0.5, 0.15), 0.95), width=0.2)


def shutter():
    d = 0.35
    x = np.zeros(int(d * SR))
    for tt, a in ((0.0, 1.0), (0.07, 0.7)):
        L = int(0.03 * SR)
        c = highpass(rng.standard_normal(L), 2500, 2) * np.exp(-np.arange(L) / (0.003 * SR)) * a
        s = int(tt * SR)
        x[s:s + L] += c
    x += bandpass(rng.standard_normal(len(x)), 800, 3000, 2) * env_exp(d, 0.03) * 0.3
    return stereo(norm(x, 0.6))


def pop():
    d = 0.18
    f = 520 + 380 * (1 - np.exp(-t(d) * 40))
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(d, 0.035)
    return stereo(norm(reverb(x, 0.6, 0.18), 0.5), width=0.3)


def beep():
    d = 0.12
    x = (np.sin(2 * np.pi * 1320 * t(d)) + 0.3 * np.sin(2 * np.pi * 2640 * t(d))) * np.minimum(1, t(d) / 0.004) * env_exp(d, 0.03)
    return stereo(norm(x, 0.35))


def tick():
    d = 0.6
    x = np.zeros(int(d * SR))
    for tt, f in ((0.0, 2600), (0.5, 2100)):
        L = int(0.04 * SR)
        c = bandpass(rng.standard_normal(L), f * 0.7, f * 1.3, 2) * np.exp(-np.arange(L) / (0.004 * SR))
        x[int(tt * SR):int(tt * SR) + L] += c
    return stereo(norm(reverb(x, 0.7, 0.2), 0.6))


def crack():
    d = 1.4
    x = np.zeros(int(d * SR))
    x[:int(0.02 * SR)] += highpass(rng.standard_normal(int(0.02 * SR)), 1500) * 2
    for _ in range(140):
        s = int(abs(rng.normal(0.05, 0.12)) * SR)
        if s < len(x) - 200:
            L = int(rng.uniform(0.0008, 0.004) * SR)
            x[s:s + L] += highpass(rng.standard_normal(L), 2000, 2) * rng.uniform(0.1, 0.8) * np.exp(-s / SR / 0.25)
    thump = np.sin(2 * np.pi * 55 * t(d)) * env_exp(d, 0.25) * 0.8
    return stereo(norm(reverb(x + thump, 1.4, 0.25), 0.9), width=0.5)


def shred():
    d = 1.6
    motor = signal.sawtooth(2 * np.pi * 118 * t(d)) * 0.25 + signal.sawtooth(2 * np.pi * 236.5 * t(d)) * 0.1
    tear = bandpass(rng.standard_normal(int(d * SR)), 1200, 6000, 2) * (0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 9 * t(d))))
    x = lowpass(motor, 2500) + tear * 0.5
    x *= np.minimum(1, t(d) / 0.08) * np.minimum(1, (d - t(d)) / 0.3)
    return stereo(norm(reverb(x, 0.5, 0.15), 0.6), width=0.3)


SOUNDS = {"hit": hit, "whoosh": whoosh, "riser": riser, "rewind": rewind, "paper": paper, "type": type_burst,
          "typelong": typelong, "stamp": stamp, "shutter": shutter, "pop": pop, "beep": beep, "tick": tick,
          "crack": crack, "shred": shred}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in SOUNDS.items():
        x = fn().astype(np.float32)
        sf.write(OUT / f"{name}.wav", x, SR)
        print(f"{name:9s} {len(x) / SR:.2f}s")
