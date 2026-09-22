# -*- coding: utf-8 -*-
"""
효과음 합성기 — 외부 파일 없이 numpy로 만든 SFX (저작권 무관).
reels_gen 이 장면에서 일어나는 일(스티커 등장, 카운트다운, 알림, 달리기…)에 맞춰 이벤트를 수집하면
build_track() 이 영상 길이의 스테레오 트랙(WAV)을 만든다.

  python3 sfx_gen.py        # 모든 효과음을 output/sfx/ 에 저장(청취용)
"""
import math, os, wave
from typing import Dict, List, Tuple

import numpy as np

SR = 44100
_rng = np.random.default_rng(7)
_CACHE: Dict[str, np.ndarray] = {}


def _t(sec: float) -> np.ndarray:
    return np.arange(int(SR * sec)) / SR


def _env(n: int, a=0.005, r=0.1) -> np.ndarray:
    a_n = max(1, int(a * SR)); r_n = max(1, int(r * SR))
    e = np.ones(n)
    e[:a_n] = np.linspace(0, 1, a_n)
    if r_n < n:
        e[-r_n:] *= np.linspace(1, 0, r_n)
    return e


def _decay(sec: float, k: float) -> np.ndarray:
    t = _t(sec); return np.exp(-t * k)


def _sine(f, sec, k=None):
    t = _t(sec); x = np.sin(2 * math.pi * f * t)
    return x * (np.exp(-t * k) if k else 1)


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    n = len(x); X = np.fft.rfft(x); f = np.fft.rfftfreq(n, 1 / SR)
    H = 1 / np.sqrt(1 + (lo / np.maximum(f, 1e-6)) ** 4) / np.sqrt(1 + (f / hi) ** 4)
    return np.fft.irfft(X * H, n)


def _noise(sec):
    return _rng.uniform(-1, 1, int(SR * sec))


def _mix(*arrs):
    """길이가 다른 배열을 가장 긴 길이에 맞춰 합산."""
    n = max(len(a) for a in arrs); out = np.zeros(n)
    for a in arrs:
        out[:len(a)] += a
    return out


def _sweep(f0, f1, sec, k=None, wave_="sine"):
    t = _t(sec); f = f0 + (f1 - f0) * (t / max(sec, 1e-6))
    ph = 2 * math.pi * np.cumsum(f) / SR
    x = np.sin(ph) if wave_ == "sine" else np.sign(np.sin(ph)) * 0.6
    return x * (np.exp(-t * k) if k else 1)


def _notes(freqs: List[float], each: float, k=9.0, gap=0.0, wave_="sine", harm=True):
    out = []
    for f in freqs:
        x = _sine(f, each, k)
        if harm:
            x += 0.35 * _sine(f * 2, each, k * 1.4) + 0.15 * _sine(f * 3, each, k * 2)
        if wave_ == "saw":
            t = _t(each); x = (2 * ((f * t) % 1) - 1) * np.exp(-t * k) * 0.6
        out.append(x)
        if gap:
            out.append(np.zeros(int(gap * SR)))
    return np.concatenate(out)


