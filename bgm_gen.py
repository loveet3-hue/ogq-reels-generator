# -*- coding: utf-8 -*-
"""
기본 BGM 생성기 — 외부 음원 없이 numpy로 직접 합성(저작권 문제 없음).
분위기(mood)별로 짧은 루프를 만들고 WAV로 저장한다. 영상 길이에 맞춰 ffmpeg가 반복/페이드 처리.

  python3 bgm_gen.py            # 4곡 모두 output/bgm/ 에 생성
  python3 bgm_gen.py chill      # 한 곡만
"""
import math, os, wave, tempfile, hashlib
from typing import List, Tuple

import numpy as np

SR = 44100
VERSION = "v2"

MOODS = {
    "cute":   "밝은 통통 (기본)",
    "upbeat": "신나는 챌린지",
    "chill":  "잔잔한 로파이",
    "funny":  "장난스러운",
}


# ──────────────────────────────────────────────────────────────
# 기본 신디
# ──────────────────────────────────────────────────────────────
def midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def adsr(n: int, a=0.01, d=0.08, s=0.7, r=0.08) -> np.ndarray:
    a_n, d_n, r_n = int(SR * a), int(SR * d), int(SR * r)
    s_n = max(0, n - a_n - d_n - r_n)
    env = np.concatenate([
        np.linspace(0, 1, max(1, a_n)),
        np.linspace(1, s, max(1, d_n)),
        np.full(s_n, s),
        np.linspace(s, 0, max(1, r_n)),
    ])
    return env[:n] if len(env) >= n else np.pad(env, (0, n - len(env)))


def osc(kind: str, freq: float, n: int, detune=0.0) -> np.ndarray:
    t = np.arange(n) / SR
    ph = 2 * math.pi * freq * (1 + detune) * t
    if kind == "sine":
        return np.sin(ph)
    if kind == "square":
        return np.sign(np.sin(ph)) * 0.6
    if kind == "triangle":
        return (2 / math.pi) * np.arcsin(np.sin(ph))
    if kind == "saw":
        return 2 * ((freq * t) % 1.0) - 1
    if kind == "pluck":  # 삼각+사인 섞은 부드러운 소리
        return 0.6 * (2 / math.pi) * np.arcsin(np.sin(ph)) + 0.4 * np.sin(ph)
    return np.sin(ph)


def lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    """FFT 기반 버터워스풍 저역통과."""
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    H = 1 / np.sqrt(1 + (f / cutoff) ** (2 * order))
    return np.fft.irfft(X * H, n)


def highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    with np.errstate(divide="ignore"):
        H = 1 / np.sqrt(1 + (cutoff / np.maximum(f, 1e-6)) ** (2 * order))
    return np.fft.irfft(X * H, n)


def kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 40)
    ph = 2 * math.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 18) * 1.0


def snare(n: int, rng: np.random.Generator, soft=False) -> np.ndarray:
    t = np.arange(n) / SR
    noise = rng.uniform(-1, 1, n) * np.exp(-t * (40 if soft else 22))
    tone = np.sin(2 * math.pi * 190 * t) * np.exp(-t * 30)
    x = highpass(noise, 1500) * (0.5 if soft else 0.8) + tone * 0.4
    return x


def hat(n: int, rng: np.random.Generator, open_=False) -> np.ndarray:
    t = np.arange(n) / SR
    noise = rng.uniform(-1, 1, n) * np.exp(-t * (12 if open_ else 60))
    return highpass(noise, 7000) * 0.5


# ──────────────────────────────────────────────────────────────
# 작곡
# ──────────────────────────────────────────────────────────────
# 코드 진행 (반음 오프셋, 루트 기준) : I  V  vi  IV 등
CHORDS = {
    "I": [0, 4, 7], "ii": [2, 5, 9], "iii": [4, 7, 11], "IV": [5, 9, 12], "V": [7, 11, 14], "vi": [9, 12, 16],
    "Imaj7": [0, 4, 7, 11], "IVmaj7": [5, 9, 12, 16], "vi7": [9, 12, 16, 19], "V7": [7, 11, 14, 17], "ii7": [2, 5, 9, 12],
}
PENTA = [0, 2, 4, 7, 9]  # 메이저 펜타토닉


