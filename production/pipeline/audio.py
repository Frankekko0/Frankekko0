"""Final audio mix: processed narration + per-act music (ducked under speech) + SFX,
normalised to -14 LUFS / -1.5 dBTP for YouTube. Writes build/mix.wav (48 kHz stereo).
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import shots as S  # noqa: E402  (word-anchored times)

SR = 48000
BUILD = ROOT / "build"
MUSIC = ROOT / "assets" / "music"

# (file, offset seconds into the track, target RMS dBFS before ducking)
CUES = {
    "ACT0": ("act0_echoes_of_time_v2.mp3", 10, -21),
    "ACT1": ("act1_inspired.mp3", 0, -23),
    "ACT2": ("act2_impact_prelude.mp3", 0, -23),
    "ACT3": ("act3_rising_tide.mp3", 0, -24),
    "ACT4": ("act4_static_motion.mp3", 0, -22),
    "ACT5": ("act5_lightless_dawn.mp3", 170, -22),
    "ACT6": ("act6_interloper.mp3", 70, -22.5),
    "ACT7": ("act7_heartbreaking.mp3", 0, -25),
    "ACT8": ("act8_despair_and_triumph.mp3", 118, -21),
}
DUCK_DB = 14.0         # music reduction while the narrator speaks (keeps the voice >= ~12 LU above the bed)
TAIL = ("act0_echoes_of_time_v2.mp3", 10, -23)   # the opening theme returns under the end card
XFADE = 1.3


def load_audio(path, mono=False):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1" if mono else "2", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32).copy()
    return x if mono else x.reshape(-1, 2)


def db(x):
    return 10 ** (x / 20)


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-12)


def peaking(f0, gain_db, q):
    a = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / SR
    al = np.sin(w0) / (2 * q)
    b = [1 + al * a, -2 * np.cos(w0), 1 - al * a]
    aa = [1 + al / a, -2 * np.cos(w0), 1 - al / a]
    return np.array(b) / aa[0], np.array(aa) / aa[0]


def follower(x, attack, release):
    """Peak-ish envelope follower on |x| (per-sample, vectorised in blocks)."""
    env = np.abs(x)
    a_att, a_rel = np.exp(-1 / (attack * SR)), np.exp(-1 / (release * SR))
    # block-wise approximation for speed: 1 ms blocks
    blk = 48
    n = len(env) // blk
    e = env[: n * blk].reshape(n, blk).max(1)
    out = np.zeros(n)
    prev = 0.0
    a_att_b, a_rel_b = a_att ** blk, a_rel ** blk
    for i in range(n):
        v = e[i]
        k = a_att_b if v > prev else a_rel_b
        prev = k * prev + (1 - k) * v
        out[i] = prev
    return np.repeat(out, blk)[: len(x)] if len(out) else np.zeros(len(x))


def process_voice(v):
    sos = signal.butter(2, 75, btype="high", fs=SR, output="sos")
    v = signal.sosfilt(sos, v)
    for f0, g, q in ((160, 1.8, 0.8), (3200, 2.2, 0.9), (7500, -1.5, 1.2)):
        b, a = peaking(f0, g, q)
        v = signal.lfilter(b, a, v)
    env = follower(v, 0.005, 0.12)
    env = np.pad(env, (0, len(v) - len(env)), mode="edge")
    lvl = 20 * np.log10(env + 1e-9)
    thr, ratio = -20.0, 2.6
    gr = np.where(lvl > thr, (lvl - thr) * (1 - 1 / ratio), 0.0)
    v = v * db(-gr)
    # small room: short decaying noise IR at low level so the voice isn't bone-dry
    rng = np.random.default_rng(3)
    L = int(0.32 * SR)
    ir = rng.standard_normal(L) * np.exp(-np.arange(L) / (0.06 * SR))
    ir = signal.sosfilt(signal.butter(2, 3500, fs=SR, output="sos"), ir)
    ir /= np.sqrt(np.sum(ir ** 2))
    wet = signal.fftconvolve(v, ir)[: len(v)]
    v = v + wet * 0.07
    return v / np.max(np.abs(v)) * db(-3)


def main():
    cues = json.loads((BUILD / "cues.json").read_text())
    dur = cues["duration"]
    N = int(dur * SR)

    # ---- narration
    voice = load_audio(BUILD / "narration.wav", mono=True)
    voice = process_voice(voice)[:N]
    voice = np.pad(voice, (0, N - len(voice)))

    # ---- music bed, act by act, crossfaded at the chapter cards
    E, L = S.E, S.L
    starts = {"ACT0": 0.0, "ACT1": E("ACT0_10") + 1.45, "ACT2": E("ACT1_11") + 0.25, "ACT3": E("ACT2_19") + 0.25,
              "ACT4": E("ACT3_08") + 0.25, "ACT5": E("ACT4_15") + 0.25, "ACT6": E("ACT5_11") + 0.25,
              "ACT7": E("ACT6_15") + 0.25, "ACT8": E("ACT7_09") + 0.25}
    order = list(starts)
    music = np.zeros((N, 2))
    for i, act in enumerate(order):
        f, off, target = CUES[act]
        a0 = starts[act]
        a1 = starts[order[i + 1]] + XFADE if i + 1 < len(order) else dur
        trk = load_audio(MUSIC / f)
        seg_len = int((a1 - a0) * SR)
        o = int(off * SR)
        seg = trk[o:o + seg_len]
        while len(seg) < seg_len:              # loop with a short crossfade if the cue runs out
            cf = int(1.5 * SR)
            nxt = trk[int(5 * SR):int(5 * SR) + seg_len - len(seg) + cf]
            ramp = np.linspace(0, 1, cf)[:, None]
            seg = np.concatenate([seg[:-cf], seg[-cf:] * (1 - ramp) + nxt[:cf] * ramp, nxt[cf:]])
        seg = seg[:seg_len] * db(target - rms_db(seg))
        fin = int((0.25 if i == 0 else XFADE * 0.8) * SR)
        fout = int(XFADE * SR) if i + 1 < len(order) else int(2.5 * SR)
        envl = np.ones(len(seg))
        envl[:fin] = np.sin(np.linspace(0, np.pi / 2, fin)) ** 2
        envl[-fout:] *= np.cos(np.linspace(0, np.pi / 2, fout)) ** 2
        s0 = int(a0 * SR)
        music[s0:s0 + len(seg)] += seg * envl[:, None]
        print(f"{act}: {f} @{off}s  {a0:6.1f}-{a1:6.1f}s")

    # ---- automation: duck under speech, drop out at the big reveals
    venv = follower(voice, 0.03, 0.45)
    venv = np.pad(venv, (0, N - len(venv)), mode="edge")
    speaking = np.clip((20 * np.log10(venv + 1e-9) + 42) / 12, 0, 1)
    gain_db = -DUCK_DB * speaking
    w = S.w
    holes = [  # (start, end, depth dB)
        (w("ACT0_08", "less") - 0.1, E("ACT0_08") + 0.9, -22),
        (L("ACT2_08") + 0.9, E("ACT2_08") + 0.5, -16),
        (L("ACT6_12") + 1.3, E("ACT6_12") + 0.9, -20),
        (w("ACT6_13", "chapter") - 0.05, w("ACT6_13", "chapter") + 0.6, -10),
        (L("ACT8_08") - 0.3, E("ACT8_08") - 0.4, -5),
        (E("ACT8_08") - 0.4, dur, -60),
    ]
    tt = np.arange(N) / SR
    for a, b, depth in holes:
        ramp_in = np.clip((tt - a) / 0.25, 0, 1)
        ramp_out = np.clip((b - tt) / 0.5, 0, 1) if b < dur else 1.0
        gain_db += depth * np.minimum(ramp_in, ramp_out)
    music *= db(gain_db)[:, None]

    # ---- end card: the opening theme comes back after "It had weeks."
    f, off, target = TAIL
    a0 = S.OC - 0.3
    seg_len = int((dur - a0) * SR)
    trk = load_audio(MUSIC / f)
    seg = trk[int(off * SR):int(off * SR) + seg_len]
    seg = np.pad(seg, ((0, seg_len - len(seg)), (0, 0)))
    seg = seg * db(target - rms_db(seg[: int(10 * SR)]))
    env = np.ones(seg_len)
    fi, fo = int(1.8 * SR), int(3.5 * SR)
    env[:fi] = np.sin(np.linspace(0, np.pi / 2, fi)) ** 2
    env[-fo:] *= np.cos(np.linspace(0, np.pi / 2, fo)) ** 2
    music[int(a0 * SR):int(a0 * SR) + seg_len] += seg * env[:, None]

    # ---- sound effects
    fx = np.zeros((N, 2))
    cache = {}
    for t0, name, g in cues["sfx"]:
        if name not in cache:
            cache[name], _ = sf.read(BUILD / "sfx" / f"{name}.wav")
        x = cache[name] * db(g - 7)
        s0 = int(t0 * SR)
        if s0 < 0 or s0 >= N:
            continue
        x = x[: N - s0]
        fx[s0:s0 + len(x)] += x

    mix = voice[:, None] * np.array([1.0, 1.0]) + music + fx
    peak = np.max(np.abs(mix))
    if peak > 0.98:
        mix *= 0.98 / peak
    tmp = BUILD / "mix_raw.wav"
    sf.write(tmp, mix.astype(np.float32), SR, subtype="FLOAT")
    sf.write(BUILD / "stem_music.wav", music.astype(np.float32), SR, subtype="FLOAT")

    # ---- two-pass loudness normalisation to -14 LUFS, -1 dBTP
    p1 = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(tmp), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    m = json.loads(p1[p1.rfind("{"):p1.rfind("}") + 1])
    af = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-af", af, "-ar", str(SR), "-c:a", "pcm_s24le", str(BUILD / "mix.wav")], check=True)
    print(f"mix: measured {m['input_i']} LUFS -> -14 LUFS; duration {dur:.1f}s")


if __name__ == "__main__":
    main()