# ──────────────────────────────────────────────────────────────
# 효과음 정의 (모노 → 나중에 스테레오)
# ──────────────────────────────────────────────────────────────
def make(name: str) -> np.ndarray:
    if name in _CACHE:
        return _CACHE[name]
    n = name
    if n == "pop":          # 스티커 등장
        x = _mix(_sweep(500, 900, 0.09, 40) * 0.9, _bandpass(_noise(0.02), 1500, 6000) * 0.3 * _decay(0.02, 150))
    elif n == "blip":
        x = _sine(1400, 0.05, 60)
    elif n == "tick":       # 짧은 클릭
        x = _mix(_bandpass(_noise(0.03), 2000, 9000) * _decay(0.03, 160) * 0.8, _sine(1800, 0.02, 120) * 0.4)
    elif n == "beep":       # 카운트다운
        x = _sine(880, 0.14, 12) * _env(int(0.14 * SR), 0.003, 0.05)
    elif n == "ding":       # 정답/완료
        x = _sine(1318, 0.5, 6) + 0.4 * _sine(2637, 0.5, 9) + 0.2 * _sine(3951, 0.5, 12)
    elif n == "notify":     # 알림 (2음 차임)
        x = np.concatenate([_sine(1046, 0.09, 8) + 0.3 * _sine(2092, 0.09, 12),
                            _sine(1568, 0.42, 7) + 0.3 * _sine(3136, 0.42, 10)])
    elif n == "tok":        # 채팅 전송
        x = _mix(_sine(420, 0.07, 45) * 0.8, _bandpass(_noise(0.03), 500, 3000) * _decay(0.03, 120) * 0.5)
    elif n == "tada":       # 축하 (도-미-솔-도)
        x = np.concatenate([_notes([523, 659, 784], 0.11, 14), _notes([1046], 0.6, 5)])
    elif n == "fanfare":    # 잭팟
        a = _notes([523, 523, 523, 659], 0.12, 8, wave_="saw")
        b = _notes([784], 0.35, 4, wave_="saw") + _notes([1046], 0.35, 4, wave_="saw") * 0.6
        c = _notes([1046], 0.9, 3) + 0.5 * _notes([1318], 0.9, 3) + 0.4 * _notes([1568], 0.9, 3)
        x = np.concatenate([a, b, c])
    elif n == "sparkle":    # 반짝
        fs = [2637, 3136, 3951, 4699, 5274]
        x = np.concatenate([_sine(f, 0.09, 25) * 0.7 for f in fs])
    elif n == "whoosh":
        sec = 0.32; t = _t(sec)
        x = _bandpass(_noise(sec), 400, 4000) * np.sin(math.pi * t / sec) ** 2
    elif n == "thud":
        x = _mix(_sweep(140, 60, 0.18, 22), _bandpass(_noise(0.05), 100, 800) * _decay(0.05, 90) * 0.6)
    elif n == "knock":
        one = _mix(_sweep(220, 120, 0.09, 40) * 0.9, _bandpass(_noise(0.03), 300, 2500) * _decay(0.03, 150) * 0.5)
        x = np.concatenate([one, np.zeros(int(0.12 * SR)), one])
    elif n == "creak":
        sec = 0.55; t = _t(sec)
        f = 180 + 80 * np.sin(t * 9) + 25 * np.sin(t * 41)
        ph = 2 * math.pi * np.cumsum(f) / SR
        x = (2 * ((ph / (2 * math.pi)) % 1) - 1) * 0.35 * _env(len(t), 0.05, 0.15)
        x = _bandpass(x, 150, 2500)
    elif n == "whistle":
        sec = 0.3; t = _t(sec)
        x = np.sin(2 * math.pi * (2200 + 60 * np.sin(t * 60)) * t) * _env(len(t), 0.01, 0.08) * 0.7
    elif n == "cheer":
        sec = 1.3; t = _t(sec)
        x = _bandpass(_noise(sec), 500, 2500) * (0.6 + 0.4 * np.sin(t * 23)) * np.sin(math.pi * t / sec) ** 0.5
    elif n == "drumroll":
        sec = 0.9; hits = int(sec * 28); step = int(SR * sec / hits)
        x = np.zeros(int(sec * SR))
        for i in range(hits):
            h = _bandpass(_noise(0.04), 1200, 6000) * _decay(0.04, 100) * (0.4 + 0.6 * i / hits)
            s = i * step; m = min(len(h), len(x) - s); x[s:s + m] += h[:m]
    elif n == "hit":
        x = _mix(_sweep(150, 50, 0.22, 20), _bandpass(_noise(0.08), 800, 5000) * _decay(0.08, 60) * 0.7)
    elif n == "boing":
        sec = 0.38; t = _t(sec)
        f = 320 * np.exp(-t * 3) + 120 + 30 * np.sin(t * 50)
        x = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t * 6)
    elif n == "whirr":
        x = _bandpass(_noise(0.45), 800, 3000) * _env(int(0.45 * SR), 0.1, 0.2) * 0.6 + _sweep(300, 900, 0.45) * 0.2 * _env(int(0.45 * SR), 0.1, 0.2)
    elif n == "rattle":
        sec = 0.35; x = np.zeros(int(sec * SR))
        for i in range(14):
            h = _bandpass(_noise(0.02), 2000, 8000) * _decay(0.02, 200) * 0.6
            s = int(i * sec / 14 * SR); m = min(len(h), len(x) - s); x[s:s + m] += h[:m]
    elif n == "charge":
        x = _sweep(300, 1400, 0.35) * _env(int(0.35 * SR), 0.02, 0.1) * 0.6
    elif n == "swish":      # 사다리 추적 이동
        sec = 0.14; t = _t(sec); x = _bandpass(_noise(sec), 1500, 7000) * np.sin(math.pi * t / sec) * 0.5
    elif n == "step":       # 발소리 1회
        x = _mix(_sweep(180, 90, 0.07, 45) * 0.8, _bandpass(_noise(0.03), 300, 2000) * _decay(0.03, 150) * 0.5)
    # ── 루프(앰비언트) ──
    elif n == "run":        # 달리기: 발소리 교대 + 바람
        sec = 1.0; x = np.zeros(int(sec * SR)); st = make("step")
        for i in range(6):
            s = int(i * sec / 6 * SR); m = min(len(st), len(x) - s); x[s:s + m] += st[:m] * (0.9 if i % 2 == 0 else 0.7)
        x += _bandpass(_noise(sec), 300, 1500) * 0.08
    elif n == "spin":       # 슬롯 회전
        sec = 0.5; x = np.zeros(int(sec * SR)); tk = make("tick")
        for i in range(10):
            s = int(i * sec / 10 * SR); m = min(len(tk), len(x) - s); x[s:s + m] += tk[:m] * 0.7
    elif n == "hiss":
        x = _bandpass(_noise(1.0), 1500, 6000) * 0.25
    elif n == "rain":
        sec = 2.0; x = _bandpass(_noise(sec), 800, 9000) * 0.22
        for _ in range(40):
            s = int(_rng.uniform(0, sec - 0.02) * SR); d = _bandpass(_noise(0.015), 3000, 9000) * _decay(0.015, 300) * 0.5
            x[s:s + len(d)] += d
    elif n == "wind":
        sec = 2.0; t = _t(sec)
        x = _bandpass(_noise(sec), 200, 1200) * (0.5 + 0.5 * np.sin(t * 2.1) * np.sin(t * 0.7)) * 0.35
    elif n == "hum":
        sec = 2.0; t = _t(sec)
        x = (np.sin(2 * math.pi * 55 * t) + 0.5 * np.sin(2 * math.pi * 110 * t)) * (0.7 + 0.3 * np.sin(t * 1.3)) * 0.35
        x += _bandpass(_noise(sec), 3000, 8000) * 0.03
    elif n == "snowwind":
        sec = 2.0; t = _t(sec); x = _bandpass(_noise(sec), 150, 700) * (0.6 + 0.4 * np.sin(t * 1.1)) * 0.2
    elif n == "birds":
        sec = 2.0; x = np.zeros(int(sec * SR))
        for _ in range(5):
            s = int(_rng.uniform(0, sec - 0.2) * SR); c = _sweep(2800, 3600, 0.08, 20) * 0.35
            c = np.concatenate([c, np.zeros(int(0.04 * SR)), _sweep(3400, 2900, 0.08, 20) * 0.3])
            m = min(len(c), len(x) - s); x[s:s + m] += c[:m]
    elif n == "bubble":     # 하트/귀여움 루프
        sec = 1.0; x = np.zeros(int(sec * SR))
        for i in range(3):
            s = int(_rng.uniform(0, sec - 0.12) * SR); b = _sweep(600, 1100, 0.1, 25) * 0.35
            m = min(len(b), len(x) - s); x[s:s + m] += b[:m]
    else:
        x = np.zeros(int(0.05 * SR))
    x = np.asarray(x, dtype=np.float64)
    peak = np.abs(x).max() or 1.0
    x = x / peak * 0.8
    _CACHE[name] = x
    return x