class Song:
    def __init__(self, mood: str):
        self.mood = mood
        cfg = {
            "cute":   dict(bpm=118, key=60, prog=["I", "V", "vi", "IV"] * 2, lead="square", pad="triangle", swing=0.0,
                           lead_oct=1, hat_div=2, snare_soft=False, lp=9000, density=0.72, arp=False),
            "upbeat": dict(bpm=142, key=62, prog=["vi", "IV", "I", "V"] * 2, lead="saw", pad="saw", swing=0.0,
                           lead_oct=1, hat_div=4, snare_soft=False, lp=11000, density=0.85, arp=True),
            "chill":  dict(bpm=84, key=58, prog=["Imaj7", "vi7", "ii7", "V7", "Imaj7", "IVmaj7", "ii7", "V7"], lead="pluck", pad="triangle", swing=0.18,
                           lead_oct=0, hat_div=2, snare_soft=True, lp=5000, density=0.5, arp=False),
            "funny":  dict(bpm=132, key=65, prog=["I", "IV", "V", "I", "I", "IV", "V", "V"], lead="square", pad="pluck", swing=0.12,
                           lead_oct=1, hat_div=2, snare_soft=False, lp=8000, density=0.9, arp=False),
        }[mood]
        self.__dict__.update(cfg)
        self.beat = 60.0 / self.bpm
        self.bars = len(self.prog)
        self.total = self.bars * 4 * self.beat
        self.n = int(self.total * SR)
        self.rng = np.random.default_rng({"cute": 11, "upbeat": 22, "chill": 33, "funny": 44}[mood])

    def place(self, buf: np.ndarray, x: np.ndarray, t0: float, gain: float):
        i = int(t0 * SR)
        if i >= len(buf):
            return
        m = min(len(x), len(buf) - i)
        buf[i:i + m] += x[:m] * gain

    def tpos(self, bar: int, step16: int) -> float:
        """16분음표 그리드 → 초 (스윙 적용)"""
        t = (bar * 16 + step16) * self.beat / 4
        if self.swing and step16 % 2 == 1:
            t += self.swing * self.beat / 4
        return t

    def render(self) -> np.ndarray:
        n = self.n
        lead, pad, bass, drums = (np.zeros(n) for _ in range(4))
        rng = self.rng
        # 패드 & 베이스
        for b, ch in enumerate(self.prog):
            notes = CHORDS[ch]
            t0 = self.tpos(b, 0); ln = int(4 * self.beat * SR)
            env = adsr(ln, 0.05, 0.3, 0.8, 0.25)
            for k, off in enumerate(notes):
                f = midi(self.key + off)
                x = (osc(self.pad, f, ln, 0.003) + osc(self.pad, f, ln, -0.003)) * 0.5 * env
                self.place(pad, x, t0, 0.5 / len(notes))
            # 베이스: 1,3박 루트 + 2,4박 5도/옥타브
            root = self.key - 12 + notes[0]
            pattern = [(0, root), (4, root), (6, root + 7), (8, root), (12, root), (14, root + (12 if self.mood != "chill" else 7))]
            if self.mood == "chill":
                pattern = [(0, root), (6, root), (8, root), (14, root + 7)]
            for st, note in pattern:
                ln2 = int(self.beat * SR * (0.9 if self.mood != "chill" else 1.4))
                x = osc("sine", midi(note), ln2) * 0.7 + osc("triangle", midi(note), ln2) * 0.3
                self.place(bass, x * adsr(ln2, 0.005, 0.1, 0.6, 0.1), self.tpos(b, st), 0.9)
        # 멜로디: 펜타토닉 랜덤워크, 8분음표 그리드
        cur = 2
        base = self.key + 12 * self.lead_oct
        for b in range(self.bars):
            chord = CHORDS[self.prog[b]]
            for st in range(0, 16, 2):
                if b == self.bars - 1 and st >= 12:
                    break
                if rng.random() > self.density:
                    continue
                if st % 8 == 0:  # 강박엔 코드톤으로
                    cands = [i for i, p in enumerate(PENTA) if p % 12 in [c % 12 for c in chord]]
                    if cands:
                        cur = min(cands, key=lambda i: abs(i - cur))
                else:
                    cur = int(np.clip(cur + rng.choice([-2, -1, -1, 0, 1, 1, 2]), 0, len(PENTA) + 2))
                octv, idx = divmod(cur, len(PENTA))
                note = base + PENTA[idx] + 12 * octv
                hold = 2 if rng.random() < 0.7 else 4
                ln = int(hold * self.beat / 4 * SR * 0.95)
                x = osc(self.lead, midi(note), ln) * adsr(ln, 0.008, 0.06, 0.6, 0.06)
                if self.arp and st % 4 == 2:
                    x2 = osc(self.lead, midi(note + 12), ln // 2) * adsr(ln // 2, 0.005, 0.05, 0.5, 0.04)
                    self.place(lead, x2, self.tpos(b, st) + self.beat / 4, 0.4)
                self.place(lead, x, self.tpos(b, st), 0.55)
        # 드럼
        kick_n = int(0.25 * SR); sn_n = int(0.22 * SR); hat_n = int(0.08 * SR)
        K, S, Ho = kick(kick_n), snare(sn_n, rng, self.snare_soft), hat(hat_n, rng, True)
        for b in range(self.bars):
            for st in range(16):
                t0 = self.tpos(b, st)
                if st in (0, 8) or (self.mood in ("upbeat", "funny") and st == 10) or (self.mood == "cute" and st == 11 and b % 2 == 1):
                    self.place(drums, K, t0, 0.95)
                if st in (4, 12):
                    self.place(drums, S, t0, 0.55 if self.snare_soft else 0.7)
                if st % (16 // (self.hat_div * 2)) == 0:
                    self.place(drums, hat(hat_n, rng), t0, 0.28 if st % 4 == 0 else 0.16)
                if st == 14 and b % 4 == 3:
                    self.place(drums, Ho, t0, 0.25)
        lead = lowpass(lead, self.lp)
        pad = lowpass(pad, 2500)
        mixL = lead * 0.30 + pad * 0.42 + bass * 0.55 + drums * 0.6
        mixR = lead * 0.36 + pad * 0.36 + bass * 0.55 + drums * 0.6
        # 살짝 리버브(딜레이 합)
        d = int(0.11 * SR)
        mixL[d:] += mixR[:-d] * 0.12; mixR[d:] += mixL[:-d] * 0.12
        st = np.stack([mixL, mixR], axis=1)
        st = np.tanh(st * 1.3)
        st /= max(1e-6, np.abs(st).max()) / 0.85
        return st


def write_wav(path: str, data: np.ndarray):
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def get_bgm(mood: str = "cute") -> str:
    """분위기별 루프 WAV 경로(캐시). 없으면 생성."""
    mood = mood if mood in MOODS else "cute"
    path = os.path.join(tempfile.gettempdir(), f"ogq_bgm_{mood}_{VERSION}.wav")
    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        write_wav(path, Song(mood).render())
    return path


if __name__ == "__main__":
    import sys
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "bgm")
    os.makedirs(out, exist_ok=True)
    for m in (sys.argv[1:] or MOODS):
        s = Song(m); data = s.render()
        p = os.path.join(out, f"bgm_{m}.wav"); write_wav(p, data)
        print(f"{m:7s} {MOODS[m]:12s} {s.bpm}bpm {s.total:.1f}s peak={np.abs(data).max():.2f} rms={np.sqrt((data**2).mean()):.3f} → {p}")
