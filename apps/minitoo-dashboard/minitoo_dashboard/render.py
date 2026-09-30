from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from . import i18n, paths
from .sources.calendar import Event
from .sources.claude import ClaudeView, Window
from .sources.weather import WeatherView

W, H = 160, 128
BLACK = (0, 0, 0)
WHITE = (242, 242, 242)
DIM = (130, 130, 130)
BLUE = (111, 179, 255)
ORANGE = (217, 119, 87)
GREY = (107, 107, 107)
YELLOW = (255, 210, 74)
RED = (230, 57, 70)
TRACK = (51, 51, 51)
CLOUD = (200, 210, 222)
FONT_PATH = paths.APP_DIR / "fonts" / "PressStart2P-Regular.ttf"
MISSING = "\U000F0000"  # private-use plane: renders as the font's .notdef glyph

_fonts: Dict[int, ImageFont.FreeTypeFont] = {}
_glyphs: Dict[str, bool] = {}


@dataclass
class DashboardModel:
    now: float
    status: str
    lang: str
    temp_unit: str
    city_name: str
    weather: Optional[WeatherView]
    event: Optional[Event]
    claude: ClaudeView


def font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
    return _fonts[size]


def _glyph_bitmap(ch: str) -> bytes:
    im = Image.new("1", (24, 24), 0)
    ImageDraw.Draw(im).text((0, 0), ch, font=font(8), fill=1)
    return im.tobytes()


def has_glyph(ch: str) -> bool:
    if ch not in _glyphs:
        _glyphs[ch] = ch == " " or _glyph_bitmap(ch) != _glyph_bitmap(MISSING)
    return _glyphs[ch]