LOOPS = {"run", "spin", "hiss", "rain", "wind", "hum", "snowwind", "birds", "bubble"}


# ──────────────────────────────────────────────────────────────
# 트랙 조립
# ──────────────────────────────────────────────────────────────
def build_track(events: List[Tuple[float, str, float]], loops: List[Tuple[float, float, str, float]],
                total: float) -> np.ndarray:
    """events: (time, name, gain) / loops: (start, duration, name, gain) → 스테레오 (n,2)"""
    n = int(total * SR) + SR // 10
    L = np.zeros(n); R = np.zeros(n)
    for t0, name, gain in events:
        x = make(name) * gain
        i = int(t0 * SR)
        if i < 0 or i >= n:
            continue
        m = min(len(x), n - i)
        pan = 0.5 + 0.25 * math.sin(t0 * 7.3)  # 살짝 좌우 변화
        L[i:i + m] += x[:m] * (1 - pan) * 1.4; R[i:i + m] += x[:m] * pan * 1.4
    for t0, dur, name, gain in loops:
        x = make(name)
        need = int(dur * SR); reps = need // len(x) + 2
        seg = np.tile(x, reps)[:need]
        fade = min(int(0.15 * SR), need // 3)
        if fade > 0:
            seg[:fade] *= np.linspace(0, 1, fade); seg[-fade:] *= np.linspace(1, 0, fade)
        i = int(t0 * SR); m = min(len(seg), n - i)
        if i < 0 or m <= 0:
            continue
        L[i:i + m] += seg[:m] * gain; R[i:i + m] += seg[:m] * gain
    st = np.stack([L, R], axis=1)
    st = np.tanh(st * 1.1)
    return st[: int(total * SR)]


def write_wav(path: str, data: np.ndarray):
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


ALL = ["pop", "blip", "tick", "beep", "ding", "notify", "tok", "tada", "fanfare", "sparkle", "whoosh", "thud", "knock",
       "creak", "whistle", "cheer", "drumroll", "hit", "boing", "whirr", "rattle", "charge", "swish", "step",
       "run", "spin", "hiss", "rain", "wind", "hum", "snowwind", "birds", "bubble"]

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "sfx")
    os.makedirs(out, exist_ok=True)
    # 한 파일에 전부 이어붙인 샘플러(이름 순, 0.4초 간격)
    events = []; t = 0.2
    for nm in ALL:
        events.append((t, nm, 1.0)); t += len(make(nm)) / SR + 0.4
    write_wav(os.path.join(out, "sfx_sampler.wav"), build_track(events, [], t))
    print("sampler:", os.path.join(out, "sfx_sampler.wav"), f"{t:.1f}s")
    for nm in ALL:
        x = make(nm); write_wav(os.path.join(out, f"{nm}.wav"), np.stack([x, x], axis=1))
    print("saved", len(ALL), "sfx")
