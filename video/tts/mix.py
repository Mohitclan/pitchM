"""Sample-accurate mix: music bed + narration + click/whoosh cues, with ducking.

    python tts/mix.py plan.json out.wav

plan.json: {"duration": 60, "sr": 48000, "music": "...wav", "music_gain_db": -7,
            "duck_db": -9, "vo": [{"file": "...wav", "at": 1.2}], "fx": [{"file": "...wav", "at": 3.0}]}
All inputs must already be 48 kHz (render.mjs prepares them). Output is float WAV; render.mjs
then loudness-normalises it with FFmpeg.
"""
import json
import sys

import numpy as np
import soundfile as sf


def load(path, sr):
    x, r = sf.read(path, dtype="float32", always_2d=True)
    assert r == sr, f"{path}: expected {sr} Hz, got {r}"
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    return x[:, :2]


def place(bus, clip, at, sr):
    i0 = int(round(at * sr))
    i1 = min(len(bus), i0 + len(clip))
    if i1 > i0:
        bus[i0:i1] += clip[: i1 - i0]


def smooth_gate(active, sr, attack=0.08, release=0.6):
    """0/1 activity → smooth 0..1 envelope (fast attack, slow release)."""
    out = np.empty_like(active)
    a = 1 - np.exp(-1 / (attack * sr))
    r = 1 - np.exp(-1 / (release * sr))
    v = 0.0
    # Work in 10 ms blocks for speed, then interpolate.
    blk = sr // 100
    n = len(active) // blk + 1
    vals = np.empty(n)
    for i in range(n):
        target = active[i * blk:(i + 1) * blk].max() if i * blk < len(active) else 0
        k = a * blk if target > v else r * blk
        v += (target - v) * min(1.0, k)
        vals[i] = v
    out[:] = np.interp(np.arange(len(active)), np.arange(n) * blk, vals)
    return out


def main(plan_path, out_path):
    p = json.loads(open(plan_path).read())
    sr = p.get("sr", 48000)
    n = int(round(p["duration"] * sr))
    vo = np.zeros((n, 2), np.float32)
    fx = np.zeros((n, 2), np.float32)
    active = np.zeros(n, np.float32)
    for v in p["vo"]:
        clip = load(v["file"], sr)
        place(vo, clip, v["at"], sr)
        i0 = int(round(v["at"] * sr))
        active[i0:min(n, i0 + len(clip))] = 1
    for f in p.get("fx", []):
        place(fx, load(f["file"], sr), f["at"], sr)
    music = load(p["music"], sr)[:n]
    if len(music) < n:
        music = np.pad(music, ((0, n - len(music)), (0, 0)))
    env = smooth_gate(active, sr)
    gain = 10 ** (p.get("music_gain_db", -7) / 20) * 10 ** (p.get("duck_db", -9) * env / 20)
    mix = vo + fx + music * gain[:, None]
    sf.write(out_path, mix, sr, subtype="FLOAT")
    print("mixed", out_path, f"peak {20 * np.log10(np.abs(mix).max() + 1e-9):.1f} dBFS")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
