#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OGQ 릴스 자동 생성기 — 코어 엔진
스티커팩(zip/폴더) + 포맷 선택 + 텍스트 → 1080x1920 MP4(H.264)

사용법(CLI):
  python3 reels_gen.py --list
  python3 reels_gen.py 팩.zip --format F04 -o out.mp4
  python3 reels_gen.py 팩.zip --format F04 --params '{"question":"..."}' -o out.mp4
  python3 reels_gen.py 팩.zip --demo            # 전체 포맷 데모 렌더 → output/
"""
import os, io, re, sys, json, math, random, zipfile, subprocess, argparse
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Any, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")

INK = (45, 45, 58)
WHITE = (255, 255, 255)
GRAY = (120, 122, 135)

# 인스타 릴스 UI에 가려지지 않는 안전 영역
SAFE_TOP = 230
SAFE_BOTTOM = H - 330
CX = W // 2


# ──────────────────────────────────────────────────────────────
# 폰트
# ──────────────────────────────────────────────────────────────
_FONT_FILES = {
    "title": "Jua-Regular.ttf",
    "bold": "Pretendard-ExtraBold.otf",
    "semi": "Pretendard-Bold.ttf",
    "reg": "Pretendard-Regular.ttf",
}
_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(8, int(size))
    key = (name, size)
    if key not in _FONT_CACHE:
        path = os.path.join(FONT_DIR, _FONT_FILES.get(name, _FONT_FILES["bold"]))
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


# ──────────────────────────────────────────────────────────────
# 스티커팩
# ──────────────────────────────────────────────────────────────
def _natural_key(s: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s)]


def trim_alpha(im: Image.Image, pad: int = 6) -> Image.Image:
    """투명 여백 제거(살짝 패딩)."""
    im = im.convert("RGBA")
    bbox = im.split()[3].getbbox()
    if not bbox:
        return im
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(im.width, x1 + pad); y1 = min(im.height, y1 + pad)
    return im.crop((x0, y0, x1, y1))


class StickerPack:
    def __init__(self, stickers: List[Image.Image], main: Optional[Image.Image],
                 tab: Optional[Image.Image], name: str = "스티커팩"):
        self.stickers = stickers
        self.main = main if main is not None else (stickers[0] if stickers else None)
        self.tab = tab
        self.name = name

    def __len__(self):
        return len(self.stickers)

    def get(self, idx) -> Image.Image:
        """1-based 번호 또는 'main'."""
        if idx == "main" or idx == 0:
            return self.main
        i = int(idx)
        if not (1 <= i <= len(self.stickers)):
            i = ((i - 1) % len(self.stickers)) + 1
        return self.stickers[i - 1]

    @staticmethod
    def _from_files(files: Dict[str, bytes], name: str) -> "StickerPack":
        stickers, main, tab = [], None, None
        entries = []
        for fn, data in files.items():
            base = os.path.basename(fn)
            if not base or base.startswith(".") or "__MACOSX" in fn:
                continue
            if not base.lower().endswith((".png", ".webp", ".jpg", ".jpeg")):
                continue
            entries.append((base, data))
        entries.sort(key=lambda e: _natural_key(e[0]))
        for base, data in entries:
            stem = os.path.splitext(base)[0].lower()
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            if stem == "main":
                main = trim_alpha(im)
            elif stem == "tab":
                tab = im
            else:
                stickers.append(trim_alpha(im))
        if not stickers:
            raise ValueError("스티커 이미지(png)를 찾지 못했습니다.")
        return StickerPack(stickers, main, tab, name)

    @classmethod
    def from_zip(cls, src, name: Optional[str] = None) -> "StickerPack":
        if isinstance(src, (bytes, bytearray)):
            zf = zipfile.ZipFile(io.BytesIO(src))
            nm = name or "스티커팩"
        else:
            zf = zipfile.ZipFile(src)
            nm = name or os.path.splitext(os.path.basename(str(src)))[0]
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
        return cls._from_files(files, nm)

    @classmethod
    def from_dir(cls, d: str) -> "StickerPack":
        files = {}
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    files[fn] = f.read()
        return cls._from_files(files, os.path.basename(os.path.normpath(d)))

    @classmethod
    def load(cls, path: str) -> "StickerPack":
        return cls.from_dir(path) if os.path.isdir(path) else cls.from_zip(path)

    def contact_sheet(self, cols: int = 6, cell: int = 180) -> Image.Image:
        """번호가 붙은 스티커 일람표 (UI/선택용)."""
        n = len(self.stickers)
        rows = math.ceil(n / cols)
        sheet = Image.new("RGBA", (cols * cell, rows * (cell + 34)), (248, 248, 250, 255))
        d = ImageDraw.Draw(sheet)
        for i, st in enumerate(self.stickers):
            x = (i % cols) * cell; y = (i // cols) * (cell + 34)
            im = fit(st, cell - 20, cell - 20)
            sheet.paste(im, (x + (cell - im.width) // 2, y + 6 + (cell - 20 - im.height) // 2), im)
            d.rounded_rectangle((x + cell // 2 - 30, y + cell - 4, x + cell // 2 + 30, y + cell + 28),
                                radius=16, fill=(60, 60, 70))
            d.text((x + cell // 2, y + cell + 12), str(i + 1), font=font("bold", 22), fill=WHITE, anchor="mm")
        return sheet


# ──────────────────────────────────────────────────────────────
# 테마(파스텔)
# ──────────────────────────────────────────────────────────────
THEMES = {
    # name: (bg, shape, accent, hue)
    "mint":     ((223, 245, 234), (196, 235, 214), (72, 190, 135), 150),
    "pink":     ((255, 228, 236), (255, 204, 219), (255, 110, 155), 340),
    "yellow":   ((255, 243, 198), (255, 231, 156), (255, 170, 50), 48),
    "sky":      ((221, 238, 255), (192, 222, 255), (70, 145, 255), 210),
    "lavender": ((236, 228, 255), (214, 200, 255), (135, 105, 255), 262),
    "peach":    ((255, 233, 216), (255, 212, 183), (255, 135, 85), 22),
}


def auto_theme(pack: StickerPack) -> str:
    """대표 스티커의 채도 가중 평균 색상 → 가장 가까운 파스텔 테마."""
    im = (pack.main or pack.stickers[0]).convert("RGBA").resize((64, 64))
    import colorsys
    import numpy as np
    hx = hy = wsum = 0.0
    for r, g, b, a in np.asarray(im).reshape(-1, 4).tolist():
        if a < 128:
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        w = s * v * (a / 255)
        if w < 0.05:
            continue
        hx += w * math.cos(2 * math.pi * h); hy += w * math.sin(2 * math.pi * h); wsum += w
    if wsum == 0:
        return "mint"
    hue = (math.degrees(math.atan2(hy, hx)) + 360) % 360
    best = min(THEMES.items(), key=lambda kv: min(abs(hue - kv[1][3]), 360 - abs(hue - kv[1][3])))
    return best[0]


# ──────────────────────────────────────────────────────────────
# 효과음 이벤트 수집 (장면 그리기 중 자동 기록 → 렌더 후 오디오 트랙 생성)
# ──────────────────────────────────────────────────────────────
_SFX = {"on": True, "scene": 0, "offset": 0.0, "t": 0.0, "events": {}, "loops": {}}


def sfx_now(name: str, gain: float = 1.0, key=None):
    """현재 프레임 시각에 효과음 1회 (같은 키는 한 번만)."""
    if not _SFX["on"]:
        return
    tg = _SFX["offset"] + _SFX["t"]
    k = key if key is not None else (name, round(tg, 2))
    if k not in _SFX["events"]:
        _SFX["events"][k] = (tg, name, gain)


def sfx_once(name: str, t: float, start: float, gain: float = 1.0):
    """장면 로컬 시각 start 에 도달한 첫 프레임에서만 재생."""
    if start <= t < start + 1.0 / FPS - 1e-6:
        sfx_now(name, gain)


def sfx_change(name: str, value, gain: float = 1.0):
    """value(예: 카운트 숫자, 메시지 번호)가 바뀔 때마다 재생."""
    sfx_now(name, gain, key=(_SFX["scene"], name, value))


def sfx_loop(name: str, start: float, dur: float, gain: float = 0.5):
    """장면 로컬 start 부터 dur 동안 루프 효과음(빗소리·달리기 등)."""
    if not _SFX["on"]:
        return
    k = (_SFX["scene"], name, round(start, 2))
    if k not in _SFX["loops"]:
        _SFX["loops"][k] = (_SFX["offset"] + start, dur, name, gain)


def sfx_reset(on: bool = True):
    _SFX.update({"on": on, "scene": 0, "offset": 0.0, "t": 0.0, "events": {}, "loops": {}})


# ──────────────────────────────────────────────────────────────
# 이징/애니 유틸
# ──────────────────────────────────────────────────────────────
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out_cubic(t):
    t = clamp(t); return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = clamp(t); return t * t * (3 - 2 * t)


def ease_out_back(t, s=1.70158):
    t = clamp(t); t -= 1
    return 1 + t * t * ((s + 1) * t + s)


def pop(t, start=0.0, dur=0.38):
    """start 시점부터 dur 동안 0→1(약간 튕김). 이전엔 0. 등장 순간 '뿅' 효과음."""
    if t < start:
        return 0.0
    sfx_once("pop", t, start, 0.7)
    return ease_out_back((t - start) / dur)


def fade(t, start=0.0, dur=0.3):
    if t < start:
        return 0.0
    return ease_out_cubic((t - start) / dur)


def bob(t, amp=10, period=1.8, phase=0.0):
    return amp * math.sin(2 * math.pi * (t / period + phase))


def wobble(t, deg=3, period=1.4, phase=0.0):
    return deg * math.sin(2 * math.pi * (t / period + phase))


# ──────────────────────────────────────────────────────────────
# 그리기 유틸
# ──────────────────────────────────────────────────────────────
_RESIZE_CACHE: Dict[Tuple[int, int, int], Image.Image] = {}


def fit(im: Image.Image, w: Optional[int], h: Optional[int] = None) -> Image.Image:
    """비율 유지로 w×h 박스 안에 맞춤(캐시)."""
    if w is None and h is None:
        return im
    iw, ih = im.size
    if w is None:
        s = h / ih
    elif h is None:
        s = w / iw
    else:
        s = min(w / iw, h / ih)
    nw, nh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
    key = (id(im), nw, nh)
    if key not in _RESIZE_CACHE:
        if len(_RESIZE_CACHE) > 600:
            _RESIZE_CACHE.clear()
        _RESIZE_CACHE[key] = im.resize((nw, nh), Image.LANCZOS)
    return _RESIZE_CACHE[key]


def paste_sticker(img: Image.Image, st: Image.Image, cx, cy, size=600, scale=1.0,
                  rot=0.0, alpha=1.0, flip=False, height=None):
    """스티커를 중심(cx,cy)에 size(가로) 기준으로 배치. height 주면 박스 맞춤."""
    if scale <= 0.01 or alpha <= 0.01 or st is None:
        return
    if height is None:
        height = size * 0.96  # 세로로 긴 스티커가 과하게 커지지 않도록 높이 캡
    im = fit(st, int(size * scale), int(height * scale))
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if abs(rot) > 0.05:
        im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
    if alpha < 0.999:
        a = im.split()[3].point(lambda p: int(p * alpha))
        im = im.copy(); im.putalpha(a)
    img.paste(im, (int(cx - im.width / 2), int(cy - im.height / 2)), im)


def silhouette(st: Image.Image, color=(70, 72, 90)) -> Image.Image:
    sil = Image.new("RGBA", st.size, color + (255,))
    sil.putalpha(st.split()[3])
    return sil


_SIL_CACHE: Dict[Tuple[int, tuple], Image.Image] = {}


def sil_of(st: Image.Image, color=(70, 72, 90)) -> Image.Image:
    key = (id(st), color)
    if key not in _SIL_CACHE:
        _SIL_CACHE[key] = silhouette(st, color)
    return _SIL_CACHE[key]


def wrap_text(text: str, fnt: ImageFont.FreeTypeFont, max_w: int, stroke_w=0) -> List[str]:
    def width(s):
        b = fnt.getbbox(s, stroke_width=stroke_w)
        return b[2] - b[0]
    lines = []
    for para in str(text).split("\n"):
        if not para.strip():
            lines.append(""); continue
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if width(cand) <= max_w:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                if width(w) <= max_w:
                    cur = w
                else:  # 단어 자체가 너무 길면 글자 단위
                    cur = ""
                    for ch in w:
                        if width(cur + ch) <= max_w:
                            cur += ch
                        else:
                            lines.append(cur); cur = ch
        lines.append(cur)
    return lines


def text_size(text, fnt, stroke_w=0):
    b = fnt.getbbox(text, stroke_width=stroke_w)
    return b[2] - b[0], b[3] - b[1]


def draw_text(img: Image.Image, text: str, cx, cy, size=64, font_name="bold", fill=INK,
              stroke=None, stroke_w=0, max_w=W - 200, spacing=1.22, anchor="mm", alpha=1.0,
              fit_lines=2):
    """여러 줄 자동 줄바꿈 중앙정렬 텍스트. anchor: mm(중앙) / mt(위 기준). 반환: 전체 bbox.
    fit_lines: 이 줄 수를 넘거나 마지막 줄이 2자 이하로 고아가 되면 글자 크기를 줄여서 맞춤."""
    if not text or alpha <= 0.01:
        return (cx, cy, cx, cy)
    size = int(size)
    min_size = max(14, int(size * 0.55))
    while True:
        fnt = font(font_name, size)
        lines = wrap_text(text, fnt, max_w, stroke_w)
        bad = fit_lines and (len(lines) > fit_lines or (len(lines) > 1 and len(lines[-1].strip()) <= 2))
        if not bad or size - 3 < min_size:
            break
        size -= 3
    lh = int(size * spacing)
    total = lh * len(lines)
    y0 = cy - total / 2 if anchor == "mm" else cy
    layer = img if alpha >= 0.999 else Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    maxw = 0
    for i, line in enumerate(lines):
        y = y0 + i * lh + lh / 2
        d.text((cx, y), line, font=fnt, fill=fill, anchor="mm",
               stroke_width=stroke_w, stroke_fill=stroke)
        maxw = max(maxw, text_size(line, fnt, stroke_w)[0])
    if layer is not img:
        a = layer.split()[3].point(lambda p: int(p * alpha)); layer.putalpha(a)
        img.alpha_composite(layer)
    return (cx - maxw / 2, y0, cx + maxw / 2, y0 + total)


def draw_pill(img: Image.Image, text: str, cx, cy, size=44, fill=WHITE, text_fill=INK,
              padx=44, pady=20, outline=None, ow=0, font_name="bold", scale=1.0, shadow=None):
    if scale <= 0.01 or not text:
        return
    size = int(size * scale)
    fnt = font(font_name, size)
    tw, _ = text_size(text, fnt)
    w = tw + 2 * padx * scale; h = size + 2 * pady * scale
    d = ImageDraw.Draw(img)
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    if shadow:
        d.rounded_rectangle(tuple(v + (0 if i % 2 == 0 else 8) for i, v in enumerate(box)),
                            radius=h / 2, fill=shadow)
    d.rounded_rectangle(box, radius=h / 2, fill=fill, outline=outline, width=ow)
    d.text((cx, cy), text, font=fnt, fill=text_fill, anchor="mm")
    return box


def draw_badge(img, text, cx, cy, r=44, fill=INK, text_fill=WHITE, size=None, scale=1.0, outline=None, ow=0):
    if scale <= 0.01:
        return
    r = r * scale
    d = ImageDraw.Draw(img)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=ow)
    d.text((cx, cy), str(text), font=font("bold", int((size or r * 1.05))), fill=text_fill, anchor="mm")


def draw_card(img, box, radius=48, fill=WHITE, shadow=(0, 0, 0, 28), offset=10, outline=None, ow=0):
    d = ImageDraw.Draw(img)
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            (box[0], box[1] + offset, box[2], box[3] + offset), radius=radius, fill=shadow)
        img.alpha_composite(sh)
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=ow)


def star_points(cx, cy, r, inner=0.38, n=4, rot=0.0):
    pts = []
    for i in range(n * 2):
        ang = math.radians(rot + i * 180 / n - 90)
        rr = r if i % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return pts


def draw_sparkles(img, t, cx, cy, radius, n=8, seed=1, color=(255, 205, 60), rmax=34, start=0.0):
    if t < start:
        return
    sfx_once("sparkle", t, start, 0.45)
    rng = random.Random(seed)
    d = ImageDraw.Draw(img)
    for i in range(n):
        ang = rng.uniform(0, 2 * math.pi); dist = radius * rng.uniform(0.75, 1.15)
        ph = rng.random(); sp = rng.uniform(0.8, 1.6)
        tw = 0.5 + 0.5 * math.sin(2 * math.pi * ((t - start) * sp + ph))
        r = rmax * rng.uniform(0.5, 1.0) * tw * fade(t, start, 0.3)
        if r < 2:
            continue
        x = cx + dist * math.cos(ang); y = cy + dist * math.sin(ang)
        d.polygon(star_points(x, y, r), fill=color)


def draw_confetti(img, t, seed=3, n=46, start=0.0, palette=None):
    if t < start:
        return
    sfx_once("tada", t, start, 0.9)
    palette = palette or [(255, 120, 155), (255, 200, 60), (90, 200, 150), (90, 160, 255), (170, 130, 255)]
    rng = random.Random(seed)
    d = ImageDraw.Draw(img)
    tt = t - start
    for i in range(n):
        x0 = rng.uniform(0, W); sp = rng.uniform(260, 520); ph = rng.uniform(0, H)
        col = palette[i % len(palette)]; w = rng.uniform(14, 22); h = rng.uniform(26, 40)
        rs = rng.uniform(-200, 200); sway = rng.uniform(20, 60)
        y = (ph + tt * sp) % (H + 120) - 60
        x = x0 + sway * math.sin(tt * 2 + i)
        ang = math.radians(tt * rs)
        c, s = math.cos(ang), math.sin(ang)
        pts = [(x + c * dx - s * dy, y + s * dx + c * dy)
               for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
        d.polygon(pts, fill=col)


def draw_heart(d: ImageDraw.ImageDraw, cx, cy, s, fill):
    r = s * 0.5
    d.ellipse((cx - s, cy - s * 0.6 - r, cx, cy - s * 0.6 + r), fill=fill)
    d.ellipse((cx, cy - s * 0.6 - r, cx + s, cy - s * 0.6 + r), fill=fill)
    d.polygon([(cx - s * 0.98, cy - s * 0.45), (cx + s * 0.98, cy - s * 0.45), (cx, cy + s * 0.9)], fill=fill)


def overlay(img: Image.Image, fn: Callable[[ImageDraw.ImageDraw], None]):
    """반투명 요소용 레이어."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fn(ImageDraw.Draw(layer))
    img.alpha_composite(layer)