def clean(text: str) -> str:
    return " ".join("".join(ch for ch in text if has_glyph(ch)).split())


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if f.getlength(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        while f.getlength(current) > max_width:
            cut = len(current)
            while cut > 1 and f.getlength(current[:cut]) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and f.getlength(last.rstrip() + "...") > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


def _fit(text: str, f: ImageFont.FreeTypeFont, max_width: int) -> str:
    return (wrap(text, f, max_width, 1) or [""])[0]


def _canvas():
    img = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # no anti-aliasing: crisp pixels, stable JPEGs
    return img, draw


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _temp(value: int, unit: str) -> str:
    return f"{value:+d}°" if unit == "celsius" else f"{value}°"


def _cloud(d, x, y):
    d.ellipse([x + 2, y + 8, x + 16, y + 22], fill=CLOUD)
    d.ellipse([x + 10, y + 2, x + 26, y + 18], fill=CLOUD)
    d.ellipse([x + 18, y + 8, x + 32, y + 22], fill=CLOUD)
    d.rectangle([x + 9, y + 14, x + 25, y + 22], fill=CLOUD)


def _icon(d, condition: str, x: int, y: int) -> None:
    if condition == "clear":
        d.ellipse([x + 6, y, x + 26, y + 20], fill=YELLOW)
        return
    if condition == "fog":
        for i in range(4):
            d.rectangle([x + 2, y + 4 + i * 5, x + 30, y + 5 + i * 5], fill=CLOUD)
        return
    if condition == "partly":
        d.ellipse([x + 16, y - 2, x + 32, y + 14], fill=YELLOW)
    _cloud(d, x, y)
    if condition == "rain":
        for i in range(3):
            d.line([x + 9 + i * 8, y + 24, x + 6 + i * 8, y + 30], fill=BLUE, width=2)
    elif condition == "snow":
        for i in range(3):
            d.rectangle([x + 7 + i * 8, y + 25, x + 9 + i * 8, y + 27], fill=WHITE)
    elif condition == "storm":
        d.line([x + 17, y + 22, x + 13, y + 28, x + 19, y + 28, x + 15, y + 34], fill=YELLOW, width=2)


def _weather_page(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    w, lang = m.weather, m.lang
    _icon(d, w.condition, 6, 12)
    d.text((46, 10), _temp(w.temp, m.temp_unit), font=font(24), fill=WHITE)
    d.text((46, 40), _fit(clean(m.city_name.split(",")[0]), font(8), 110), font=font(8), fill=DIM)
    d.text((6, 58), _fit(i18n.t(lang, "cond_" + w.condition), font(8), 148), font=font(8), fill=WHITE)
    d.text((6, 72), f"{_temp(w.tmax, m.temp_unit)} / {_temp(w.tmin, m.temp_unit)}", font=font(8), fill=WHITE)
    if w.precip_from and w.precip_kind:
        key = f"{w.precip_kind}_now" if w.precip_from == "now" else f"{w.precip_kind}_from"
        d.text((6, 88), _fit(i18n.t(lang, key, t=w.precip_from), font(8), 148), font=font(8), fill=BLUE)
    if w.age_s:
        d.text((6, 104), i18n.t(lang, "ago", d=i18n.duration(w.age_s, lang)), font=font(8), fill=DIM)
    return img


def _calendar_page(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    e, lang = m.event, m.lang
    d.text((6, 6), i18n.t(lang, "next"), font=font(8), fill=DIM)
    if e.start <= m.now:
        d.text((6, 20), i18n.t(lang, "now"), font=font(24), fill=BLUE)
        small = i18n.t(lang, "until", t=_hhmm(e.end))
    else:
        d.text((6, 20), _hhmm(e.start), font=font(24), fill=BLUE)
        minutes = max(1, math.ceil((e.start - m.now) / 60))
        small = (i18n.t(lang, "in_min", m=minutes) if minutes < 60
                 else i18n.t(lang, "in_h", h=minutes // 60, m=minutes % 60))
    d.text((6, 50), _fit(small, font(8), 148), font=font(8), fill=BLUE)
    for i, line in enumerate(wrap(clean(e.title), font(8), 148, 3)):
        d.text((6, 68 + i * 12), line, font=font(8), fill=WHITE)
    return img


def _pct(window: Window) -> str:
    return f"{round(window.pct)}%" if window.pct is not None else "--"


def _claude_page(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    c, lang = m.claude, m.lang
    d.text((6, 6), "CLAUDE", font=font(8), fill=DIM)
    if not c.has_data:
        d.text((6, 56), i18n.t(lang, "no_data"), font=font(8), fill=WHITE)
        return img
    d.text((6, 20), _pct(c.five), font=font(24), fill=ORANGE)
    d.text((86, 22), i18n.t(lang, "five_hour"), font=font(8), fill=DIM)
    if c.five.is_reset:
        d.text((86, 34), _fit(i18n.t(lang, "reset"), font(8), 70), font=font(8), fill=DIM)
    elif c.five.reset_in is not None:
        d.text((86, 34), i18n.duration(c.five.reset_in, lang), font=font(8), fill=DIM)
    d.rectangle([6, 50, 153, 55], fill=TRACK)
    if c.five.pct is not None:
        d.rectangle([6, 50, 6 + round(147 * min(c.five.pct, 100) / 100), 55], fill=ORANGE)
    week_text = i18n.t(lang, "reset") if c.week.is_reset else _pct(c.week)
    d.text((6, 64), f"{i18n.t(lang, 'week')} {week_text}", font=font(8), fill=WHITE)
    d.rectangle([6, 76, 153, 79], fill=TRACK)
    if c.week.pct is not None:
        d.rectangle([6, 76, 6 + round(147 * min(c.week.pct, 100) / 100), 79], fill=ORANGE)
    if c.as_of is not None:
        d.text((6, 92), i18n.t(lang, "as_of", t=_hhmm(c.as_of)), font=font(8), fill=DIM)
    return img


def _decorate(img: Image.Image, index: int, total: int, status: str) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([148, 116, 155, 123], fill=ORANGE if status == "working" else GREY)
    if total > 1:
        x0 = (W - (total * 10 - 4)) // 2
        for i in range(total):
            d.rectangle([x0 + i * 10, 122, x0 + i * 10 + 5, 124], fill=WHITE if i == index else TRACK)


def render_pages(m: DashboardModel) -> List[Image.Image]:
    pages = []
    if m.weather is not None:
        pages.append(_weather_page(m))
    if m.event is not None:
        pages.append(_calendar_page(m))
    pages.append(_claude_page(m))
    for i, page in enumerate(pages):
        _decorate(page, i, len(pages), m.status)
    return pages


def render_alert(lang: str) -> Image.Image:
    img, d = _canvas()
    d.rectangle([0, 0, W - 1, H - 1], outline=RED, width=4)
    d.text(((W - 24) // 2, 24), "!", font=font(24), fill=RED)
    for i, line in enumerate(wrap(i18n.t(lang, "alert"), font(8), 140, 2)):
        x = (W - int(font(8).getlength(line))) // 2
        d.text((x, 76 + i * 12), line, font=font(8), fill=WHITE)
    return img


def demo_model(now: float, lang: str, status: str = "working") -> DashboardModel:
    return DashboardModel(
        now=now, status=status, lang=lang, temp_unit="celsius",
        city_name="Москва" if lang == "ru" else "Moscow",
        weather=WeatherView(12, 15, 8, "cloudy", "18:00", "rain", None),
        event=Event("Созвон с командой" if lang == "ru" else "Team sync", now + 25 * 60, now + 85 * 60, "Work"),
        claude=ClaudeView(Window(23.0, 7800, False), Window(41.0, 3 * 86400, False), None, True),
    )
