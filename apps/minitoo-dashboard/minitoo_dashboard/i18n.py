from __future__ import annotations

STRINGS = {
    "en": {
        "next": "NEXT", "now": "NOW", "until": "until {t}", "in_min": "in {m} min", "in_h": "in {h}h {m:02d}m",
        "five_hour": "5-hour", "week": "week", "reset": "reset", "as_of": "as of {t}", "no_data": "no data yet",
        "ago": "{d} ago", "rain_from": "Rain from {t}", "snow_from": "Snow from {t}",
        "rain_now": "Rain now", "snow_now": "Snow now", "alert": "Claude needs you",
        "cond_clear": "Clear", "cond_partly": "Partly cloudy", "cond_cloudy": "Cloudy", "cond_fog": "Fog",
        "cond_rain": "Rain", "cond_snow": "Snow", "cond_storm": "Storm",
    },
    "ru": {
        "next": "ДАЛЕЕ", "now": "СЕЙЧАС", "until": "до {t}", "in_min": "через {m} мин", "in_h": "через {h}ч {m:02d}м",
        "five_hour": "5 часов", "week": "неделя", "reset": "сброшен", "as_of": "на {t}", "no_data": "нет данных",
        "ago": "{d} назад", "rain_from": "Дождь с {t}", "snow_from": "Снег с {t}",
        "rain_now": "Идёт дождь", "snow_now": "Идёт снег", "alert": "Claude ждёт вас",
        "cond_clear": "Ясно", "cond_partly": "Переменная обл.", "cond_cloudy": "Облачно", "cond_fog": "Туман",
        "cond_rain": "Дождь", "cond_snow": "Снег", "cond_storm": "Гроза",
    },
}
UNITS = {"en": ("d", "h", "m"), "ru": ("д", "ч", "м")}


def t(lang: str, key: str, **kw) -> str:
    return STRINGS.get(lang, STRINGS["en"])[key].format(**kw)


def duration(seconds: float, lang: str) -> str:
    d, h, m = UNITS.get(lang, UNITS["en"])
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s // 86400}{d}{(s % 86400) // 3600}{h}"
    if s >= 3600:
        return f"{s // 3600}{h}{(s % 3600) // 60:02d}{m}"
    return f"{max(1, s // 60)}{m}"