def draw_ring_countdown(img, cx, cy, r, p, color, width=22, track=(255, 255, 255)):
    d = ImageDraw.Draw(img)
    box = (cx - r, cy - r, cx + r, cy + r)
    d.ellipse(box, outline=track, width=width)
    if p > 0.002:
        d.arc(box, start=-90, end=-90 + 360 * clamp(p), fill=color, width=width)


# ──────────────────────────────────────────────────────────────
# 컨텍스트(팩 + 테마 + 배경)
# ──────────────────────────────────────────────────────────────
class Ctx:
    def __init__(self, pack: StickerPack, theme="auto", bg_style="circles", brand="OGQ 마켓"):
        self.pack = pack
        self.theme_name = auto_theme(pack) if theme in (None, "", "auto") else theme
        bg, shape, accent, _ = THEMES.get(self.theme_name, THEMES["mint"])
        self.bg_color, self.shape_color, self.accent = bg, shape, accent
        self.bg_style = bg_style
        self.brand = brand
        self.meta: Dict[str, Any] = {}   # 포맷이 결과(우승 번호 등)를 기록 → UI에 표시
        self._bg = self._build_bg()

    def st(self, idx) -> Image.Image:
        return self.pack.get(idx)

    def _build_bg(self) -> Image.Image:
        img = Image.new("RGBA", (W, H), self.bg_color + (255,))
        d = ImageDraw.Draw(img)
        if self.bg_style == "circles":
            d.ellipse((W - 420, -260, W + 420, 580), fill=self.shape_color)
            d.ellipse((-380, H - 620, 300, H + 60), fill=self.shape_color)
            d.ellipse((-150, 700, 130, 980), fill=self.shape_color)
        elif self.bg_style == "halftone":
            step = 56
            for yy in range(SAFE_TOP - 40, H - 260, step):
                for xx in range(40, W - 20, step):
                    d.ellipse((xx - 7, yy - 7, xx + 7, yy + 7), fill=self.shape_color)
        elif self.bg_style == "checker":
            d.rounded_rectangle((36, 36, W - 36, H - 36), radius=60, outline=self.shape_color, width=26)
            s = 52
            for i in range(0, W, s * 2):
                d.rectangle((i, 0, i + s, 36), fill=self.shape_color)
                d.rectangle((i + s, H - 36, i + 2 * s, H), fill=self.shape_color)
        elif self.bg_style == "space":
            img = Image.new("RGBA", (W, H), (22, 24, 56, 255))
            d = ImageDraw.Draw(img)
            rng = random.Random(7)
            for _ in range(160):
                x, y, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1, 4)
                d.ellipse((x - r, y - r, x + r, y + r), fill=(235, 235, 255))
        # 하단 브랜드
        if self.brand:
            col = (200, 205, 225) if self.bg_style == "space" else GRAY
            d.text((CX, H - 175), self.brand, font=font("semi", 30), fill=col, anchor="mm")
        return img

    def bg(self) -> Image.Image:
        return self._bg.copy()

    def dark(self):
        return self.bg_style == "space"

    @property
    def ink(self):
        return WHITE if self.dark() else INK


# ──────────────────────────────────────────────────────────────
# 씬 / 타임라인 / 렌더
# ──────────────────────────────────────────────────────────────
@dataclass
class Scene:
    duration: float
    draw: Callable[[float, Image.Image], None]           # (t_local, img) → img 위에 그림
    static: Optional[Callable[[Image.Image], None]] = None  # 배경 위 정적 요소(한 번만)
    _base: Optional[Image.Image] = None

    def base(self, ctx: Ctx) -> Image.Image:
        if self._base is None:
            b = ctx.bg()
            if self.static:
                self.static(b)
            self._base = b
        return self._base


class Timeline:
    def __init__(self, ctx: Ctx, scenes: List[Scene]):
        self.ctx = ctx
        self.scenes = [s for s in scenes if s.duration > 0]
        self.total = sum(s.duration for s in self.scenes)

    def frame(self, t: float) -> Image.Image:
        acc = 0.0
        for idx, s in enumerate(self.scenes):
            if t < acc + s.duration or s is self.scenes[-1]:
                tl = min(t - acc, s.duration)
                _SFX.update({"scene": idx, "offset": acc, "t": tl})
                img = s.base(self.ctx).copy()
                s.draw(tl, img)
                return img
            acc += s.duration
        return self.ctx.bg()

    def preview(self, n=6) -> List[Image.Image]:
        return [self.frame(self.total * (i + 0.5) / n) for i in range(n)]


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def resolve_audio(bgm) -> Optional[str]:
    """bgm: None/'none' → 무음, 'cute'|'upbeat'|'chill'|'funny' → 기본 음악, 그 외 → 파일 경로."""
    if not bgm or str(bgm).lower() in ("none", "off", "무음"):
        return None
    try:
        import bgm_gen
        if str(bgm) in bgm_gen.MOODS:
            return bgm_gen.get_bgm(str(bgm))
    except Exception:
        pass
    return str(bgm) if os.path.exists(str(bgm)) else None


