"""Soft ambient music bed, synthesized locally (no samples, no licences).

    docker run --rm -v "$PWD":/work pitchmirror-kokoro python tts/music.py 60 assets/audio/music-60.wav

Warm detuned pads over a slow four-chord loop, sparse piano-like plucks that enter after
the opening, a low root from the middle, and a convolution reverb. It fades in over 2.5 s
and out over the last 4 s.
"""
import sys

import numpy as np
import soundfile as sf

SR = 44100
RNG = np.random.default_rng(7)


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


# D major: Dmaj9 – Bm11 – Gmaj7 – Asus4 (MIDI notes)
CHORDS = [
    [50, 57, 62, 66, 69, 76],
    [47, 54, 59, 62, 66, 73],
    [43, 50, 55, 59, 62, 71],
    [45, 52, 57, 62, 64, 69],
]
CHORD_S = 5.0


def pad_voice(f, n, t):
    # Two slightly detuned sines + a quiet octave, with slow vibrato.
    vib = 1 + 0.0015 * np.sin(2 * np.pi * 0.21 * t)
    s = np.sin(2 * np.pi * f * vib * t) + np.sin(2 * np.pi * f * 1.003 * t + 1.3)
    s += 0.18 * np.sin(2 * np.pi * 2 * f * t + 0.7)
    return s


def envelope(n, attack, release):
    env = np.ones(n)
    a, r = min(int(attack * SR), n // 2), min(int(release * SR), n // 2)
    if a:
        env[:a] = np.linspace(0, 1, a) ** 2
    if r:
        env[-r:] *= np.linspace(1, 0, r) ** 2
    return env


def pluck(f, dur):
    t = np.arange(int(dur * SR)) / SR
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
    return s * np.exp(-t * 2.6) * np.minimum(1, t / 0.006)


def reverb(x, seconds=3.2, mix=0.32):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = RNG.standard_normal(n) * np.exp(-t * 3.0 / seconds * 2.3)
    ir[: int(0.012 * SR)] = 0
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return (1 - mix) * x + mix * wet * 0.6


def lowpass(x, cutoff):
    # Gentle one-pole low-pass, vectorised with an IIR via cumulative trick per block.
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(0, len(x), 4096):
        blk = x[i:i + 4096]
        out = np.empty_like(blk)
        for j, v in enumerate(blk):
            acc = (1 - a) * v + a * acc
            out[j] = acc
        y[i:i + 4096] = out
    return y


def render(total):
    n = int(total * SR)
    out = np.zeros(n)
    t_all = np.arange(n) / SR
    # Pads: each chord overlaps the next by 1.5 s for a smooth crossfade.
    k = 0
    start = 0.0
    while start < total:
        chord = CHORDS[k % len(CHORDS)]
        seg = min(CHORD_S + 1.5, total - start)
        i0, i1 = int(start * SR), int(min(total, start + seg) * SR)
        t = t_all[i0:i1]
        s = sum(pad_voice(midi(m), i1 - i0, t) * (0.55 if m < 52 else 0.32) for m in chord)
        out[i0:i1] += s * envelope(i1 - i0, 1.6, 1.6) * 0.06
        start += CHORD_S
        k += 1
    # Plucks: sparse arpeggio, enter after 6 s, thin out in the last 8 s.
    step = CHORD_S / 4
    tt, k = 6.0, 0
    while tt < total - 6:
        chord = CHORDS[int(tt // CHORD_S) % len(CHORDS)]
        note = chord[[3, 4, 5, 4][k % 4]] + 12
        if RNG.random() > 0.25:
            p = pluck(midi(note), 2.4) * 0.05 * (0.8 + 0.4 * RNG.random())
            i0 = int(tt * SR)
            i1 = min(n, i0 + len(p))
            out[i0:i1] += p[: i1 - i0]
        tt += step
        k += 1
    # Low root from a third of the way in, for a gentle build.
    root_in = total * 0.33
    for c in range(int(root_in // CHORD_S), int(total // CHORD_S) + 1):
        i0 = int(max(root_in, c * CHORD_S) * SR)
        i1 = min(n, int((c + 1) * CHORD_S * SR) + int(0.8 * SR))
        if i1 <= i0:
            continue
        f = midi(CHORDS[c % len(CHORDS)][0] - 12)
        t = t_all[i0:i1]
        out[i0:i1] += np.sin(2 * np.pi * f * t) * envelope(i1 - i0, 1.2, 1.0) * 0.05
    out = lowpass(out, 3800)
    out = reverb(out)
    # Master fades.
    out *= envelope(n, 2.5, 4.0)
    out /= np.max(np.abs(out)) + 1e-9
    out *= 0.5
    # Slight stereo width: decorrelate with a short delay on the right channel.
    d = int(0.011 * SR)
    right = np.concatenate([np.zeros(d), out[:-d]])
    return np.stack([out * 0.98 + right * 0.02, right * 0.98 + out * 0.02], axis=1)


if __name__ == "__main__":
    seconds = float(sys.argv[1])
    sf.write(sys.argv[2], render(seconds), SR, subtype="PCM_16")
    print("wrote", sys.argv[2])
