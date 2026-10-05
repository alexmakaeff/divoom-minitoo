from __future__ import annotations

STRINGS = {
    "en": {
        "now": "NOW", "until": "until {t}", "in_short": "{d}", "none_left": "No events left", "overdue": "OVERDUE", "today": "TODAY",
        "h5": "5h", "wk": "wk", "wk_short": "wk", "as_of_short": "@{t}", "reset": "reset", "reset_in": "reset {d}", "as_of": "as of {t}",
        "no_data": "Claude: no data yet", "no_weather": "No weather data", "ago": "{d} ago",
        "rain_at": "Rain {t}", "snow_at": "Snow {t}", "rain_now": "Rain now", "snow_now": "Snow now",
        "alert": "Claude needs you", "limit_five": "5-hour limit", "limit_week": "Weekly limit",
        "notify_title": "MiniToo dashboard",
        "silent_body": "MiniToo is not responding: probably the phone holds it. Turn off Bluetooth on the phone and restart MiniToo",
        "keychain_body": "No Keychain access for Claude limits. In Terminal run: minitoo-dashboard grant-keychain",
        "cond_clear": "Clear", "cond_partly": "Partly cloudy", "cond_cloudy": "Cloudy", "cond_fog": "Fog",
        "cond_rain": "Rain", "cond_snow": "Snow", "cond_storm": "Storm",
    },
    "ru": {
        "now": "СЕЙЧАС", "until": "до {t}", "in_short": "{d}", "none_left": "Событий нет", "overdue": "ПРОСРОЧЕНО", "today": "СЕГОДНЯ",
        "h5": "5ч", "wk": "нед", "wk_short": "нд", "as_of_short": "на {t}", "reset": "сброшен", "reset_in": "сброс {d}", "as_of": "на {t}",
        "no_data": "Claude: нет данных", "no_weather": "Нет данных о погоде", "ago": "{d} назад",
        "rain_at": "Дождь {t}", "snow_at": "Снег {t}", "rain_now": "Идёт дождь", "snow_now": "Идёт снег",
        "alert": "Claude ждёт вас", "limit_five": "Лимит 5ч", "limit_week": "Лимит недели",
        "notify_title": "Дашборд MiniToo",
        "silent_body": "MiniToo не отвечает: вероятно, его держит телефон. Выключите Bluetooth на телефоне и перезагрузите MiniToo",
        "keychain_body": "Нет доступа к Keychain для лимитов Claude. В Терминале: minitoo-dashboard grant-keychain",
        "cond_clear": "Ясно", "cond_partly": "Перем. обл.", "cond_cloudy": "Облачно", "cond_fog": "Туман",
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