def render_video(tl: Timeline, out_path: str, fps: int = FPS, audio_path: Optional[str] = None,
                 progress: Optional[Callable[[int, int], None]] = None, crf: int = 20,
                 volume: float = 1.0, fade_out: float = 1.0, sfx: bool = True, sfx_volume: float = 1.0) -> str:
    """1패스: 프레임 → 무음 H.264. 2패스: BGM(루프·페이드) + 효과음 트랙 합성(-c:v copy)."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    sfx_reset(on=sfx)
    tmp_video = out_path + ".video.tmp.mp4"
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", tmp_video]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = max(1, int(round(tl.total * fps)))
    try:
        for i in range(n):
            img = tl.frame(i / fps)
            proc.stdin.write(img.convert("RGB").tobytes())
            if progress:
                progress(i + 1, n)
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "ignore")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 오류: " + err[-2000:])

    events = list(_SFX["events"].values()); loops = list(_SFX["loops"].values())
    sfx_wav = None
    if sfx and (events or loops):
        try:
            import sfx_gen
            sfx_wav = out_path + ".sfx.tmp.wav"
            sfx_gen.write_wav(sfx_wav, sfx_gen.build_track(events, loops, tl.total))
        except Exception:
            sfx_wav = None
    if not audio_path and not sfx_wav:
        os.replace(tmp_video, out_path)
        return out_path

    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", tmp_video]
    parts, labels, idx = [], [], 1
    if audio_path:
        cmd += ["-stream_loop", "-1", "-i", audio_path]
        af = [f"volume={max(0.0, volume):.2f}", "afade=t=in:st=0:d=0.15"]
        if fade_out and tl.total > fade_out + 0.5:
            af.append(f"afade=t=out:st={tl.total - fade_out:.2f}:d={fade_out:.2f}")
        parts.append(f"[{idx}:a]" + ",".join(af) + "[b]"); labels.append("[b]"); idx += 1
    if sfx_wav:
        cmd += ["-i", sfx_wav]
        parts.append(f"[{idx}:a]volume={max(0.0, sfx_volume):.2f}[s]"); labels.append("[s]")
    if len(labels) == 2:
        parts.append("[b][s]amix=inputs=2:duration=shortest:normalize=0[a]")
    else:
        parts[-1] = parts[-1][:-3] + "[a]"
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]", "-shortest",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True)
    for f in (tmp_video, sfx_wav):
        if f and os.path.exists(f):
            os.remove(f)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg(오디오) 오류: " + r.stderr.decode("utf-8", "ignore")[-2000:])
    return out_path


# ──────────────────────────────────────────────────────────────
# 포맷 스펙 & 파라미터 헬퍼
# ──────────────────────────────────────────────────────────────
@dataclass
class Field:
    key: str
    label: str
    type: str                # text | textarea | sticker | stickers | pairs | int | float | choice
    default: Any = None      # 값 또는 callable(pack)
    n: Optional[int] = None  # stickers/pairs 권장 개수
    help: str = ""
    options: Optional[List[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None


@dataclass
class FormatSpec:
    id: str
    name: str
    category: str
    scene_desc: str
    fields: List[Field]
    build: Callable[[Ctx, dict], List[Scene]]
    tip: str = ""

    def defaults(self, pack: StickerPack) -> dict:
        out = {}
        for f in self.fields:
            v = f.default(pack) if callable(f.default) else f.default
            out[f.key] = v
        return out


FORMATS: Dict[str, FormatSpec] = {}


def register(spec: FormatSpec):
    FORMATS[spec.id] = spec
    for k in sorted(FORMATS):  # 시트 번호순 유지
        FORMATS[k] = FORMATS.pop(k)
    return spec


def spread(n_pack: int, k: int, offset: int = 0) -> List[int]:
    """팩에서 k개를 고르게 뽑은 1-based 번호."""
    if n_pack <= 0:
        return []
    if k >= n_pack:
        return list(range(1, n_pack + 1))[:k] + [1] * max(0, k - n_pack)
    return [int(round(offset + i * (n_pack - 1) / max(1, k - 1))) % n_pack + 1 for i in range(k)]


def P(params: dict, key: str, default=None):
    v = params.get(key)
    if v is None or v == "" or v == []:
        return default
    return v


def rseed(params: dict) -> int:
    """seed 파라미터가 0/비어 있으면 매번 새로운 랜덤 시드(결과가 고정되지 않게)."""
    try:
        v = int(P(params, "seed", 0) or 0)
    except Exception:
        v = 0
    return v if v > 0 else random.randrange(1, 10 ** 9)


def as_int_list(v, fallback: List[int]) -> List[int]:
    if v is None:
        return fallback
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s]+", v.strip()) if x]
    out = []
    for x in v:
        try:
            out.append(int(x))
        except Exception:
            pass
    return out or fallback


def as_pairs(v, fallback: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
    """'라벨|번호' 줄 목록 → [(라벨, 번호)]."""
    if v is None:
        return fallback
    if isinstance(v, str):
        lines = [l.strip() for l in v.splitlines() if l.strip()]
    else:
        lines = v
    out = []
    for i, l in enumerate(lines):
        if isinstance(l, (list, tuple)) and len(l) >= 2:
            out.append((str(l[0]), int(l[1]))); continue
        parts = [p.strip() for p in str(l).split("|")]
        label = parts[0]
        try:
            idx = int(parts[1]) if len(parts) > 1 else (fallback[i][1] if i < len(fallback) else i + 1)
        except Exception:
            idx = i + 1
        out.append((label, idx))
    return out or fallback


def as_lines(v, fallback: List[str]) -> List[str]:
    if v is None:
        return fallback
    if isinstance(v, str):
        lines = [l.strip() for l in v.splitlines() if l.strip()]
        return lines or fallback
    return [str(x) for x in v] or fallback


# 공통 CTA 씬
def cta_scene(ctx: Ctx, text: str, sticker=None, dur=2.0, sub: str = "") -> Scene:
    st = ctx.st(sticker) if sticker is not None else ctx.pack.main

    def draw(t, img):
        s = pop(t, 0.0, 0.4)
        paste_sticker(img, st, CX, 780 + bob(t, 8), size=520, scale=s, rot=wobble(t, 2))
        draw_text(img, text, CX, 1210, size=72, fill=ctx.ink, stroke=None if ctx.dark() else WHITE,
                  stroke_w=0 if ctx.dark() else 8, alpha=fade(t, 0.15, 0.3), max_w=W - 180)
        if sub:
            draw_pill(img, sub, CX, 1380, size=40, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.35, 0.35))
        draw_sparkles(img, t, CX, 780, 330, n=7, seed=11, start=0.2)
    return Scene(dur, draw)


def title_scene(ctx: Ctx, text: str, dur=1.6, sub: str = "", sticker=None, size=92) -> Scene:
    st = ctx.st(sticker) if sticker is not None else None

    def draw(t, img):
        s = pop(t, 0.0, 0.4)
        y = 900 if st is None else 760
        draw_text(img, text, CX, y, size=int(size * min(1, s + 0.2)), font_name="title",
                  fill=ctx.ink, stroke=None if ctx.dark() else WHITE, stroke_w=0 if ctx.dark() else 10,
                  alpha=fade(t, 0, 0.2), max_w=W - 160)
        if sub:
            draw_pill(img, sub, CX, y + 190, size=42, fill=WHITE if not ctx.dark() else ctx.accent,
                      text_fill=INK if not ctx.dark() else WHITE, scale=pop(t, 0.25, 0.35))
        if st is not None:
            paste_sticker(img, st, CX, 1230 + bob(t, 10), size=440, scale=pop(t, 0.2, 0.4))
    return Scene(dur, draw)


def sequence_scenes(ctx: Ctx, header: str, cuts: List[Tuple[str, int]], per=1.5, label_size=54,
                    sticker_size=760, keep_header=True) -> List[Scene]:
    """(라벨, 스티커) 컷 나열 — 상황별/요일별/직장인 하루 등 공통."""
    scenes = []
    for i, (label, idx) in enumerate(cuts):
        st = ctx.st(idx)

        def draw(t, img, st=st, label=label, i=i):
            if keep_header and header:
                draw_pill(img, header, CX, SAFE_TOP + 80, size=44, fill=ctx.accent, text_fill=WHITE)
            paste_sticker(img, st, CX, 940 + bob(t, 10, 1.6), size=sticker_size, scale=pop(t, 0, 0.42),
                          rot=wobble(t, 2.5, 1.6))
            draw_text(img, label, CX, 1420, size=label_size + 12, fill=ctx.ink,
                      stroke=None if ctx.dark() else WHITE, stroke_w=0 if ctx.dark() else 9,
                      alpha=fade(t, 0.12, 0.25), max_w=W - 160)
        scenes.append(Scene(per, draw))
    return scenes


# ══════════════════════════════════════════════════════════════
# 포맷 구현
# ══════════════════════════════════════════════════════════════

# ── F01 화면 인터랙션 유도 ────────────────────────────────────
def build_f01(ctx: Ctx, p: dict) -> List[Scene]:
    guide = P(p, "guide", "동그라미에 손을 올려보세요")
    count = int(P(p, "count", 3))
    react_idx = int(P(p, "reaction", 1))
    result = P(p, "result", "지큐가 반가워해요!")
    cta = P(p, "cta", "좋았다면 댓글로 알려줘!")
    st = ctx.st(react_idx)
    circle_y = 1000

    def draw_guide(t, img):
        draw_text(img, guide, CX, 430, size=78, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10,
                  alpha=fade(t, 0, 0.25), max_w=W - 160)
        s = pop(t, 0.15, 0.45)
        r = 240 * s
        pulse = 1 + 0.06 * math.sin(t * 5)
        d = ImageDraw.Draw(img)
        d.ellipse((CX - r * pulse - 30, circle_y - r * pulse - 30, CX + r * pulse + 30, circle_y + r * pulse + 30),
                  outline=ctx.shape_color, width=14)
        d.ellipse((CX - r, circle_y - r, CX + r, circle_y + r), fill=WHITE, outline=ctx.accent, width=16)
        draw_text(img, "여기!", CX, circle_y, size=88, font_name="title", fill=ctx.accent, alpha=fade(t, 0.4, 0.2))

    def draw_count(t, img):
        draw_text(img, guide, CX, 430, size=78, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 160)
        r = 240
        d = ImageDraw.Draw(img)
        d.ellipse((CX - r, circle_y - r, CX + r, circle_y + r), fill=WHITE, outline=ctx.accent, width=16)
        sec = int(t); frac = t - sec
        num = max(1, count - sec)
        sfx_change("beep", num, 0.8)
        draw_ring_countdown(img, CX, circle_y, r + 34, 1 - frac, ctx.accent, width=18, track=ctx.shape_color)
        sc = 1.25 - 0.25 * ease_out_cubic(frac * 2)
        draw_text(img, str(num), CX, circle_y, size=int(170 * sc), font_name="title", fill=ctx.accent)
        draw_pill(img, "손 떼지 마세요!", CX, 1420, size=42, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.2))

    def draw_react(t, img):
        draw_confetti(img, t, seed=5, start=0.05)
        paste_sticker(img, st, CX, 900 + bob(t, 10), size=780, scale=pop(t, 0, 0.45), rot=wobble(t, 3))
        draw_sparkles(img, t, CX, 900, 400, n=9, seed=3, start=0.2)
        draw_text(img, result, CX, 1400, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10,
                  alpha=fade(t, 0.25, 0.3), max_w=W - 160)

    return [Scene(2.2, draw_guide), Scene(count, draw_count), Scene(2.6, draw_react),
            cta_scene(ctx, cta, react_idx, 2.0)]


register(FormatSpec(
    "F01", "화면 인터랙션 유도", "참여형",
    '안내 자막("동그라미에 손을 올려보세요") → 카운트 3·2·1 → 캐릭터 반응 컷 → CTA',
    [Field("guide", "안내 문구", "text", "동그라미에 손을 올려보세요"),
     Field("count", "카운트 초", "int", 3, min=1, max=5),
     Field("reaction", "반응 스티커 번호", "sticker", lambda pk: 1),
     Field("result", "반응 문구", "text", "지큐가 반가워해요!"),
     Field("cta", "마무리 CTA", "text", "좋았다면 댓글로 알려줘!")],
    build_f01))


# ── F03 그림자 퀴즈 ──────────────────────────────────────────
def build_f03(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "이 스티커의 정체는?")
    rounds = as_pairs(P(p, "rounds"), [("정답 공개!", i) for i in spread(len(ctx.pack), 3, 2)])
    timer = float(P(p, "timer", 3))
    cta = P(p, "cta", "몇 개 맞췄는지 댓글로!")
    scenes = [title_scene(ctx, title, 1.5, sub="그림자만 보고 맞혀보세요", sticker="main")]
    for i, (label, idx) in enumerate(rounds):
        st = ctx.st(idx); sil = sil_of(st, (72, 74, 92))

        def draw_q(t, img, sil=sil, i=i):
            draw_badge(img, f"Q{i + 1}", 130, SAFE_TOP + 60, r=58, fill=ctx.accent, size=50)
            draw_text(img, "정답은?", CX, 430, size=96, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
            paste_sticker(img, sil, CX, 960 + bob(t, 6), size=760, scale=pop(t, 0, 0.4))
            draw_ring_countdown(img, CX, 1470, 70, 1 - t / timer, ctx.accent, width=16, track=ctx.shape_color)
            draw_text(img, str(max(1, math.ceil(timer - t))), CX, 1470, size=70, fill=ctx.ink)
            sfx_change("beep", max(1, math.ceil(timer - t)), 0.7)

        def draw_a(t, img, st=st, label=label, i=i):
            sfx_once("ding", t, 0.0, 0.8)
            draw_badge(img, f"Q{i + 1}", 130, SAFE_TOP + 60, r=58, fill=ctx.accent, size=50)
            draw_text(img, "정답!", CX, 430, size=96, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=10)
            draw_sparkles(img, t, CX, 960, 420, n=8, seed=20 + i, start=0.1)
            paste_sticker(img, st, CX, 960 + bob(t, 8), size=760, scale=pop(t, 0, 0.45), rot=wobble(t, 3))
            draw_pill(img, label, CX, 1470, size=48, fill=WHITE, text_fill=INK, scale=pop(t, 0.3), shadow=ctx.shape_color)
        scenes += [Scene(timer, draw_q), Scene(2.0, draw_a)]
    scenes.append(cta_scene(ctx, cta, rounds[-1][1], 2.0))
    return scenes


register(FormatSpec(
    "F03", "그림자 퀴즈", "참여형",
    '스티커 실루엣 제시 → "정답은?" 카운트 → 정답 공개 (라운드 반복)',
    [Field("title", "타이틀", "text", "이 스티커의 정체는?"),
     Field("rounds", "라운드 (한 줄에 '정답 문구|스티커번호')", "pairs",
           lambda pk: "\n".join(f"정답 공개!|{i}" for i in spread(len(pk), 3, 2)), n=3),
     Field("timer", "생각할 시간(초)", "float", 3, min=1, max=6),
     Field("cta", "마무리 CTA", "text", "몇 개 맞췄는지 댓글로!")],
    build_f03))


# ── F04 이지선다 ─────────────────────────────────────────────
def build_f04(ctx: Ctx, p: dict) -> List[Scene]:
    q = P(p, "question", "오늘의 지큐, 어느 쪽?")
    a_label, b_label = P(p, "a_label", "A. 신나는 지큐"), P(p, "b_label", "B. 졸린 지큐")
    a_idx, b_idx = int(P(p, "a_sticker", 1)), int(P(p, "b_sticker", len(ctx.pack)))
    cta = P(p, "cta", "꾹 눌러 멈추고, 댓글로 알려줘!")
    sub = P(p, "sub", "친구 태그해서 같이 골라보기")
    A, B = ctx.st(a_idx), ctx.st(b_idx)
    top_y, bot_y, mid = 690, 1330, 1010

    def draw(t, img):
        draw_text(img, q, CX, SAFE_TOP + 110, size=72, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10,
                  max_w=W - 160)
        d = ImageDraw.Draw(img)
        # 두 영역 카드
        draw_card(img, (80, 400, W - 80, mid - 40), radius=56, fill=WHITE, shadow=None)
        draw_card(img, (80, mid + 40, W - 80, 1640), radius=56, fill=WHITE, shadow=None)
        sa, sb = pop(t, 0.1, 0.45), pop(t, 0.35, 0.45)
        paste_sticker(img, A, 330, top_y + bob(t, 8), size=400, height=440, scale=sa, rot=wobble(t, 2.5))
        paste_sticker(img, B, 750, bot_y + bob(t, 8, phase=0.5), size=400, height=440, scale=sb, rot=wobble(t, 2.5, phase=0.5))
        draw_text(img, a_label, 785, top_y, size=58, fill=INK, max_w=330, alpha=fade(t, 0.3, 0.3), fit_lines=3)
        draw_text(img, b_label, 295, bot_y, size=58, fill=INK, max_w=330, alpha=fade(t, 0.55, 0.3), fit_lines=3)
        # VS 배지
        s = pop(t, 0.6, 0.4)
        draw_badge(img, "VS", CX, mid, r=86, fill=ctx.accent, size=68, scale=s, outline=WHITE, ow=10)
        # 깜빡이는 선택 강조
        blink = (math.sin(t * 4) > 0)
        if t > 1.2:
            box = (80, 400, W - 80, mid - 40) if blink else (80, mid + 40, W - 80, 1640)
            d.rounded_rectangle(box, radius=56, outline=ctx.accent, width=14)

    return [title_scene(ctx, q, 1.4, sub="둘 중 하나만 골라!"), Scene(4.5, draw), cta_scene(ctx, cta, a_idx, 2.2, sub)]


register(FormatSpec(
    "F04", "이지선다", "참여형",
    '질문 → A vs B → "꾹 눌러 확인, 댓글로 알려줘" + 친구 태그',
    [Field("question", "질문", "text", "오늘의 지큐, 어느 쪽?"),
     Field("a_label", "A 라벨", "text", "A. 신나는 지큐"),
     Field("a_sticker", "A 스티커 번호", "sticker", lambda pk: 1),
     Field("b_label", "B 라벨", "text", "B. 졸린 지큐"),
     Field("b_sticker", "B 스티커 번호", "sticker", lambda pk: len(pk)),
     Field("cta", "마무리 CTA", "text", "꾹 눌러 멈추고, 댓글로 알려줘!"),
     Field("sub", "보조 문구", "text", "친구 태그해서 같이 골라보기")],
    build_f04))


# ── F05 멈춰서 뽑기 ──────────────────────────────────────────
def build_f05(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "화면을 멈춰보세요!")
    sub = P(p, "sub", "오늘의 나는 어떤 지큐?")
    idxs = as_int_list(P(p, "stickers"), list(range(1, len(ctx.pack) + 1)))
    speed = float(P(p, "speed", 8))
    dur = float(P(p, "duration", 8))
    cta = P(p, "cta", "멈춘 번호를 댓글로!")
    idxs = list(idxs); random.Random(rseed(p)).shuffle(idxs)  # 매번 다른 순서
    sts = [ctx.st(i) for i in idxs]

    def static(img):
        draw_card(img, (110, 560, W - 110, 1460), radius=64, fill=WHITE, shadow=ctx.shape_color + (255,), offset=14)

    def draw(t, img):
        draw_text(img, title, CX, SAFE_TOP + 110, size=84, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        draw_pill(img, sub, CX, SAFE_TOP + 230, size=40, fill=ctx.accent, text_fill=WHITE)
        k = int(t * speed) % len(sts)
        sfx_change("tick", int(t * speed), 0.6)
        paste_sticker(img, sts[k], CX, 990, size=720, height=700)
        draw_badge(img, idxs[k], W - 190, 640, r=54, fill=ctx.accent, size=48)
        draw_text(img, cta, CX, 1560, size=56, fill=ctx.ink, stroke=WHITE, stroke_w=8)
    return [Scene(dur, draw, static)]


register(FormatSpec(
    "F05", "멈춰서 뽑기", "참여형",
    '스티커가 빠르게 넘어감 → "화면을 멈춰보세요" (루프용)',
    [Field("title", "타이틀", "text", "화면을 멈춰보세요!"),
     Field("sub", "보조 문구", "text", "오늘의 나는 어떤 지큐?"),
     Field("stickers", "사용할 스티커 번호들", "stickers", lambda pk: list(range(1, len(pk) + 1))),
     Field("speed", "초당 전환 수", "float", 8, min=2, max=20),
     Field("duration", "길이(초)", "float", 8, min=4, max=20),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "하단 문구", "text", "멈춘 번호를 댓글로!")],
    build_f05))


# ── F06 캐릭터 경주 예측 ─────────────────────────────────────
def build_f06(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "먼저 도착할 캐릭터는 몇 번?")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 5))[:5]
    while len(idxs) < 5:
        idxs.append(idxs[-1] if idxs else 1)
    winner = int(P(p, "winner", 0))
    seed = rseed(p)
    rng = random.Random(seed)
    if not (1 <= winner <= 5):
        winner = rng.randint(1, 5)
    race_dur = float(P(p, "duration", 5))
    ctx.meta["결과"] = f"{winner}번 우승 (스티커 {idxs[winner - 1]})"
    cta = P(p, "cta", "맞췄으면 댓글로 자랑해!")
    sts = [ctx.st(i) for i in idxs]
    lane_y = [520 + i * 210 for i in range(5)]
    x0, x1 = 210, W - 170
    order = [i for i in range(5) if i != winner - 1]
    rng.shuffle(order)
    finish = {winner - 1: race_dur * 0.9}
    for k, i in enumerate(order):
        finish[i] = race_dur * (1.06 + 0.16 * k)
    phases = [rng.uniform(0, 6.28) for _ in range(5)]

    def lanes(img):
        d = ImageDraw.Draw(img)
        for i, y in enumerate(lane_y):
            d.rounded_rectangle((90, y - 90, W - 90, y + 90), radius=44, fill=WHITE)
            d.line((x1 + 20, y - 80, x1 + 20, y + 80), fill=INK, width=6)
            for j in range(8):  # 체커 깃발
                for kk in range(2):
                    if (j + kk) % 2 == 0:
                        d.rectangle((x1 + 24 + kk * 16, y - 80 + j * 20, x1 + 40 + kk * 16, y - 60 + j * 20), fill=INK)
        for i, y in enumerate(lane_y):
            draw_badge(img, i + 1, 150, y, r=42, fill=ctx.accent, size=44)

    def prog(i, t):
        f = finish[i]
        base = ease_in_out(t / f) if t < f else 1.0
        wob = 0.025 * math.sin(t * 5 + phases[i]) * (1 if base < 0.95 else 0)
        pv = clamp(base + wob)
        if i != winner - 1 and t < finish[winner - 1]:
            pv = min(pv, 0.93)
        return pv

    def draw_intro(t, img):
        draw_text(img, title, CX, SAFE_TOP + 110, size=72, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        lanes(img)
        for i, y in enumerate(lane_y):
            paste_sticker(img, sts[i], x0 + 80, y + bob(t, 4, phase=i / 5), size=170, height=165, scale=pop(t, 0.1 * i, 0.4))
        draw_pill(img, "댓글로 예측해봐!", CX, 1650, size=42, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.6))

    def draw_race(t, img):
        sfx_once("whistle", t, 0.0, 0.8)
        sfx_loop("run", 0.3, finish[winner - 1], 0.6)
        sfx_once("cheer", t, finish[winner - 1], 0.9)
        draw_text(img, title, CX, SAFE_TOP + 110, size=72, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        lanes(img)
        for i, y in enumerate(lane_y):
            pv = prog(i, t)
            x = lerp(x0 + 80, x1 - 60, pv)
            paste_sticker(img, sts[i], x, y + bob(t, 6, 0.35, phase=i / 5), size=170, height=165, rot=wobble(t, 6, 0.35, phase=i / 5))
        if t < 1.0:
            draw_text(img, "START!", CX, 1650, size=90, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=10)
        elif t > finish[winner - 1]:
            draw_text(img, f"{winner}번 도착!", CX, 1650, size=90, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=10)

    def draw_result(t, img):
        draw_confetti(img, t, seed=seed + 1)
        draw_text(img, f"{winner}번 우승!", CX, 430, size=110, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=12)
        paste_sticker(img, sts[winner - 1], CX, 960 + bob(t, 10), size=760, scale=pop(t, 0, 0.45), rot=wobble(t, 3))
        draw_sparkles(img, t, CX, 960, 400, n=9, seed=9)
        draw_text(img, cta, CX, 1440, size=64, fill=ctx.ink, stroke=WHITE, stroke_w=9, alpha=fade(t, 0.3), max_w=W - 160)

    return [Scene(2.2, draw_intro), Scene(race_dur + 0.8, draw_race), Scene(2.6, draw_result)]


register(FormatSpec(
    "F06", "캐릭터 경주 예측", "참여형",
    "번호 붙은 캐릭터 5종 출발선 정렬 → 달리기 경주 → 결과 도착 → 댓글로 예측 유도",
    [Field("title", "타이틀", "text", "먼저 도착할 캐릭터는 몇 번?"),
     Field("stickers", "출전 스티커 5개", "stickers", lambda pk: spread(len(pk), 5), n=5),
     Field("winner", "우승 번호(1~5, 0=랜덤)", "int", 0, min=0, max=5),
     Field("duration", "경주 시간(초)", "float", 5, min=3, max=10),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "마무리 CTA", "text", "맞췄으면 댓글로 자랑해!")],
    build_f06))


# ── F07 사다리 타기 ──────────────────────────────────────────
def build_f07(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "내 운명의 지큐는?")
    labels = as_lines(P(p, "labels"), ["A", "B", "C", "D", "E"])[:5]
    while len(labels) < 5:
        labels.append(chr(65 + len(labels)))
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 5, 1))[:5]
    while len(idxs) < 5:
        idxs.append(idxs[-1] if idxs else 1)
    pick = int(P(p, "pick", 0))
    seed = rseed(p)
    cta = P(p, "cta", "네 결과도 댓글로 알려줘!")
    rng = random.Random(seed)
    if not (1 <= pick <= 5):
        pick = rng.randint(1, 5)
    n = 5
    xs = [190 + i * (W - 380) / (n - 1) for i in range(n)]
    y_top, y_bot = 600, 1330
    levels = [y_top + 60 + k * (y_bot - y_top - 120) / 9 for k in range(10)]
    rungs = []  # (level_idx, col)  col~col+1
    for li in range(10):
        used = set()
        for c in rng.sample(range(n - 1), k=rng.choice([1, 1, 2])):
            if c in used or c - 1 in used or c + 1 in used:
                continue
            used.add(c); rungs.append((li, c))
    rset = set(rungs)
    # 경로 계산
    col = pick - 1
    path = [(xs[col], y_top)]
    for li, y in enumerate(levels):
        path.append((xs[col], y))
        if (li, col) in rset:
            col += 1; path.append((xs[col], y))
        elif (li, col - 1) in rset:
            col -= 1; path.append((xs[col], y))
    path.append((xs[col], y_bot))
    result_col = col
    ctx.meta["결과"] = f"선택 {labels[pick - 1]} → 결과 스티커 {idxs[result_col]}"
    seg_len = [math.dist(path[i], path[i + 1]) for i in range(len(path) - 1)]
    total_len = sum(seg_len)
    sts = [ctx.st(i) for i in idxs]

    def ladder(img):
        d = ImageDraw.Draw(img)
        draw_card(img, (70, y_top - 130, W - 70, y_bot + 130), radius=56, fill=WHITE, shadow=None)
        for x in xs:
            d.line((x, y_top, x, y_bot), fill=(200, 205, 220), width=12)
        for li, c in rungs:
            d.line((xs[c], levels[li], xs[c + 1], levels[li]), fill=(200, 205, 220), width=12)
        for i, x in enumerate(xs):
            draw_pill(img, labels[i], x, y_top - 70, size=36, fill=ctx.accent, text_fill=WHITE, padx=22, pady=12)
        for i, x in enumerate(xs):
            paste_sticker(img, sts[i], x, y_bot + 70, size=150, height=120)

    def draw_intro(t, img):
        draw_text(img, title, CX, SAFE_TOP + 110, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        ladder(img)
        draw_pill(img, "하나 골라서 따라가 보자!", CX, 1610, size=42, fill=WHITE, text_fill=INK, scale=pop(t, 0.3), shadow=ctx.shape_color)

    def draw_trace(t, img):
        draw_text(img, title, CX, SAFE_TOP + 110, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        ladder(img)
        d = ImageDraw.Draw(img)
        L = total_len * ease_in_out(t / 4.0)
        acc = 0.0; head = path[0]
        for i, sl in enumerate(seg_len):
            if acc + sl <= L:
                d.line((path[i], path[i + 1]), fill=ctx.accent, width=18); acc += sl; head = path[i + 1]
            else:
                f = (L - acc) / sl if sl else 0
                head = (lerp(path[i][0], path[i + 1][0], f), lerp(path[i][1], path[i + 1][1], f))
                d.line((path[i], head), fill=ctx.accent, width=18)
                sfx_change("swish", i, 0.5)
                break
        r = 26
        d.ellipse((head[0] - r, head[1] - r, head[0] + r, head[1] + r), fill=ctx.accent, outline=WHITE, width=6)
        draw_pill(img, f"{labels[pick - 1]} 선택!", CX, 1610, size=42, fill=ctx.accent, text_fill=WHITE)

    def draw_result(t, img):
        draw_confetti(img, t, seed=seed + 3)
        draw_text(img, f"{labels[pick - 1]}의 운명은…", CX, 400, size=64, fill=ctx.ink, stroke=WHITE, stroke_w=9)
        paste_sticker(img, sts[result_col], CX, 940 + bob(t, 10), size=760, scale=pop(t, 0, 0.45), rot=wobble(t, 3))
        draw_sparkles(img, t, CX, 940, 400, n=9, seed=13)
        draw_text(img, cta, CX, 1440, size=64, fill=ctx.ink, stroke=WHITE, stroke_w=9, alpha=fade(t, 0.3), max_w=W - 160)

    return [Scene(2.0, draw_intro), Scene(4.6, draw_trace), Scene(2.6, draw_result)]


register(FormatSpec(
    "F07", "사다리 타기 결과 뽑기", "참여형",
    '선택지 5개 + 사다리 → "내 운명의 캐릭터는?" → 선 따라가기 → 결과 캐릭터 공개',
    [Field("title", "타이틀", "text", "내 운명의 지큐는?"),
     Field("labels", "선택지 5개 (한 줄에 하나)", "textarea", "A\nB\nC\nD\nE", n=5),
     Field("stickers", "결과 스티커 5개", "stickers", lambda pk: spread(len(pk), 5, 1), n=5),
     Field("pick", "따라갈 선택지(1~5, 0=랜덤)", "int", 0, min=0, max=5),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "마무리 CTA", "text", "네 결과도 댓글로 알려줘!")],
    build_f07))


# ── F08 유형 그리드 고르기 ───────────────────────────────────
def build_f08(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "나는 어떤 지큐일까?")
    cells = as_pairs(P(p, "cells"), [("", i) for i in spread(len(ctx.pack), 9)])
    cells = cells[:12]
    cta = P(p, "cta", "내 번호는 댓글로!")
    hold = float(P(p, "duration", 8))
    n = len(cells)
    cols = 3 if n <= 9 else 4
    rows = math.ceil(n / cols)
    has_label = any(l for l, _ in cells)
    gx0, gx1 = 70, W - 70
    gy0, gy1 = 470, 1640
    cw = (gx1 - gx0) / cols
    ch = min((gy1 - gy0) / rows, cw * (1.25 if has_label else 1.05))
    gy0 = gy0 + ((gy1 - gy0) - ch * rows) / 2
    sts = [ctx.st(i) for _, i in cells]

    def draw(t, img):
        draw_text(img, title, CX, SAFE_TOP + 100, size=78, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        for k, (label, _) in enumerate(cells):
            r, c = divmod(k, cols)
            x = gx0 + c * cw + cw / 2; y = gy0 + r * ch + ch / 2
            s = pop(t, 0.06 * k, 0.4)
            if s <= 0.01:
                continue
            bw, bh = (cw - 22) * s, (ch - 22) * s
            draw_card(img, (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2), radius=int(36 * s), fill=WHITE, shadow=None)
            sy = y - (bh * 0.1 if has_label else 0)
            paste_sticker(img, sts[k], x, sy, size=int((cw - 60) * s), height=int((ch - (110 if has_label else 60)) * s))
            draw_badge(img, k + 1, x - bw / 2 + 36 * s, y - bh / 2 + 36 * s, r=30, fill=ctx.accent, size=30, scale=s)
            if label:
                draw_text(img, label, x, y + bh / 2 - 40 * s, size=int(32 * s), fill=INK, max_w=int(cw - 30))
        draw_pill(img, cta, CX, 1720, size=42, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.06 * n + 0.2))
    return [Scene(hold, draw)]


register(FormatSpec(
    "F08", "유형 그리드 고르기", "참여형",
    "질문 타이틀 → 번호 붙은 캐릭터 유형 8~12칸 정지 화면 → 댓글로 번호",
    [Field("title", "질문 타이틀", "text", "나는 어떤 지큐일까?"),
     Field("cells", "칸 목록 (한 줄에 '라벨|번호', 라벨 생략 가능)", "pairs",
           lambda pk: "\n".join(f"|{i}" for i in spread(len(pk), 9)), n=9),
     Field("duration", "길이(초)", "float", 8, min=4, max=15),
     Field("cta", "하단 CTA", "text", "내 번호는 댓글로!")],
    build_f08, tip="관계 유형(9번)·자리 고르기(10번)도 라벨을 바꿔서 같은 포맷으로 제작"))


# ── F11 TOP5 카운트다운 ──────────────────────────────────────
def build_f11(ctx: Ctx, p: dict) -> List[Scene]:
    header = P(p, "header", "이번 주 인기 스티커 TOP 5")
    items = as_pairs(P(p, "items"), [(f"스티커 {i}", i) for i in spread(len(ctx.pack), 5)])[:5]
    cta = P(p, "cta", "OGQ마켓에서 바로 확인!")
    per = float(P(p, "per", 1.7))
    scenes = [title_scene(ctx, header, 1.5, sticker="main")]
    n = len(items)
    for k in range(n - 1, -1, -1):
        rank = k + 1; label, idx = items[k]; st = ctx.st(idx)

        def draw(t, img, rank=rank, label=label, st=st):
            sfx_once("hit", t, 0.05, 0.8)
            draw_pill(img, header, CX, SAFE_TOP + 70, size=38, fill=WHITE, text_fill=INK)
            if rank == 1:
                draw_confetti(img, t, seed=31)
            s = pop(t, 0, 0.4)
            draw_text(img, f"{rank}", 200, 560, size=int(240 * max(s, 0.2)), font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=14)
            draw_text(img, "위", 200 + 130, 620, size=80, font_name="title", fill=ctx.ink, alpha=fade(t, 0.15))
            paste_sticker(img, st, CX + 60, 1000 + bob(t, 10), size=700, height=640, scale=pop(t, 0.1, 0.45), rot=wobble(t, 2.5))
            if rank == 1:
                draw_sparkles(img, t, CX + 60, 1000, 400, n=10, seed=32)
            draw_pill(img, label, CX, 1440, size=50, fill=ctx.accent if rank == 1 else WHITE,
                      text_fill=WHITE if rank == 1 else INK, scale=pop(t, 0.3), shadow=ctx.shape_color)
        scenes.append(Scene(per, draw))
    scenes.append(cta_scene(ctx, cta, items[0][1], 2.2))
    return scenes


register(FormatSpec(
    "F11", "신상/인기 스티커 TOP 5", "서비스 연결형",
    "5위 → 1위 카운트다운, 컷마다 스티커와 작품명 → 마켓 유도",
    [Field("header", "헤더", "text", "이번 주 인기 스티커 TOP 5"),
     Field("items", "1위→5위 순서로 '작품명|스티커번호'", "pairs",
           lambda pk: "\n".join(f"스티커 {i}|{i}" for i in spread(len(pk), 5)), n=5),
     Field("per", "컷당 길이(초)", "float", 1.7, min=1, max=3),
     Field("cta", "마무리 CTA", "text", "OGQ마켓에서 바로 확인!")],
    build_f11))


# ── F12 캐릭터 소개 ──────────────────────────────────────────
def build_f12(ctx: Ctx, p: dict) -> List[Scene]:
    name = P(p, "name", ctx.pack.name)
    creator = P(p, "creator", "by 크리에이터")
    tagline = P(p, "tagline", "새로 나온 스티커!")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 4, 3))[:4]
    cta = P(p, "cta", "OGQ마켓에서 만나요")
    sts = [ctx.st(i) for i in idxs]
    main = ctx.pack.main

    def draw_intro(t, img):
        draw_pill(img, "NEW", 200, SAFE_TOP + 60, size=44, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.5))
        draw_confetti(img, t, seed=41, n=30)
        paste_sticker(img, main, CX, 880 + bob(t, 10), size=720, scale=pop(t, 0, 0.5), rot=wobble(t, 3))
        draw_sparkles(img, t, CX, 880, 400, n=8, seed=42, start=0.3)
        draw_text(img, name, CX, 1360, size=84, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, alpha=fade(t, 0.3), max_w=W - 160)
        draw_text(img, tagline, CX, 1490, size=48, fill=GRAY, alpha=fade(t, 0.5))

    scenes = [Scene(2.4, draw_intro)]
    for k, st in enumerate(sts):
        def draw(t, img, st=st, k=k):
            sfx_once("whoosh", t, 0.0, 0.7)
            draw_pill(img, name, CX, SAFE_TOP + 70, size=40, fill=WHITE, text_fill=INK)
            x_from = -600 if k % 2 == 0 else W + 600
            x = lerp(x_from, CX, ease_out_cubic(t / 0.45))
            paste_sticker(img, st, x, 960 + bob(t, 10), size=780, rot=wobble(t, 3) + (8 if k % 2 == 0 else -8) * (1 - ease_out_cubic(t / 0.45)))
            draw_badge(img, f"{k + 1}/{len(sts)}", W - 170, SAFE_TOP + 70, r=48, fill=ctx.accent, size=30)
        scenes.append(Scene(1.25, draw))

    def draw_end(t, img):
        draw_text(img, name, CX, 400, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 160)
        draw_text(img, creator, CX, 520, size=44, fill=GRAY)
        n = len(sts); cw = (W - 160) / n
        for k, st in enumerate(sts):
            x = 80 + cw * (k + 0.5)
            paste_sticker(img, st, x, 900 + bob(t, 6, phase=k / n), size=int(cw - 20), height=300, scale=pop(t, 0.1 * k, 0.4))
        paste_sticker(img, main, CX, 1250 + bob(t, 8), size=380, scale=pop(t, 0.5, 0.4))
        draw_pill(img, cta, CX, 1520, size=46, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.7))
    scenes.append(Scene(2.8, draw_end))
    return scenes


register(FormatSpec(
    "F12", "캐릭터 소개 (신규/추천)", "서비스 연결형",
    "캐릭터 등장 → 대표 스티커 3~4컷 → 작품명·크리에이터",
    [Field("name", "작품명", "text", lambda pk: pk.name),
     Field("creator", "크리에이터", "text", "by 크리에이터"),
     Field("tagline", "한 줄 소개", "text", "새로 나온 스티커!"),
     Field("stickers", "대표 스티커 3~4개", "stickers", lambda pk: spread(len(pk), 4, 3), n=4),
     Field("cta", "마무리 CTA", "text", "OGQ마켓에서 만나요")],
    build_f12))


# ── F13 스티커로만 대화하기 ──────────────────────────────────
def build_f13(ctx: Ctx, p: dict) -> List[Scene]:
    chat_name = P(p, "chat_name", ctx.pack.name)
    msgs = as_lines(P(p, "messages"), ["L|1", "R|오늘 뭐해?", "L|3", "R|같이 놀자!!", "L|5", "R|6", "L|점심은?", "R|10"])
    interval = float(P(p, "interval", 0.9))
    hold = float(P(p, "hold", 2.0))
    # 파싱
    items = []
    for m in msgs:
        side, _, body = m.partition("|")
        side = side.strip().upper()
        if side not in ("L", "R"):
            side, body = "L", m
        body = body.strip()
        if body.isdigit():
            items.append((side, "st", int(body)))
        else:
            items.append((side, "tx", body))
    # 레이아웃
    px0, px1 = 70, W - 70
    top, bottom = 560, 1620
    gap = 26
    bubbles = []  # (side, kind, val, h)
    for side, kind, val in items:
        if kind == "st":
            st = ctx.st(val); im = fit(st, 330, 300); bubbles.append((side, kind, im, im.height))
        else:
            fnt = font("semi", 40)
            lines = wrap_text(val, fnt, 560)
            h = len(lines) * 52 + 40
            bubbles.append((side, kind, lines, h))
    ys = []; y = top
    for b in bubbles:
        ys.append(y); y += b[3] + gap
    total_dur = interval * len(bubbles) + hold

    def static(img):
        draw_card(img, (px0 - 20, 400, px1 + 20, 1700), radius=64, fill=(244, 246, 250), shadow=ctx.shape_color + (255,), offset=14)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((px0 - 20, 400, px1 + 20, 540), radius=64, fill=WHITE)
        d.rectangle((px0 - 20, 480, px1 + 20, 540), fill=WHITE)
        d.line((px0 - 20, 540, px1 + 20, 540), fill=(225, 228, 236), width=3)
        av = ctx.pack.main
        d.ellipse((px0 + 30, 425, px0 + 120, 515), fill=ctx.bg_color)
        paste_sticker(img, av, px0 + 75, 470, size=76, height=76)
        d.text((px0 + 150, 470), chat_name, font=font("bold", 40), fill=INK, anchor="lm")
        d.text((px1 - 40, 470), "● 온라인", font=font("semi", 28), fill=(80, 190, 120), anchor="rm")

    def draw(t, img):
        k = min(len(bubbles), int(t / interval) + 1)
        sfx_change("tok", k, 0.8)
        # 스크롤 오프셋(마지막 표시 버블이 bottom 안에 오도록)
        def off_for(kk):
            if kk <= 0:
                return 0
            b = ys[kk - 1] + bubbles[kk - 1][3]
            return max(0, b - bottom)
        cur = off_for(k); prev = off_for(k - 1)
        f = ease_out_cubic(((t - (k - 1) * interval)) / 0.3)
        off = lerp(prev, cur, f)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for i in range(k):
            side, kind, val, h = bubbles[i]
            y = ys[i] - off
            if y + h < top - 40 or y > bottom + 40:
                continue
            s = pop(t, i * interval, 0.32) if i == k - 1 else 1.0
            if kind == "st":
                im = val
                x = px0 + 40 if side == "L" else px1 - 40 - im.width
                if s < 0.999:
                    im2 = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))))
                    ox = 0 if side == "L" else im.width - im2.width
                    layer.paste(im2, (int(x + ox), int(y + h - im2.height)), im2)
                else:
                    layer.paste(im, (int(x), int(y)), im)
            else:
                lines = val; fnt = font("semi", 40)
                tw = max(text_size(l, fnt)[0] for l in lines) + 60
                bw, bh = tw * s, h * s
                if side == "L":
                    box = (px0 + 40, y + h - bh, px0 + 40 + bw, y + h)
                    fill, tf = WHITE, INK
                else:
                    box = (px1 - 40 - bw, y + h - bh, px1 - 40, y + h)
                    fill, tf = ctx.accent, WHITE
                d.rounded_rectangle(box, radius=30, fill=fill)
                if s > 0.9:
                    for j, line in enumerate(lines):
                        d.text((box[0] + 30, box[1] + 20 + j * 52 + 26), line, font=fnt, fill=tf, anchor="lm")
        # 채팅 영역 밖은 가리기
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rectangle((px0 - 20, 545, px1 + 20, 1700), fill=255)
        layer.putalpha(Image.composite(layer.split()[3], Image.new("L", img.size, 0), mask))
        img.alpha_composite(layer)
        draw_text(img, P(p, "title", "스티커로만 대화하기"), CX, SAFE_TOP + 90, size=66, font_name="title",
                  fill=ctx.ink, stroke=WHITE, stroke_w=10)
    return [Scene(total_dur, draw, static)]


register(FormatSpec(
    "F13", "스티커로만 대화하기", "서비스 연결형",
    "채팅 화면에서 스티커/말풍선이 순서대로 올라옴",
    [Field("title", "타이틀", "text", "스티커로만 대화하기"),
     Field("chat_name", "채팅 상대 이름", "text", lambda pk: pk.name),
     Field("messages", "메시지 (한 줄에 'L|스티커번호' 또는 'R|텍스트')", "textarea",
           "L|1\nR|오늘 뭐해?\nL|3\nR|같이 놀자!!\nL|5\nR|6\nL|점심은?\nR|10"),
     Field("interval", "메시지 간격(초)", "float", 0.9, min=0.4, max=2),
     Field("hold", "마지막 정지(초)", "float", 2.0, min=0.5, max=5)],
    build_f13))


# ── F14 시즌/기념일 ──────────────────────────────────────────
def build_f14(ctx: Ctx, p: dict) -> List[Scene]:
    greeting = P(p, "greeting", "해피 추석!")
    sub = P(p, "sub", "풍성한 한가위 보내세요")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 3, 4))[:3]
    cta = P(p, "cta", "따뜻한 마음, 스티커로 전해요")
    sts = [ctx.st(i) for i in idxs]

    def draw_end(t, img):
        draw_confetti(img, t, seed=51, n=36)
        draw_text(img, greeting, CX, 480, size=112, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=12, max_w=W - 140)
        n = len(sts); cw = (W - 120) / n
        for k, st in enumerate(sts):
            x = 60 + cw * (k + 0.5)
            paste_sticker(img, st, x, 1000 + bob(t, 10, phase=k / n), size=int(cw - 24), height=460,
                          scale=pop(t, 0.12 * k, 0.45), rot=wobble(t, 3, phase=k / n))
        draw_pill(img, cta, CX, 1420, size=46, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.5))
    return ([title_scene(ctx, greeting, 1.8, sub=sub, size=112)]
            + sequence_scenes(ctx, greeting, [(sub, i) for i in idxs], per=1.3, label_size=48)
            + [Scene(2.6, draw_end)])


register(FormatSpec(
    "F14", "시즌/기념일", "서비스 연결형",
    "시즌 인사 문구 + 관련 캐릭터 2~3컷",
    [Field("greeting", "인사 문구", "text", "해피 추석!"),
     Field("sub", "보조 문구", "text", "풍성한 한가위 보내세요"),
     Field("stickers", "스티커 2~3개", "stickers", lambda pk: spread(len(pk), 3, 4), n=3),
     Field("cta", "마무리 문구", "text", "따뜻한 마음, 스티커로 전해요")],
    build_f14))


# ── F16 귀여움 5초 충전 ──────────────────────────────────────
def build_f16(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "귀여움 5초 충전")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 5))[:8]
    per = float(P(p, "per", 1.0))
    done = P(p, "done", "충전 완료!")
    sts = [ctx.st(i) for i in idxs]
    total = per * len(sts)

    def draw(t, img):
        draw_text(img, title, CX, SAFE_TOP + 110, size=84, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        # 배터리 바
        d = ImageDraw.Draw(img)
        bx0, bx1, by = 140, W - 180, 520
        d.rounded_rectangle((bx0, by - 40, bx1, by + 40), radius=40, fill=WHITE, outline=INK, width=6)
        d.rounded_rectangle((bx1 + 8, by - 18, bx1 + 30, by + 18), radius=8, fill=INK)
        pv = clamp(t / total)
        if pv > 0.02:
            d.rounded_rectangle((bx0 + 10, by - 30, bx0 + 10 + (bx1 - bx0 - 20) * pv, by + 30), radius=30, fill=ctx.accent)
        d.text((CX - 20, by), f"{int(pv * 100)}%", font=font("bold", 40), fill=INK if pv < 0.55 else WHITE, anchor="mm")
        k = min(len(sts) - 1, int(t / per)); tl = t - k * per
        sfx_change("charge", k, 0.45)
        paste_sticker(img, sts[k], CX, 1040 + bob(tl, 10, 1.0), size=780, scale=pop(tl, 0, 0.4), rot=wobble(tl, 3, 1.0))
        draw_sparkles(img, t, CX, 1040, 420, n=6, seed=60 + k)

    def draw_done(t, img):
        draw_confetti(img, t, seed=61)
        draw_text(img, done, CX, 480, size=120, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=12)
        n = len(sts); cw = (W - 100) / min(n, 5)
        for k, st in enumerate(sts[:5]):
            x = 50 + cw * (k + 0.5)
            paste_sticker(img, st, x, 1000 + bob(t, 8, phase=k / 5), size=int(cw - 16), height=260, scale=pop(t, 0.08 * k, 0.4))
        paste_sticker(img, ctx.pack.main, CX, 1400 + bob(t, 8), size=380, scale=pop(t, 0.5, 0.4))
    return [Scene(total, draw), Scene(2.0, draw_done)]


register(FormatSpec(
    "F16", "귀여움 5초 충전", "귀여움/중독형",
    "캐릭터 5종이 1초씩 연속 등장 (충전 게이지) → 충전 완료",
    [Field("title", "타이틀", "text", "귀여움 5초 충전"),
     Field("stickers", "스티커 5개", "stickers", lambda pk: spread(len(pk), 5), n=5),
     Field("per", "컷당 길이(초)", "float", 1.0, min=0.5, max=2),
     Field("done", "완료 문구", "text", "충전 완료!")],
    build_f16))


# ── F18 주파수 ───────────────────────────────────────────────
def build_f18(ctx: Ctx, p: dict) -> List[Scene]:
    text = P(p, "text", "보기만 해도 기분 좋아지는 주파수")
    idx = int(P(p, "sticker", 1))
    dur = float(P(p, "duration", 8))
    hz = P(p, "hz", "528Hz")
    st = ctx.st(idx)
    cy = 1000

    def draw(t, img):
        sfx_loop("hum", 0, dur, 0.5)
        sfx_change("sparkle", int(t / 1.5), 0.3)
        def rings(d):
            for k in range(4):
                ph = (t * 0.45 + k / 4) % 1.0
                r = 330 + 380 * ph
                a = int(170 * (1 - ph))
                d.ellipse((CX - r, cy - r, CX + r, cy + r), outline=(160, 190, 255, a), width=10)
        overlay(img, rings)
        # 반짝이는 별
        rng = random.Random(99); d = ImageDraw.Draw(img)
        for i in range(30):
            x, y = rng.uniform(40, W - 40), rng.uniform(300, 1700)
            tw = 0.5 + 0.5 * math.sin(2 * math.pi * (t * rng.uniform(0.4, 1.2) + rng.random()))
            r = 12 * tw
            d.polygon(star_points(x, y, r), fill=(255, 240, 200))
        paste_sticker(img, st, CX, cy + bob(t, 12, 2.4), size=760, scale=0.98 + 0.03 * math.sin(t * 2.2), rot=wobble(t, 2, 2.4))
        draw_text(img, text, CX, 440, size=72, font_name="title", fill=WHITE, max_w=W - 160, alpha=fade(t, 0.1, 0.5))
        draw_pill(img, hz, CX, 1500, size=48, fill=(255, 255, 255), text_fill=(40, 40, 90), scale=pop(t, 0.4))
    return [Scene(dur, draw)]


register(FormatSpec(
    "F18", "주파수", "귀여움/중독형",
    "우주 배경 + '~하게 되는 주파수' + 스티커 (배경 스타일이 자동으로 우주로 바뀜)",
    [Field("text", "주파수 문구", "text", "보기만 해도 기분 좋아지는 주파수"),
     Field("hz", "주파수 라벨", "text", "528Hz"),
     Field("sticker", "스티커 번호", "sticker", lambda pk: 1),
     Field("duration", "길이(초)", "float", 8, min=4, max=15)],
    build_f18, tip="배경 스타일 'space' 사용 권장(자동 적용)"))


# ── F19 상황별 표정 ──────────────────────────────────────────
def build_f19(ctx: Ctx, p: dict) -> List[Scene]:
    situation = P(p, "situation", "월급날 내 모습")
    cuts = as_pairs(P(p, "cuts"), [("아침: 통장 확인", 4), ("점심: 플렉스", 22), ("저녁: 카드값 확인", 19)])
    cuts = [(l, i) for l, i in cuts]
    per = float(P(p, "per", 1.5))
    cta = P(p, "cta", "공감되면 저장!")
    return ([title_scene(ctx, situation, 1.5, sticker="main")]
            + sequence_scenes(ctx, situation, cuts, per=per)
            + [cta_scene(ctx, cta, cuts[-1][1], 1.8)])


register(FormatSpec(
    "F19", "상황별 표정", "공감형",
    "상황 문구 → 상황별 캐릭터 리액션 컷 3~4개",
    [Field("situation", "상황 문구", "text", "월급날 내 모습"),
     Field("cuts", "컷 목록 (한 줄에 '라벨|스티커번호')", "pairs",
           lambda pk: "\n".join(f"{l}|{i}" for l, i in zip(["아침: 통장 확인", "점심: 플렉스", "저녁: 카드값 확인"], spread(len(pk), 3, 3))), n=3),
     Field("per", "컷당 길이(초)", "float", 1.5, min=0.8, max=3),
     Field("cta", "마무리 CTA", "text", "공감되면 저장!")],
    build_f19))


# ── F20 요일별 내 상태 ───────────────────────────────────────
def build_f20(ctx: Ctx, p: dict) -> List[Scene]:
    header = P(p, "header", "요일별 내 상태")
    cuts = as_pairs(P(p, "cuts"), [(f"{d}요일의 나", i) for d, i in zip("월화수목금", spread(len(ctx.pack), 5))])
    per = float(P(p, "per", 1.4))
    cta = P(p, "cta", "너는 무슨 요일이 제일 힘들어?")
    return ([title_scene(ctx, header, 1.4, sticker="main")]
            + sequence_scenes(ctx, header, cuts, per=per, label_size=64)
            + [cta_scene(ctx, cta, cuts[-1][1], 2.0)])


register(FormatSpec(
    "F20", "요일별 내 상태", "공감형",
    '"월요일의 나" → "금요일의 나" 대비 2~5컷',
    [Field("header", "헤더", "text", "요일별 내 상태"),
     Field("cuts", "컷 목록 (한 줄에 '라벨|스티커번호')", "pairs",
           lambda pk: "\n".join(f"{d}요일의 나|{i}" for d, i in zip("월화수목금", spread(len(pk), 5))), n=5),
     Field("per", "컷당 길이(초)", "float", 1.4, min=0.8, max=3),
     Field("cta", "마무리 CTA", "text", "너는 무슨 요일이 제일 힘들어?")],
    build_f20))


# ── F21 직장인 하루 ──────────────────────────────────────────
def build_f21(ctx: Ctx, p: dict) -> List[Scene]:
    header = P(p, "header", "직장인의 하루")
    cuts = as_pairs(P(p, "cuts"), [(l, i) for l, i in zip(["출근", "회의", "점심", "퇴근"], spread(len(ctx.pack), 4, 2))])
    per = float(P(p, "per", 1.4))
    cta = P(p, "cta", "오늘도 고생했어!")
    scenes = [title_scene(ctx, header, 1.4, sticker="main")]
    n = len(cuts)
    for k, (label, idx) in enumerate(cuts):
        st = ctx.st(idx)

        def draw(t, img, st=st, label=label, k=k):
            draw_pill(img, header, CX, SAFE_TOP + 80, size=44, fill=ctx.accent, text_fill=WHITE)
            # 진행 스텝
            d = ImageDraw.Draw(img)
            sx0, sx1, sy = 200, W - 200, 470
            d.line((sx0, sy, sx1, sy), fill=WHITE, width=10)
            for j in range(n):
                x = lerp(sx0, sx1, j / max(1, n - 1))
                draw_badge(img, j + 1, x, sy, r=30, fill=ctx.accent if j <= k else WHITE, text_fill=WHITE if j <= k else GRAY, size=28)
            paste_sticker(img, st, CX, 1000 + bob(t, 10), size=760, scale=pop(t, 0, 0.42), rot=wobble(t, 2.5))
            draw_text(img, label, CX, 1460, size=84, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, alpha=fade(t, 0.1))
        scenes.append(Scene(per, draw))
    scenes.append(cta_scene(ctx, cta, cuts[-1][1], 2.0))
    return scenes


register(FormatSpec(
    "F21", "직장인 하루", "공감형",
    "출근 → 회의 → 점심 → 퇴근, 컷마다 표정 교체 (진행 스텝 표시)",
    [Field("header", "헤더", "text", "직장인의 하루"),
     Field("cuts", "컷 목록 (한 줄에 '라벨|스티커번호')", "pairs",
           lambda pk: "\n".join(f"{l}|{i}" for l, i in zip(["출근", "회의", "점심", "퇴근"], spread(len(pk), 4, 2))), n=4),
     Field("per", "컷당 길이(초)", "float", 1.4, min=0.8, max=3),
     Field("cta", "마무리 CTA", "text", "오늘도 고생했어!")],
    build_f21))


# ── F23 MBTI별 반응 ──────────────────────────────────────────
def build_f23(ctx: Ctx, p: dict) -> List[Scene]:
    situation = P(p, "situation", "친구가 갑자기 놀자고 할 때")
    cells = as_pairs(P(p, "cells"), [(l, i) for l, i in zip(["E", "I", "F", "T"], spread(len(ctx.pack), 4, 1))])[:4]
    hold = float(P(p, "duration", 6))
    cta = P(p, "cta", "너는 어떤 타입?")
    sts = [ctx.st(i) for _, i in cells]
    gx0, gx1, gy0, gy1 = 70, W - 70, 480, 1620
    cw, ch = (gx1 - gx0) / 2, (gy1 - gy0) / 2

    def draw(t, img):
        draw_text(img, situation, CX, SAFE_TOP + 100, size=72, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        for k, (label, _) in enumerate(cells):
            r, c = divmod(k, 2)
            x = gx0 + c * cw + cw / 2; y = gy0 + r * ch + ch / 2
            s = pop(t, 0.25 * k, 0.45)
            if s <= 0.01:
                continue
            bw, bh = (cw - 24) * s, (ch - 24) * s
            draw_card(img, (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2), radius=int(44 * s), fill=WHITE, shadow=None)
            paste_sticker(img, sts[k], x, y - 40 * s + bob(t, 6, phase=k / 4), size=int((cw - 90) * s), height=int((ch - 200) * s))
            draw_pill(img, label, x, y + bh / 2 - 70 * s, size=int(42 * s), fill=ctx.accent, text_fill=WHITE, padx=int(34 * s), pady=int(14 * s))
        draw_pill(img, cta, CX, 1710, size=42, fill=WHITE, text_fill=INK, scale=pop(t, 1.2), shadow=ctx.shape_color)
    return [title_scene(ctx, situation, 1.4, sub="MBTI별 반응"), Scene(hold, draw)]


register(FormatSpec(
    "F23", "MBTI별 반응", "공감형",
    "상황 제시 → 4분할 화면에 유형별 캐릭터",
    [Field("situation", "상황", "text", "친구가 갑자기 놀자고 할 때"),
     Field("cells", "4칸 (한 줄에 '유형|스티커번호')", "pairs",
           lambda pk: "\n".join(f"{l}|{i}" for l, i in zip(["E", "I", "F", "T"], spread(len(pk), 4, 1))), n=4),
     Field("duration", "정지 길이(초)", "float", 6, min=3, max=12),
     Field("cta", "하단 CTA", "text", "너는 어떤 타입?")],
    build_f23))


# ── F24 감정 시각화 단일 컷 루프 ─────────────────────────────
def build_f24(ctx: Ctx, p: dict) -> List[Scene]:
    caption = P(p, "caption", "오늘 이만큼 힘듦")
    idx = int(P(p, "sticker", 1))
    effect = P(p, "effect", "steam")
    dur = float(P(p, "duration", 10))
    st = ctx.st(idx)
    cy = 1000

    def fx(t, img):
        rng = random.Random(77)
        if effect == "none":
            return

        def steam(d):
            for i in range(9):
                ph = (t * 0.28 + rng.random()) % 1.0
                x = CX + rng.uniform(-260, 260) + 40 * math.sin(t * 2 + i)
                y = cy - 200 - 520 * ph
                r = 34 + 50 * ph
                a = int(150 * (1 - ph) * ease_out_cubic(ph * 4))
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, a))

        def sweat(d):
            for i in range(7):
                sp = rng.uniform(0.6, 1.1); ph = (t * sp + rng.random()) % 1.0
                x = CX + rng.uniform(-360, 360)
                y = cy - 350 + 700 * ph
                a = int(230 * (1 - ph * 0.6))
                d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(120, 190, 255, a))
                d.polygon([(x - 12, y - 4), (x + 12, y - 4), (x, y - 34)], fill=(120, 190, 255, a))

        def hearts(d):
            for i in range(8):
                ph = (t * 0.32 + rng.random()) % 1.0
                x = CX + rng.uniform(-330, 330) + 30 * math.sin(t * 3 + i)
                y = cy + 200 - 900 * ph
                s = 22 + 18 * rng.random()
                a = int(230 * (1 - ph))
                draw_heart(d, x, y, s, (255, 120, 160, a))

        def sparkle(d):
            for i in range(10):
                x, y = CX + rng.uniform(-420, 420), cy + rng.uniform(-420, 420)
                tw = 0.5 + 0.5 * math.sin(2 * math.pi * (t * rng.uniform(0.6, 1.4) + rng.random()))
                d.polygon(star_points(x, y, 30 * tw), fill=(255, 210, 70, int(240 * tw)))

        def anger(d):
            for i in range(5):
                x, y = CX + rng.uniform(-380, 380), cy - 320 + rng.uniform(-120, 120)
                s = 26 + 14 * math.sin(t * 6 + i)
                for ang in (0, 90):
                    for sign in (1, -1):
                        aa = math.radians(ang + 45)
                        d.line((x + sign * s * math.cos(aa), y + sign * s * math.sin(aa),
                                x + sign * s * 0.3 * math.cos(aa), y + sign * s * 0.3 * math.sin(aa)),
                               fill=(255, 80, 80, 230), width=10)
        overlay(img, {"steam": steam, "sweat": sweat, "hearts": hearts, "sparkle": sparkle, "anger": anger}.get(effect, steam))

    def draw(t, img):
        if effect == "steam":
            sfx_loop("hiss", 0, dur, 0.35)
        elif effect == "hearts":
            sfx_loop("bubble", 0, dur, 0.4)
        elif effect == "sparkle":
            sfx_change("sparkle", int(t / 1.2), 0.3)
        elif effect == "anger":
            sfx_change("thud", int(t / 0.8), 0.5)
        elif effect == "sweat":
            sfx_change("tick", int(t / 0.5), 0.3)
        breathe = 1 + 0.02 * math.sin(2 * math.pi * t / 2.6)
        if effect in ("steam", "sparkle"):
            fx(t, img)
        paste_sticker(img, st, CX, cy + bob(t, 8, 2.6), size=800, scale=breathe, rot=wobble(t, 1.5, 3.2))
        if effect not in ("steam", "sparkle"):
            fx(t, img)
        draw_text(img, caption, CX, 460, size=84, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 160)
    return [Scene(dur, draw)]


register(FormatSpec(
    "F24", "감정 시각화 단일 컷 루프", "공감형",
    '캐릭터 1컷 + 이펙트(연기·땀·하트·반짝·분노) + 자막 한 줄 → 10초 정지 루프',
    [Field("caption", "자막", "text", "오늘 이만큼 힘듦"),
     Field("sticker", "스티커 번호", "sticker", lambda pk: 1),
     Field("effect", "이펙트", "choice", "steam", options=["steam", "sweat", "hearts", "sparkle", "anger", "none"]),
     Field("duration", "길이(초)", "float", 10, min=4, max=20)],
    build_f24))


# ── F25 날씨 맞춤 ────────────────────────────────────────────
def build_f25(ctx: Ctx, p: dict) -> List[Scene]:
    weather = P(p, "weather", "rain")
    idx = int(P(p, "sticker", 1))
    captions = as_lines(P(p, "captions"), ["비 오는 날엔", "집에서 뒹굴뒹굴", "우산 꼭 챙겨!"])
    per = float(P(p, "per", 2.2))
    st = ctx.st(idx)
    cy = 1000

    def weather_fx(t, img):
        rng = random.Random(88)

        def rain(d):
            for i in range(110):
                x0 = rng.uniform(-100, W + 100); sp = rng.uniform(1400, 2200); ph = rng.uniform(0, H)
                y = (ph + t * sp) % (H + 200) - 100; x = x0 - (y - ph) * 0.12
                d.line((x, y, x - 6, y + 46), fill=(120, 160, 230, 150), width=5)

        def snow(d):
            for i in range(90):
                x0 = rng.uniform(0, W); sp = rng.uniform(120, 260); ph = rng.uniform(0, H); r = rng.uniform(5, 13)
                y = (ph + t * sp) % (H + 60) - 30; x = x0 + 40 * math.sin(t * 1.2 + i)
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 235))

        def sun(d):
            sx, sy = W - 200, 330
            for k in range(12):
                ang = math.radians(k * 30 + t * 12)
                d.line((sx + 150 * math.cos(ang), sy + 150 * math.sin(ang), sx + 230 * math.cos(ang), sy + 230 * math.sin(ang)),
                       fill=(255, 210, 90, 200), width=14)
            d.ellipse((sx - 120, sy - 120, sx + 120, sy + 120), fill=(255, 220, 110, 255))

        def wind(d):
            for i in range(14):
                sp = rng.uniform(500, 900); ph = rng.uniform(0, W); y = rng.uniform(300, 1700); ln = rng.uniform(120, 300)
                x = (ph + t * sp) % (W + 400) - 200
                d.line((x, y, x + ln, y + 8 * math.sin(t * 5 + i)), fill=(255, 255, 255, 190), width=8)
        overlay(img, {"rain": rain, "snow": snow, "sun": sun, "wind": wind}.get(weather, rain))

    scenes = []
    for k, cap in enumerate(captions):
        def draw(t, img, cap=cap, k=k):
            sfx_loop({"rain": "rain", "snow": "snowwind", "sun": "birds", "wind": "wind"}.get(weather, "rain"), 0, per, 0.5)
            if weather in ("sun",):
                weather_fx(t, img)
            paste_sticker(img, st, CX, cy + bob(t, 10, 2.2), size=780, scale=pop(t, 0, 0.4) if k == 0 else 1.0, rot=wobble(t, 2, 2.2))
            if weather != "sun":
                weather_fx(t, img)
            draw_text(img, cap, CX, 460, size=88, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 160, alpha=fade(t, 0, 0.3))
        scenes.append(Scene(per, draw))
    return scenes


register(FormatSpec(
    "F25", "날씨 맞춤", "공감형",
    "날씨 이펙트(비/눈/해/바람) 배경 + 캐릭터 → 짧은 자막 컷 전환",
    [Field("weather", "날씨", "choice", "rain", options=["rain", "snow", "sun", "wind"]),
     Field("sticker", "스티커 번호", "sticker", lambda pk: 1),
     Field("captions", "자막 (한 줄에 하나, 컷마다 전환)", "textarea", "비 오는 날엔\n집에서 뒹굴뒹굴\n우산 꼭 챙겨!"),
     Field("per", "컷당 길이(초)", "float", 2.2, min=1, max=4)],
    build_f25))


# ── F02 떨어지는 스티커 받기 ─────────────────────────────────
def build_f02(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "떨어지는 지큐를 받아보세요!")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 6))
    dur = float(P(p, "duration", 6))
    cta = P(p, "cta", "몇 개 받았는지 댓글로!")
    sts = [ctx.st(i) for i in idxs]
    rng = random.Random(rseed(p))
    drops = []
    for k in range(int(dur * 1.6)):
        drops.append((rng.uniform(150, W - 150), k * dur / (dur * 1.6), rng.uniform(-90, 90), rng.uniform(0.7, 1.0), k % len(sts)))
    zone_y = 1500

    def draw_fall(t, img):
        draw_text(img, title, CX, SAFE_TOP + 110, size=72, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        d = ImageDraw.Draw(img)
        pulse = 1 + 0.04 * math.sin(t * 6)
        d.rounded_rectangle((CX - 300 * pulse, zone_y - 70, CX + 300 * pulse, zone_y + 70), radius=70, fill=WHITE, outline=ctx.accent, width=12)
        d.text((CX, zone_y), "여기서 받기 👇" if False else "여기서 받기!", font=font("bold", 46), fill=ctx.accent, anchor="mm")
        for x, t0, rot, sc, k in drops:
            tt = t - t0
            if tt < 0 or tt > 2.6:
                continue
            if tt < 1.0 / FPS - 1e-6:
                sfx_change("whoosh", round(t0, 3), 0.4)
            y = -200 + (zone_y + 250) * (tt / 2.4) ** 1.4
            paste_sticker(img, sts[k], x + 30 * math.sin(tt * 3), y, size=int(380 * sc), rot=rot + tt * 60)
    return ([title_scene(ctx, title, 1.5, sub="손가락으로 받아봐!")]
            + [Scene(dur, draw_fall), cta_scene(ctx, cta, idxs[0], 2.0, "슬라이딩 스티커는 인스타에서 추가")])


register(FormatSpec(
    "F02", "떨어지는 스티커 받기", "참여형",
    "떨어지는 스티커 애니 → 하단 받는 구역 → 결과 → 댓글 유도 (인스타 슬라이딩 스티커는 업로드 시 추가)",
    [Field("title", "타이틀", "text", "떨어지는 지큐를 받아보세요!"),
     Field("stickers", "떨어질 스티커들", "stickers", lambda pk: spread(len(pk), 6), n=6),
     Field("duration", "낙하 시간(초)", "float", 6, min=3, max=12),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "마무리 CTA", "text", "몇 개 받았는지 댓글로!")],
    build_f02, tip="장애물/슬라이딩 스티커 인터랙션은 인스타그램 앱에서 얹어야 함 — 이 영상은 그 배경용"))


# ── F09 친구 사이 유형 (F08 변형) ─────────────────────────────
_F09_LABELS = ["단짝", "밥친구", "술친구", "여행메이트", "게임메이트", "수다친구", "운동메이트", "비밀친구", "츤데레"]
register(FormatSpec(
    "F09", "우리 사이 유형 고르기", "참여형",
    '"너랑 나의 친구 사이 유형" → 관계 유형 8~9개 그리드 → "우리는 몇 번?" 친구 태그 유도',
    [Field("title", "질문 타이틀", "text", "너랑 나의 친구 사이 유형은?"),
     Field("cells", "칸 목록 (한 줄에 '관계 라벨|번호')", "pairs",
           lambda pk: "\n".join(f"{l}|{i}" for l, i in zip(_F09_LABELS, spread(len(pk), 9))), n=9),
     Field("duration", "길이(초)", "float", 8, min=4, max=15),
     Field("cta", "하단 CTA", "text", "우리는 몇 번? 친구 태그!")],
    build_f08))


# ── F10 자리/옆집 고르기 (아파트) ─────────────────────────────
def build_f10(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "어느 지큐 옆집에 살래?")
    cells = as_pairs(P(p, "cells"), [(f"{3 - k // 3}0{k % 3 + 1}호", i) for k, i in enumerate(spread(len(ctx.pack), 9))])[:12]
    cta = P(p, "cta", "원하는 호수를 댓글로!")
    hold = float(P(p, "duration", 8))
    n = len(cells); cols = 3; rows = math.ceil(n / cols)
    bx0, bx1 = 110, W - 110
    by1 = 1640; fh = min(300, (by1 - 560) / rows); by0 = by1 - fh * rows
    cw = (bx1 - bx0) / cols
    sts = [ctx.st(i) for _, i in cells]
    wall = (250, 236, 220)

    def static(img):
        d = ImageDraw.Draw(img)
        d.polygon([(bx0 - 40, by0 - 20), (CX, by0 - 190), (bx1 + 40, by0 - 20)], fill=(230, 140, 110))
        d.rectangle((bx0, by0 - 20, bx1, by1), fill=wall)
        d.rectangle((bx0, by1, bx1, by1 + 26), fill=(190, 176, 160))
        for r in range(rows + 1):
            d.line((bx0, by0 + r * fh - 20, bx1, by0 + r * fh - 20), fill=(215, 200, 185), width=6)

    def draw(t, img):
        draw_text(img, title, CX, SAFE_TOP + 80, size=76, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, max_w=W - 140)
        d = ImageDraw.Draw(img)
        for k, (label, _) in enumerate(cells):
            r, c = divmod(k, cols)
            x = bx0 + c * cw + cw / 2; y = by0 + r * fh + fh / 2 - 20
            s = pop(t, 0.07 * k, 0.4)
            if s <= 0.01:
                continue
            ww, wh = (cw - 60) * s, (fh - 70) * s
            box = (x - ww / 2, y - wh / 2, x + ww / 2, y + wh / 2)
            d.rounded_rectangle(box, radius=int(22 * s), fill=(236, 246, 255), outline=(120, 100, 90), width=int(8 * s))
            paste_sticker(img, sts[k], x, y - 8, size=int((cw - 100) * s), height=int((fh - 120) * s))
            draw_pill(img, label, x, y + wh / 2 - 4, size=int(26 * s), fill=ctx.accent, text_fill=WHITE, padx=int(16 * s), pady=int(8 * s))
        draw_pill(img, cta, CX, 1730, size=40, fill=WHITE, text_fill=INK, scale=pop(t, 0.07 * n + 0.2), shadow=ctx.shape_color)
    return [Scene(hold, draw, static)]


register(FormatSpec(
    "F10", "옆집/자리 고르기", "참여형",
    "아파트 창문마다 캐릭터 배치 → 원하는 자리 댓글",
    [Field("title", "질문 타이틀", "text", "어느 지큐 옆집에 살래?"),
     Field("cells", "칸 목록 (한 줄에 '호수 라벨|번호', 3열)", "pairs",
           lambda pk: "\n".join(f"{3 - k // 3}0{k % 3 + 1}호|{i}" for k, i in enumerate(spread(len(pk), 9))), n=9),
     Field("duration", "길이(초)", "float", 8, min=4, max=15),
     Field("cta", "하단 CTA", "text", "원하는 호수를 댓글로!")],
    build_f10))


# ── F15 출시 카운트다운 티저 ─────────────────────────────────
def _load_image(v) -> Optional[Image.Image]:
    try:
        if v is None:
            return None
        if isinstance(v, Image.Image):
            return v.convert("RGBA")
        if isinstance(v, (bytes, bytearray)):
            return Image.open(io.BytesIO(v)).convert("RGBA")
        if isinstance(v, str) and os.path.exists(v):
            return Image.open(v).convert("RGBA")
    except Exception:
        return None
    return None


def build_f15(ctx: Ctx, p: dict) -> List[Scene]:
    dday = P(p, "dday", "D-1")
    name = P(p, "name", ctx.pack.name)
    sub = P(p, "sub", "COMING SOON")
    idx = P(p, "sticker", "main")
    logo = _load_image(p.get("logo"))
    st = ctx.st(idx) if idx not in ("main", "", None) else ctx.pack.main
    fx0, fx1, fy0, fy1 = 240, W - 240, 560, 1400
    frame_col = (140, 110, 90); door_col = (222, 190, 150)

    def door(img, open_p, shake=0.0):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((fx0 - 30 + shake, fy0 - 30, fx1 + 30 + shake, fy1 + 20), radius=30, fill=frame_col)
        d.rectangle((fx0 + shake, fy0, fx1 + shake, fy1), fill=(40, 40, 60))
        pw = (fx1 - fx0) / 2 * (1 - open_p)
        if pw > 2:
            d.rectangle((fx0 + shake, fy0, fx0 + pw + shake, fy1), fill=door_col)
            d.rectangle((fx1 - pw + shake, fy0, fx1 + shake, fy1), fill=door_col)
            d.rounded_rectangle((fx0 + pw - 40 + shake, 980, fx0 + pw - 16 + shake, 1004), radius=12, fill=frame_col)
            d.rounded_rectangle((fx1 - pw + 16 + shake, 980, fx1 - pw + 40 + shake, 1004), radius=12, fill=frame_col)

    def draw_knock(t, img):
        draw_text(img, sub, CX, 400, size=64, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        shake = 10 * math.sin(t * 40) if (0.8 < t < 1.1 or 1.6 < t < 1.9) else 0
        sfx_once("knock", t, 0.8, 0.9); sfx_once("knock", t, 1.6, 0.9)
        door(img, 0.0, shake)
        if t > 0.8:
            draw_pill(img, "똑똑!", CX + 260, 620, size=48, fill=WHITE, text_fill=INK, scale=pop(t, 0.8), shadow=ctx.shape_color)
        draw_text(img, "누가 오는 걸까?", CX, 1560, size=56, fill=ctx.ink, stroke=WHITE, stroke_w=8, alpha=fade(t, 0.3))

    def draw_open(t, img):
        sfx_once("creak", t, 0.0, 0.8)
        draw_text(img, sub, CX, 400, size=64, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        op = ease_in_out(t / 1.0)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((fx0 - 30, fy0 - 30, fx1 + 30, fy1 + 20), radius=30, fill=frame_col)
        d.rectangle((fx0, fy0, fx1, fy1), fill=ctx.bg_color)
        if op > 0.15:
            paste_sticker(img, st, CX, 990 + bob(t, 8), size=int((fx1 - fx0) - 80), height=fy1 - fy0 - 60, scale=pop(t, 0.3, 0.5))
        pw = (fx1 - fx0) / 2 * (1 - op)
        if pw > 2:
            d.rectangle((fx0, fy0, fx0 + pw, fy1), fill=door_col)
            d.rectangle((fx1 - pw, fy0, fx1, fy1), fill=door_col)
        draw_sparkles(img, t, CX, 990, 420, n=8, seed=15, start=0.9)
        draw_text(img, name, CX, 1560, size=64, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=9, alpha=fade(t, 1.0), max_w=W - 160)

    def draw_dday(t, img):
        draw_confetti(img, t, seed=16, n=30)
        draw_text(img, dday, CX, 620, size=int(260 * min(1, 0.3 + pop(t, 0, 0.5))), font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=16)
        draw_text(img, name, CX, 860, size=76, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, alpha=fade(t, 0.3), max_w=W - 160)
        paste_sticker(img, st, CX, 1200 + bob(t, 8), size=460, scale=pop(t, 0.4, 0.4))
        if logo is not None:
            paste_sticker(img, logo, CX, 1540, size=420, height=150, alpha=fade(t, 0.7))
        else:
            draw_pill(img, sub, CX, 1540, size=44, fill=WHITE, text_fill=INK, scale=pop(t, 0.7), shadow=ctx.shape_color)
    return [Scene(2.4, draw_knock), Scene(2.6, draw_open), Scene(2.8, draw_dday)]


register(FormatSpec(
    "F15", "출시 카운트다운 티저", "서비스 연결형",
    'D-1 티저 애니(문 열고 등장) → 콜라보 로고 → 캡션 "D-1"',
    [Field("dday", "D-day 문구", "text", "D-1"),
     Field("name", "작품/콜라보명", "text", lambda pk: pk.name),
     Field("sub", "보조 문구", "text", "COMING SOON"),
     Field("sticker", "등장 스티커 번호", "sticker", lambda pk: 1),
     Field("logo", "콜라보 로고 이미지 (선택)", "image", None)],
    build_f15))


# ── F17 트렌드 오디오 챌린지 ─────────────────────────────────
def build_f17(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "지큐 챌린지")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 6))
    bpm = float(P(p, "bpm", 120))
    dur = float(P(p, "duration", 8))
    beats_per_cut = int(P(p, "beats_per_cut", 2))
    sts = [ctx.st(i) for i in idxs]
    beat = 60.0 / bpm

    def draw(t, img):
        b = t / beat; bi = int(b); bf = b - bi
        k = (bi // beats_per_cut) % len(sts)
        sfx_change("hit", bi, 0.35)
        d = ImageDraw.Draw(img)
        # 비트 링
        r = 360 + 220 * bf
        d.ellipse((CX - r, 1000 - r, CX + r, 1000 + r), outline=ctx.shape_color, width=int(30 * (1 - bf)) + 1)
        draw_pill(img, title, CX, SAFE_TOP + 90, size=52, fill=ctx.accent, text_fill=WHITE, scale=1 + 0.06 * (1 - bf))
        bounce = 1.18 - 0.18 * ease_out_cubic(bf * 1.6)
        side = -1 if bi % 2 == 0 else 1
        paste_sticker(img, sts[k], CX + side * 60 * (1 - bf), 1000 - 60 * (1 - bf) ** 2 * 3, size=760, scale=bounce,
                      rot=side * 8 * (1 - bf), flip=(side > 0))
        for j in range(4):
            xx = 200 + j * 226; hh = 40 + 120 * abs(math.sin(t * 7 + j * 1.3))
            d.rounded_rectangle((xx - 40, 1600 - hh, xx + 40, 1600), radius=20, fill=ctx.accent if j % 2 else ctx.shape_color)
    return [Scene(dur, draw)]


register(FormatSpec(
    "F17", "트렌드 오디오 챌린지", "귀여움/중독형",
    "비트에 맞춰 캐릭터가 통통 튀며 교체 (BGM 업로드하거나 인스타에서 트렌드 오디오 추가)",
    [Field("title", "타이틀", "text", "지큐 챌린지"),
     Field("stickers", "안무 컷 스티커들", "stickers", lambda pk: spread(len(pk), 6), n=6),
     Field("bpm", "BPM (오디오 템포)", "float", 120, min=60, max=200),
     Field("beats_per_cut", "몇 비트마다 스티커 교체", "int", 2, min=1, max=8),
     Field("duration", "길이(초)", "float", 8, min=4, max=20)],
    build_f17, tip="트렌드 오디오는 저작권상 인스타 앱에서 추가 → BPM만 맞춰 두면 비트가 맞음"))


# ── F22 캐릭터 일상 (모션) ────────────────────────────────────
def build_f22(ctx: Ctx, p: dict) -> List[Scene]:
    header = P(p, "header", "지큐의 하루")
    raw = as_lines(P(p, "cuts"), ["기상|21|jump", "출근길|16|walk", "일할 때|13|shake", "퇴근!|19|spin", "잘 자|24|zoom"])
    per = float(P(p, "per", 1.6))
    cta = P(p, "cta", "너의 하루도 스티커로!")
    cuts = []
    for k, l in enumerate(raw):
        parts = [x.strip() for x in l.split("|")]
        label = parts[0]
        try:
            idx = int(parts[1])
        except Exception:
            idx = spread(len(ctx.pack), len(raw))[k]
        motion = parts[2] if len(parts) > 2 else ["jump", "walk", "shake", "spin", "zoom"][k % 5]
        cuts.append((label, idx, motion))
    scenes = [title_scene(ctx, header, 1.4, sticker="main")]
    for k, (label, idx, motion) in enumerate(cuts):
        st = ctx.st(idx)

        def draw(t, img, st=st, label=label, motion=motion):
            draw_pill(img, header, CX, SAFE_TOP + 80, size=44, fill=ctx.accent, text_fill=WHITE)
            x, y, sc, rot, flip = CX, 980, 1.0, 0.0, False
            if motion == "walk":
                sfx_loop("run", 0, per, 0.5)
                x = lerp(-300, W + 300, t / per); y = 980 + abs(math.sin(t * 9)) * -30; rot = wobble(t, 6, 0.5)
            elif motion == "jump":
                sfx_change("boing", int(t / 0.7), 0.7)
                ph = (t / 0.7) % 1.0; y = 980 - 260 * math.sin(math.pi * ph); sc = 1 + 0.08 * math.sin(math.pi * ph)
            elif motion == "shake":
                sfx_once("rattle", t, 0.0, 0.6)
                x = CX + 26 * math.sin(t * 28); rot = 4 * math.sin(t * 28)
            elif motion == "spin":
                sfx_once("whirr", t, 0.0, 0.6)
                rot = 360 * (t / per); sc = pop(t, 0, 0.5)
            elif motion == "zoom":
                sfx_once("charge", t, 0.0, 0.5)
                sc = 0.5 + 0.6 * ease_out_cubic(t / per)
            elif motion == "slide":
                sfx_once("whoosh", t, 0.0, 0.7)
                y = lerp(H + 400, 980, ease_out_back(t / 0.6))
            else:
                sc = pop(t, 0, 0.4); y = 980 + bob(t, 10)
            paste_sticker(img, st, x, y, size=740, scale=sc, rot=rot, flip=flip)
            draw_text(img, label, CX, 1470, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10, alpha=fade(t, 0.1))
        scenes.append(Scene(per, draw))
    scenes.append(cta_scene(ctx, cta, cuts[-1][1], 2.0))
    return scenes


register(FormatSpec(
    "F22", "캐릭터 일상 (모션)", "공감형",
    "캐릭터가 주인공으로 상황별 모션 (걷기/점프/흔들기/회전/줌/슬라이드)",
    [Field("header", "헤더", "text", "지큐의 하루"),
     Field("cuts", "컷 목록 (한 줄에 '라벨|번호|모션')  모션: walk jump shake spin zoom slide pop", "textarea",
           lambda pk: "\n".join(f"{l}|{i}|{m}" for l, i, m in zip(["기상", "출근길", "일할 때", "퇴근!", "잘 자"], spread(len(pk), 5), ["jump", "walk", "shake", "spin", "zoom"]))),
     Field("per", "컷당 길이(초)", "float", 1.6, min=0.8, max=3),
     Field("cta", "마무리 CTA", "text", "너의 하루도 스티커로!")],
    build_f22))


# ── F26 틀린 하나 찾기 ────────────────────────────────────────
def _variant(st: Image.Image, mode: str) -> Image.Image:
    if mode == "flip":
        return st.transpose(Image.FLIP_LEFT_RIGHT)
    if mode == "tint":
        r, g, b, a = st.split()
        return Image.merge("RGBA", (r.point(lambda v: min(255, int(v * 0.85))), g, b.point(lambda v: min(255, int(v * 1.15))), a))
    if mode == "rotate":
        return st.rotate(12, resample=Image.BICUBIC, expand=True)
    if mode == "gray":
        g = st.convert("L").convert("RGBA"); g.putalpha(st.split()[3]); return g
    return st.transpose(Image.FLIP_LEFT_RIGHT)


def build_f26(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "다른 하나를 찾아라!")
    idx = int(P(p, "sticker", 1))
    mode = P(p, "difference", "flip")
    timer = float(P(p, "timer", 5))
    cols, rows = 4, 5
    cta = P(p, "cta", "몇 초 만에 찾았어? 댓글로!")
    rng = random.Random(rseed(p))
    odd = rng.randrange(cols * rows)
    ctx.meta["결과"] = f"다른 하나: {odd // cols + 1}행 {odd % cols + 1}열"
    st = ctx.st(idx); var = _variant(st, mode)
    gx0, gx1, gy0, gy1 = 70, W - 70, 480, 1560
    cw, ch = (gx1 - gx0) / cols, (gy1 - gy0) / rows

    def grid(img, t, reveal=False):
        d = ImageDraw.Draw(img)
        for k in range(cols * rows):
            r, c = divmod(k, cols)
            x = gx0 + c * cw + cw / 2; y = gy0 + r * ch + ch / 2
            s = pop(t, 0.02 * k, 0.3) if not reveal else 1.0
            d.rounded_rectangle((x - cw / 2 + 8, y - ch / 2 + 8, x + cw / 2 - 8, y + ch / 2 - 8), radius=26, fill=WHITE)
            paste_sticker(img, var if k == odd else st, x, y, size=int((cw - 40) * s), height=int((ch - 40) * s))
        if reveal:
            r, c = divmod(odd, cols)
            x = gx0 + c * cw + cw / 2; y = gy0 + r * ch + ch / 2
            pr = 1 + 0.05 * math.sin(t * 8)
            d.rounded_rectangle((x - cw / 2 * pr + 2, y - ch / 2 * pr + 2, x + cw / 2 * pr - 2, y + ch / 2 * pr - 2),
                                radius=30, outline=(255, 80, 90), width=14)

    def draw_q(t, img):
        draw_text(img, title, CX, SAFE_TOP + 90, size=78, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        grid(img, t)
        draw_ring_countdown(img, CX, 1635, 56, 1 - t / timer, ctx.accent, width=14, track=ctx.shape_color)
        draw_text(img, str(max(1, math.ceil(timer - t))), CX, 1635, size=52, fill=ctx.ink)
        sfx_change("beep", max(1, math.ceil(timer - t)), 0.7)

    def draw_a(t, img):
        sfx_once("ding", t, 0.0, 0.8)
        draw_text(img, "정답은 여기!", CX, SAFE_TOP + 90, size=78, font_name="title", fill=(255, 80, 90), stroke=WHITE, stroke_w=10)
        grid(img, t, reveal=True)
        draw_pill(img, cta, CX, 1640, size=40, fill=ctx.accent, text_fill=WHITE, scale=pop(t, 0.3))
    return [title_scene(ctx, title, 1.3, sub={"flip": "하나만 좌우가 반대!", "tint": "하나만 색이 달라!", "rotate": "하나만 기울어 있어!", "gray": "하나만 흑백!"}.get(mode, "")),
            Scene(timer, draw_q), Scene(2.6, draw_a)]


register(FormatSpec(
    "F26", "틀린 하나 찾기", "참여형",
    "같은 스티커 20개 중 하나만 다름(좌우반전/색/기울기/흑백) → 카운트다운 → 정답 표시",
    [Field("title", "타이틀", "text", "다른 하나를 찾아라!"),
     Field("sticker", "스티커 번호", "sticker", lambda pk: 1),
     Field("difference", "다른 점", "choice", "flip", options=["flip", "tint", "rotate", "gray"]),
     Field("timer", "제한 시간(초)", "float", 5, min=2, max=10),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "마무리 CTA", "text", "몇 초 만에 찾았어? 댓글로!")],
    build_f26))


# ── F27 숨은 캐릭터 찾기 ─────────────────────────────────────
def build_f27(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "숨은 지큐를 찾아라!")
    target = int(P(p, "target", 1))
    count = int(P(p, "count", 40))
    timer = float(P(p, "timer", 6))
    cta = P(p, "cta", "찾았으면 댓글에 '찾았다'!")
    rng = random.Random(rseed(p))
    others = [i for i in range(1, len(ctx.pack) + 1) if i != target] or [target]
    items = []
    for k in range(count):
        items.append((rng.uniform(120, W - 120), rng.uniform(560, 1560), rng.choice(others), rng.uniform(-25, 25), rng.uniform(0.8, 1.15)))
    tx, ty = rng.uniform(160, W - 160), rng.uniform(600, 1520)
    tpos = rng.randrange(len(items))
    items.insert(tpos, (tx, ty, target, rng.uniform(-15, 15), 0.9))
    ctx.meta["결과"] = f"타깃 위치: 가로 {int(tx / W * 100)}% · 세로 {int(ty / H * 100)}% 지점"
    tst = ctx.st(target)

    def field(img, t, reveal=False):
        for k, (x, y, i, rot, sc) in enumerate(items):
            s = pop(t, 0.012 * k, 0.3) if not reveal else 1.0
            paste_sticker(img, ctx.st(i), x, y, size=int(190 * sc * s), rot=rot)
        if reveal:
            d = ImageDraw.Draw(img)
            r = 130 + 8 * math.sin(t * 8)
            d.ellipse((tx - r, ty - r, tx + r, ty + r), outline=(255, 80, 90), width=14)

    def draw_q(t, img):
        draw_pill(img, title, CX, SAFE_TOP + 70, size=46, fill=ctx.accent, text_fill=WHITE)
        draw_card(img, (60, 340, 400, 500), radius=30, fill=WHITE, shadow=None)
        d = ImageDraw.Draw(img); d.text((100, 420), "찾을 스티커 →", font=font("bold", 30), fill=INK, anchor="lm")
        paste_sticker(img, tst, 330, 420, size=120, height=130)
        field(img, t)
        draw_ring_countdown(img, W - 130, 420, 60, 1 - t / timer, ctx.accent, width=14, track=ctx.shape_color)
        draw_text(img, str(max(1, math.ceil(timer - t))), W - 130, 420, size=54, fill=ctx.ink)
        sfx_change("beep", max(1, math.ceil(timer - t)), 0.7)

    def draw_a(t, img):
        sfx_once("ding", t, 0.0, 0.8)
        draw_pill(img, "여기 있었지롱!", CX, SAFE_TOP + 70, size=46, fill=(255, 80, 90), text_fill=WHITE)
        field(img, t, reveal=True)
        draw_pill(img, cta, CX, 1650, size=40, fill=WHITE, text_fill=INK, scale=pop(t, 0.3), shadow=ctx.shape_color)
    return [title_scene(ctx, title, 1.3, sub="제한 시간 안에 찾기!", sticker=target),
            Scene(timer, draw_q), Scene(2.6, draw_a)]


register(FormatSpec(
    "F27", "숨은 캐릭터 찾기", "참여형",
    "수십 개 스티커 사이에 숨은 타깃 스티커 찾기 → 카운트다운 → 위치 공개",
    [Field("title", "타이틀", "text", "숨은 지큐를 찾아라!"),
     Field("target", "찾을 스티커 번호", "sticker", lambda pk: 1),
     Field("count", "방해 스티커 개수", "int", 40, min=10, max=80),
     Field("timer", "제한 시간(초)", "float", 6, min=2, max=12),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "마무리 CTA", "text", "찾았으면 댓글에 '찾았다'!")],
    build_f27))


# ── F28 슬롯머신 뽑기 ────────────────────────────────────────
def build_f28(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "오늘의 지큐 뽑기")
    idxs = as_int_list(P(p, "stickers"), spread(len(ctx.pack), 6))
    results = as_lines(P(p, "results"), ["행운 가득한 하루!", "간식 먹고 힘내기", "오늘은 푹 쉬는 날", "칭찬 받을 예정", "깜짝 선물 예감", "무한 귀여움 충전"])
    cta = P(p, "cta", "너는 뭐 나왔어? 댓글로!")
    rng = random.Random(rseed(p))
    sts = [ctx.st(i) for i in idxs]
    n = len(sts)
    jackpot = rng.random() < float(P(p, "jackpot_rate", 0.3))
    final = [rng.randrange(n)] * 3 if jackpot else [rng.randrange(n) for _ in range(3)]
    if not jackpot and final[0] == final[1] == final[2]:
        final[2] = (final[2] + 1) % n
    stops = [2.2, 3.2, 4.2]
    cell = 300; y0 = 760
    xs = [CX - cell - 20, CX, CX + cell + 20]
    result_txt = results[final[0] % len(results)]
    ctx.meta["결과"] = ("잭팟! " if jackpot else "") + f"스티커 {idxs[final[0]]} · {result_txt}"

    def static(img):
        draw_card(img, (60, y0 - cell / 2 - 60, W - 60, y0 + cell / 2 + 60), radius=50, fill=(80, 60, 110), shadow=None)
        d = ImageDraw.Draw(img)
        for x in xs:
            d.rounded_rectangle((x - cell / 2, y0 - cell / 2, x + cell / 2, y0 + cell / 2), radius=28, fill=WHITE)
        d.rounded_rectangle((W - 120, y0 - 40, W - 70, y0 + 40), radius=25, fill=(255, 90, 90))

    def reel_pos(k, t):
        """릴 k의 현재 오프셋(스티커 인덱스 실수)."""
        stop = stops[k]
        if t >= stop:
            return float(final[k])
        # 감속: 남은 시간이 짧을수록 느려짐
        speed = 14.0 * clamp((stop - t) / stop) + 1.5
        return (final[k] - speed * (stop - t) * 0.6) % n

    def draw_spin(t, img):
        sfx_loop("spin", 0, stops[2], 0.5)
        for k_ in range(3):
            sfx_once("thud", t, stops[k_], 0.9)
        sfx_once("fanfare" if jackpot else "ding", t, stops[2] + 0.2, 0.9)
        draw_text(img, title, CX, SAFE_TOP + 100, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        for k, x in enumerate(xs):
            pos = reel_pos(k, t)
            layer = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
            base = int(math.floor(pos)); frac = pos - base
            for j in (-1, 0, 1):
                idx = (base + j) % n
                cy = cell / 2 + (j - frac) * cell
                paste_sticker(layer, sts[idx], cell / 2, cy, size=cell - 40, height=cell - 40)
            img.paste(layer, (int(x - cell / 2), int(y0 - cell / 2)), layer)
            if t >= stops[k]:
                ImageDraw.Draw(img).rounded_rectangle((x - cell / 2, y0 - cell / 2, x + cell / 2, y0 + cell / 2), radius=28,
                                                      outline=ctx.accent, width=10)
        if t < stops[0]:
            draw_pill(img, "돌아가는 중…", CX, 1240, size=44, fill=WHITE, text_fill=INK)
        elif t > stops[2] + 0.2:
            if jackpot:
                draw_confetti(img, t, seed=rseed({}), start=stops[2] + 0.2)
                draw_text(img, "JACKPOT!!", CX, 1240, size=110, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=12)
            else:
                draw_text(img, "결과 확인!", CX, 1240, size=90, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)

    def draw_result(t, img):
        if jackpot:
            draw_confetti(img, t, seed=rseed({}))
        draw_text(img, "오늘의 결과", CX, 400, size=64, fill=ctx.ink, stroke=WHITE, stroke_w=9)
        paste_sticker(img, sts[final[0]], CX, 900 + bob(t, 10), size=740, scale=pop(t, 0, 0.45), rot=wobble(t, 3))
        draw_sparkles(img, t, CX, 900, 400, n=8, seed=28)
        draw_text(img, result_txt, CX, 1380, size=80, font_name="title", fill=ctx.accent, stroke=WHITE, stroke_w=10, alpha=fade(t, 0.3), max_w=W - 160)
        draw_pill(img, cta, CX, 1560, size=42, fill=WHITE, text_fill=INK, scale=pop(t, 0.6), shadow=ctx.shape_color)
    return [Scene(6.0, draw_spin, static), Scene(3.0, draw_result)]


register(FormatSpec(
    "F28", "슬롯머신 뽑기", "참여형",
    "슬롯 3칸이 차례로 멈춤 → (잭팟) → 결과 스티커 + 오늘의 문구",
    [Field("title", "타이틀", "text", "오늘의 지큐 뽑기"),
     Field("stickers", "릴에 들어갈 스티커들", "stickers", lambda pk: spread(len(pk), 6), n=6),
     Field("results", "결과 문구 (한 줄에 하나, 스티커 순서대로)", "textarea", "행운 가득한 하루!\n간식 먹고 힘내기\n오늘은 푹 쉬는 날\n칭찬 받을 예정\n깜짝 선물 예감\n무한 귀여움 충전"),
     Field("jackpot_rate", "잭팟 확률(0~1)", "float", 0.3, min=0, max=1),
     Field("seed", "랜덤 시드 (0 = 매번 랜덤)", "int", 0, min=0),
     Field("cta", "마무리 CTA", "text", "너는 뭐 나왔어? 댓글로!")],
    build_f28))


# ── F29 티어표 ───────────────────────────────────────────────
_TIER_COLORS = {"S": (255, 127, 127), "A": (255, 191, 127), "B": (255, 223, 127), "C": (191, 255, 127), "D": (127, 191, 255), "F": (200, 200, 210)}


def build_f29(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "지큐 표정 티어표")
    raw = as_lines(P(p, "items"), [f"S|{i}" for i in spread(len(ctx.pack), 2, 0)] + [f"A|{i}" for i in spread(len(ctx.pack), 3, 1)]
                   + [f"B|{i}" for i in spread(len(ctx.pack), 2, 2)] + [f"C|{i}" for i in spread(len(ctx.pack), 2, 3)])
    per = float(P(p, "per", 0.55))
    cta = P(p, "cta", "S티어 동의? 반박은 댓글로!")
    tiers = as_lines(P(p, "tiers"), ["S", "A", "B", "C"])
    items = []
    for l in raw:
        a, _, b = l.partition("|")
        try:
            items.append((a.strip().upper(), int(b)))
        except Exception:
            pass
    rows = {t: [i for tt, i in items if tt == t] for t in tiers}
    ty0, ty1 = 500, 1600
    rh = (ty1 - ty0) / len(tiers)
    lab_w = 150
    order = [(t, i) for t in tiers for i in rows[t]]
    n_items = len(order)
    intro = 0.6
    total = intro + per * n_items + 2.0

    def static(img):
        d = ImageDraw.Draw(img)
        for k, t in enumerate(tiers):
            y = ty0 + k * rh
            d.rounded_rectangle((60, y + 6, W - 60, y + rh - 6), radius=24, fill=WHITE)
            d.rounded_rectangle((60, y + 6, 60 + lab_w, y + rh - 6), radius=24, fill=_TIER_COLORS.get(t, (200, 200, 210)))
            d.text((60 + lab_w / 2, y + rh / 2), t, font=font("title", 70), fill=INK, anchor="mm")

    def draw(t, img):
        draw_text(img, title, CX, SAFE_TOP + 100, size=80, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=10)
        placed = {tt: 0 for tt in tiers}
        size = min(rh - 26, (W - 120 - lab_w) / 5 - 10)
        for k, (tt, i) in enumerate(order):
            start = intro + k * per
            row_i = tiers.index(tt); slot = placed[tt]; placed[tt] += 1
            tx = 60 + lab_w + 20 + slot * (size + 10) + size / 2
            ty = ty0 + row_i * rh + rh / 2
            if t < start:
                continue
            sfx_once("whoosh", t, start, 0.6); sfx_once("thud", t, start + 0.4, 0.5)
            f = ease_out_cubic((t - start) / 0.4)
            x = lerp(CX, tx, f); y = lerp(1900, ty, f); sc = lerp(1.8, 1.0, f)
            paste_sticker(img, ctx.st(i), x, y, size=int(size * sc), height=int(size * sc), rot=wobble(t, 4, 0.5) * (1 - f))
        if t > intro + per * n_items + 0.3:
            draw_pill(img, cta, CX, 1660, size=42, fill=ctx.accent, text_fill=WHITE, scale=pop(t, intro + per * n_items + 0.3))
        elif t > intro:
            k = min(n_items - 1, int((t - intro) / per))
            draw_pill(img, f"{order[k][0]} 티어!", CX, 1660, size=42, fill=WHITE, text_fill=INK, shadow=ctx.shape_color)
    return [Scene(total, draw, static)]


register(FormatSpec(
    "F29", "티어표", "공감형",
    "S/A/B/C 티어표에 스티커가 하나씩 날아가 배치 → 반박 유도",
    [Field("title", "타이틀", "text", "지큐 표정 티어표"),
     Field("tiers", "티어 목록 (한 줄에 하나, 위에서부터)", "textarea", "S\nA\nB\nC"),
     Field("items", "배치 목록 (한 줄에 '티어|스티커번호', 순서대로 등장)", "textarea",
           lambda pk: "\n".join([f"S|{i}" for i in spread(len(pk), 2, 0)] + [f"A|{i}" for i in spread(len(pk), 3, 1)]
                                + [f"B|{i}" for i in spread(len(pk), 2, 2)] + [f"C|{i}" for i in spread(len(pk), 2, 3)])),
     Field("per", "배치 간격(초)", "float", 0.55, min=0.3, max=1.5),
     Field("cta", "마무리 CTA", "text", "S티어 동의? 반박은 댓글로!")],
    build_f29))


# ── F30 알림 폭탄 ────────────────────────────────────────────
def build_f30(ctx: Ctx, p: dict) -> List[Scene]:
    title = P(p, "title", "POV: 단톡방 알림 폭탄")
    app = P(p, "app", "톡")
    raw = as_lines(P(p, "messages"), ["지큐|1|야 뭐해", "지큐|3|나와", "지큐|6|빨리!!", "지큐|12|구독 댓글 알림!", "지큐|17|놀자놀자", "지큐|22|최고다", "지큐|9|사랑해", "지큐|24|잘자"])
    interval = float(P(p, "interval", 0.7))
    hold = float(P(p, "hold", 2.0))
    cta = P(p, "cta", "이런 친구 있으면 태그")
    items = []
    for k, l in enumerate(raw):
        parts = [x.strip() for x in l.split("|")]
        name = parts[0] if parts else "친구"
        try:
            idx = int(parts[1])
        except Exception:
            idx = (k % len(ctx.pack)) + 1
        msg = parts[2] if len(parts) > 2 else ""
        items.append((name, idx, msg))
    nh, gap = 150, 16
    top = 520; max_show = 6
    total = interval * len(items) + hold

    def static(img):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((60, 320, W - 60, 1720), radius=70, fill=(30, 30, 40))
        d.rounded_rectangle((70, 330, W - 70, 1710), radius=64, fill=(60, 64, 90))
        d.text((CX, 420), "9:41", font=font("bold", 72), fill=WHITE, anchor="mm")

    def draw(t, img):
        k = min(len(items), int(t / interval) + 1)
        sfx_change("notify", k, 0.9)
        show = items[max(0, k - max_show):k]
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
        # 최신이 위로
        for j, (name, idx, msg) in enumerate(reversed(show)):
            gi = k - 1 - j
            s = pop(t, gi * interval, 0.3) if j == 0 else 1.0
            y = top + j * (nh + gap) - (1 - s) * 60
            a = int(255 * min(1, s + 0.2))
            box = (100, y, W - 100, y + nh)
            d.rounded_rectangle(box, radius=34, fill=(245, 246, 250, a))
            d.text((box[0] + 150, y + 42), f"{app} · {name}", font=font("bold", 30), fill=(60, 60, 70, a), anchor="lm")
            d.text((box[0] + 150, y + 98), msg, font=font("semi", 36), fill=(30, 30, 40, a), anchor="lm")
            d.text((box[2] - 40, y + 42), "지금", font=font("reg", 26), fill=(150, 150, 160, a), anchor="rm")
            d.rounded_rectangle((box[0] + 24, y + 22, box[0] + 130, y + nh - 22), radius=26, fill=(255, 255, 255, a))
        img.alpha_composite(layer)
        for j, (name, idx, msg) in enumerate(reversed(show)):
            gi = k - 1 - j
            s = pop(t, gi * interval, 0.3) if j == 0 else 1.0
            y = top + j * (nh + gap) - (1 - s) * 60
            paste_sticker(img, ctx.st(idx), 100 + 77, y + nh / 2, size=96, height=96)
        draw_badge(img, k, W - 150, 400, r=44, fill=(255, 70, 70), size=40, scale=1 + 0.15 * (1 - clamp((t - (k - 1) * interval) / 0.3)))
        draw_text(img, title, CX, SAFE_TOP - 20, size=64, font_name="title", fill=ctx.ink, stroke=WHITE, stroke_w=9, max_w=W - 120)
        if t > interval * len(items) + 0.3:
            draw_pill(img, cta, CX, 1620, size=42, fill=ctx.accent, text_fill=WHITE, scale=pop(t, interval * len(items) + 0.3))
    return [Scene(total, draw, static)]


register(FormatSpec(
    "F30", "알림 폭탄", "공감형",
    "잠금화면 알림이 연속으로 쌓임(스티커 아바타 + 메시지) → 친구 태그 유도",
    [Field("title", "타이틀", "text", "POV: 단톡방 알림 폭탄"),
     Field("app", "앱 이름", "text", "톡"),
     Field("messages", "알림 (한 줄에 '이름|스티커번호|메시지')", "textarea",
           "지큐|1|야 뭐해\n지큐|3|나와\n지큐|6|빨리!!\n지큐|12|구독 댓글 알림!\n지큐|17|놀자놀자\n지큐|22|최고다\n지큐|9|사랑해\n지큐|24|잘자"),
     Field("interval", "알림 간격(초)", "float", 0.7, min=0.3, max=2),
     Field("hold", "마지막 정지(초)", "float", 2.0, min=0.5, max=5),
     Field("cta", "마무리 CTA", "text", "이런 친구 있으면 태그")],
    build_f30))


# ──────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────
def build_timeline(pack: StickerPack, fmt_id: str, params: Optional[dict] = None,
                   theme="auto", bg_style="circles", brand="OGQ 마켓") -> Timeline:
    spec = FORMATS[fmt_id]
    params = dict(spec.defaults(pack), **{k: v for k, v in (params or {}).items() if v not in (None, "")})
    if fmt_id == "F18":
        bg_style = "space"
    ctx = Ctx(pack, theme=theme, bg_style=bg_style, brand=brand)
    return Timeline(ctx, spec.build(ctx, params))


def generate(pack_path: str, fmt_id: str, out_path: str, params: Optional[dict] = None,
             theme="auto", bg_style="circles", brand="OGQ 마켓", audio: Optional[str] = "cute",
             progress=None, volume: float = 1.0) -> str:
    pack = StickerPack.load(pack_path)
    tl = build_timeline(pack, fmt_id, params, theme, bg_style, brand)
    return render_video(tl, out_path, audio_path=resolve_audio(audio), progress=progress, volume=volume)


def main():
    ap = argparse.ArgumentParser(description="OGQ 릴스 자동 생성기")
    ap.add_argument("pack", nargs="?", help="스티커팩 zip 또는 폴더")
    ap.add_argument("--format", "-f", default=None, help="포맷 ID (예: F04)")
    ap.add_argument("--params", "-p", default=None, help="JSON 문자열 또는 .json 파일")
    ap.add_argument("--out", "-o", default=None)
    ap.add_argument("--theme", default="auto", choices=["auto"] + list(THEMES))
    ap.add_argument("--bg", default="circles", choices=["circles", "halftone", "checker", "plain", "space"])
    ap.add_argument("--brand", default="OGQ 마켓")
    ap.add_argument("--bgm", "--audio", dest="bgm", default="cute",
                    help="none(무음) | cute/upbeat/chill/funny(기본 음악) | 파일 경로(mp3/m4a/wav). 기본 cute")
    ap.add_argument("--volume", type=float, default=1.0, help="음악 볼륨 배율 (0.0~2.0)")
    ap.add_argument("--no-sfx", action="store_true", help="효과음 끄기")
    ap.add_argument("--sfx-volume", type=float, default=1.0, help="효과음 볼륨 배율")
    ap.add_argument("--list", action="store_true", help="포맷 목록")
    ap.add_argument("--demo", action="store_true", help="모든 포맷을 기본값으로 렌더")
    ap.add_argument("--preview", action="store_true", help="영상 대신 6프레임 미리보기 PNG")
    a = ap.parse_args()

    if a.list or not a.pack:
        for f in FORMATS.values():
            print(f"{f.id}  [{f.category}] {f.name}\n      {f.scene_desc}")
            for fld in f.fields:
                print(f"        - {fld.key} ({fld.type}): {fld.label}")
        return
    params = {}
    if a.params:
        params = json.load(open(a.params, encoding="utf-8")) if os.path.exists(a.params) else json.loads(a.params)
    pack = StickerPack.load(a.pack)
    ids = list(FORMATS) if a.demo else [a.format or "F12"]
    outdir = os.path.join(BASE_DIR, "output")
    for fid in ids:
        tl = build_timeline(pack, fid, params, a.theme, a.bg, a.brand)
        safe = re.sub(r"[\\/:*?\"<>|]", "·", f"{pack.name}_{fid}_{FORMATS[fid].name}")
        out = a.out if (a.out and not a.demo) else os.path.join(outdir, safe + ".mp4")
        if a.preview:
            frames = tl.preview(6)
            sheet = Image.new("RGB", (6 * 270, 480), "white")
            for i, fr in enumerate(frames):
                sheet.paste(fr.resize((270, 480)).convert("RGB"), (i * 270, 0))
            out = os.path.splitext(out)[0] + "_preview.png"
            sheet.save(out); print("saved", out); continue

        def prog(i, n, fid=fid):
            if i % 30 == 0 or i == n:
                print(f"\r{fid} {i}/{n}", end="", flush=True)
        render_video(tl, out, audio_path=resolve_audio(a.bgm), progress=prog, volume=a.volume,
                     sfx=not a.no_sfx, sfx_volume=a.sfx_volume)
        print(f"\r{fid} 완료 → {out} ({tl.total:.1f}s)")


if __name__ == "__main__":
    main()
