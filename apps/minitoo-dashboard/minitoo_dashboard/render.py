from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from . import i18n, paths
from .sources.calendar import Item
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
    event: Optional[Item]  # what the event row shows (event or reminder)
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


def _weather_row(d, m: DashboardModel) -> None:
    w, lang = m.weather, m.lang
    if w is None:
        d.text((4, 16), i18n.t(lang, "no_weather"), font=font(8), fill=DIM)
        return
    _icon(d, w.condition, 2, 4)
    d.text((40, 6), _temp(w.temp, m.temp_unit), font=font(16), fill=WHITE)
    d.text((112, 10), f"{w.tmax}/{w.tmin}", font=font(8), fill=DIM)
    if w.age_s:
        text, color = i18n.t(lang, "ago", d=i18n.duration(w.age_s, lang)), DIM
    elif w.precip_from and w.precip_kind:
        key = f"{w.precip_kind}_now" if w.precip_from == "now" else f"{w.precip_kind}_at"
        text, color = i18n.t(lang, key, t=w.precip_from), BLUE
    else:
        text, color = i18n.t(lang, "cond_" + w.condition), WHITE
    d.text((40, 28), _fit(text, font(8), 116), font=font(8), fill=color)


def _event_row(d, m: DashboardModel) -> None:
    e, lang = m.event, m.lang
    if e is None:
        d.text((4, 58), i18n.t(lang, "none_left"), font=font(8), fill=DIM)
        return
    box = WHITE
    if e.kind == "overdue":
        d.text((4, 49), i18n.t(lang, "overdue"), font=font(8), fill=YELLOW)
        title_y, counter_y, box = 64, 49, YELLOW
    elif e.kind == "today":
        d.text((4, 49), i18n.t(lang, "today"), font=font(8), fill=BLUE)
        title_y, counter_y = 64, 49
    elif e.start <= m.now:
        d.text((4, 47), i18n.t(lang, "now"), font=font(8), fill=BLUE)
        d.text((4, 57), i18n.t(lang, "until", t=_hhmm(e.end)), font=font(8), fill=BLUE)
        title_y, counter_y = 70, 47
    else:
        d.text((4, 47), _hhmm(e.start), font=font(16), fill=BLUE)
        left = i18n.t(lang, "in_short", d=i18n.duration(max(60, e.start - m.now), lang))
        d.text((88, 51), _fit(left, font(8), 46), font=font(8), fill=BLUE)
        title_y, counter_y = 68, 51
    if e.more:
        counter = f"+{e.more}"
        d.text((156 - int(font(8).getlength(counter)), counter_y), counter, font=font(8), fill=DIM)
    if e.kind == "event":
        d.text((4, title_y), _fit(clean(e.title), font(8), 152), font=font(8), fill=WHITE)
    else:  # reminders get a checkbox, like in the Reminders app
        d.rectangle([4, title_y, 11, title_y + 7], outline=box)
        d.text((16, title_y), _fit(clean(e.title), font(8), 140), font=font(8), fill=WHITE)


def _pct(window: Window) -> str:
    return f"{round(window.pct)}%" if window.pct is not None else "--"


def _claude_row(d, m: DashboardModel) -> None:
    c, lang = m.claude, m.lang
    if not c.has_data:
        d.text((4, 100), i18n.t(lang, "no_data"), font=font(8), fill=DIM)
        return
    for y, label, window in ((89, i18n.t(lang, "h5"), c.five), (101, i18n.t(lang, "wk"), c.week)):
        d.text((4, y), label, font=font(8), fill=DIM)
        d.rectangle([30, y, 114, y + 6], fill=TRACK)
        if window.pct is not None:
            d.rectangle([30, y, 30 + round(84 * min(window.pct, 100) / 100), y + 6], fill=ORANGE)
        d.text((120, y), _pct(window), font=font(8), fill=WHITE)
    if c.as_of is not None:
        footer = i18n.t(lang, "as_of", t=_hhmm(c.as_of))
    elif c.five.is_reset:
        footer = i18n.t(lang, "reset")
    elif c.five.reset_in is not None:
        footer = i18n.t(lang, "reset_in", d=i18n.duration(c.five.reset_in, lang))
    else:
        footer = ""
    d.text((4, 115), _fit(footer, font(8), 140), font=font(8), fill=DIM)


def render_screen(m: DashboardModel) -> Image.Image:
    img, d = _canvas()
    _weather_row(d, m)
    d.line([4, 41, 155, 41], fill=TRACK)
    _event_row(d, m)
    d.line([4, 83, 155, 83], fill=TRACK)
    _claude_row(d, m)
    d.rectangle([148, 116, 155, 123], fill=ORANGE if m.status == "working" else GREY)
    return img


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
        event=Item("event", "Созвон с командой" if lang == "ru" else "Team sync", now + 25 * 60, now + 85 * 60),
        claude=ClaudeView(Window(23.0, 7800, False), Window(41.0, 3 * 86400, False), None, True),
    )
