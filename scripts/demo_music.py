"""Background music for the console walkthrough, synthesised rather than sampled.

A slow, quiet loop in A minor (Am - F - C - G at 84 bpm): a soft pad, a sine
sub under each bar, a plucked arpeggio that comes in after the opening, and a
little reverb. Everything is generated here with numpy, so the track carries no
licence and needs no download.

    uv run --with numpy python scripts/demo_music.py out.wav 150
"""

from __future__ import annotations

import sys
import wave

import numpy as np

RATE = 44100
BPM = 84
BEAT = 60 / BPM
BAR = 4 * BEAT
# two bars per chord; MIDI notes, root first
CHORDS = [
    (57, 60, 64),  # A minor
    (53, 57, 60),  # F major
    (48, 52, 55),  # C major
    (55, 59, 62),  # G major
]


def hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def envelope(n: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(n)
    a, r = min(n, int(attack * RATE)), min(n, int(release * RATE))
    if a:
        env[:a] = np.linspace(0, 1, a) ** 2
    if r:
        env[-r:] *= np.linspace(1, 0, r) ** 2
    return env


def pad_voice(freq: float, seconds: float, rng: np.random.Generator) -> np.ndarray:
    """Three slightly detuned voices of a soft, band-limited saw."""
    t = np.arange(int(seconds * RATE)) / RATE
    out = np.zeros_like(t)
    for detune in (-0.07, 0.0, 0.06):
        f = freq * 2 ** (detune / 12)
        phase = rng.uniform(0, 2 * np.pi)
        for k in range(1, 7):  # few harmonics: warm, nothing bright
            out += np.sin(2 * np.pi * f * k * t + phase * k) / k ** 1.6
    return out / 3


def pluck(freq: float, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    tone = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) * np.exp(-t * 9)
    return tone * np.exp(-t * 3.2) * envelope(len(t), 0.004, 0.05)


def reverb(signal: np.ndarray, seconds: float, mix: float, rng: np.random.Generator) -> np.ndarray:
    """Convolve with a decaying noise tail: a plain, smooth room."""
    n = int(seconds * RATE)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / RATE * 4.2)
    ir[: int(0.012 * RATE)] = 0  # a short pre-delay keeps the dry note clear
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(signal) + n)))
    wet = np.fft.irfft(np.fft.rfft(signal, size) * np.fft.rfft(ir, size), size)[: len(signal)]
    return signal * (1 - mix) + wet * mix


def compose(seconds: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = int((seconds + 1) * RATE)
    left, right = np.zeros(total), np.zeros(total)
    chord_len = 2 * BAR
    arp_from = 2 * chord_len  # the arpeggio comes in after the first two chords

    start = 0.0
    index = 0
    while start < seconds:
        notes = CHORDS[index % len(CHORDS)]
        at = int(start * RATE)

        # pad: the chord an octave below, overlapping the next chord a little
        length = chord_len + 1.2
        pad = sum(pad_voice(hz(n - 12), length, rng) for n in notes)
        pad *= envelope(len(pad), 1.4, 1.6) * 0.09
        end = min(total, at + len(pad))
        left[at:end] += pad[: end - at] * 1.0
        right[at:end] += pad[: end - at] * 0.92

        # sub: the root, once per bar
        for bar in range(2):
            b = at + int(bar * BAR * RATE)
            if b >= total:
                break
            sub_t = np.arange(int(BAR * RATE)) / RATE
            sub = np.sin(2 * np.pi * hz(notes[0] - 24) * sub_t) * np.exp(-sub_t * 1.1) * 0.16
            sub *= envelope(len(sub), 0.02, 0.4)
            e = min(total, b + len(sub))
            left[b:e] += sub[: e - b]
            right[b:e] += sub[: e - b]

        # arpeggio: chord tones up an octave in eighths, alternating sides
        if start >= arp_from:
            pattern = [notes[0], notes[1], notes[2], notes[1] + 12, notes[2], notes[1], notes[0] + 12, notes[2]]
            for step in range(16):
                b = at + int(step * BEAT / 2 * RATE)
                if b >= total:
                    break
                note = pattern[step % len(pattern)] + 12
                p = pluck(hz(note), 1.6) * (0.055 if step % 4 else 0.07)
                e = min(total, b + len(p))
                pan = 0.35 if step % 2 else -0.35
                left[b:e] += p[: e - b] * (1 - pan)
                right[b:e] += p[: e - b] * (1 + pan)
                # a dotted-eighth echo, quieter and on the other side
                d = b + int(BEAT * 0.75 * RATE)
                if d < total:
                    e = min(total, d + len(p))
                    left[d:e] += p[: e - d] * 0.35 * (1 + pan)
                    right[d:e] += p[: e - d] * 0.35 * (1 - pan)

        start += chord_len
        index += 1

    left = reverb(left, 2.6, 0.32, rng)
    right = reverb(right, 2.6, 0.32, rng)
    stereo = np.stack([left, right], axis=1)[: int(seconds * RATE)]
    stereo *= envelope(len(stereo), 2.5, 4.0)[:, None]
    return stereo / np.max(np.abs(stereo)) * 0.5  # about -6 dBFS; ffmpeg sets the final level


def write(path: str, seconds: float) -> None:
    audio = compose(seconds)
    with wave.open(path, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((audio * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    write(sys.argv[1], float(sys.argv[2]))
