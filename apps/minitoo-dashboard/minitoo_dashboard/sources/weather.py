from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
PRECIP_THRESHOLD = 50
MAX_AGE = 6 * 3600
AGE_MARKER_AFTER = 3600

Get = Callable[..., Any]


def http_get_json(url: str, params: dict, timeout: float = 10) -> Any:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "minitoo-dashboard"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def condition_key(code: int) -> str:
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 95 <= code <= 99:
        return "storm"
    return "cloudy"


@dataclass
class Place:
    name: str
    country: str
    admin1: str
    lat: float
    lon: float

    def label(self) -> str:
        parts = [self.name]
        if self.admin1 and self.admin1 != self.name:
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


def geocode(name: str, lang: str, get: Get = http_get_json) -> List[Place]:
    data = get(GEOCODE_URL, {"name": name, "count": 5, "language": lang, "format": "json"})
    places = []
    for r in (data or {}).get("results") or []:
        if "latitude" in r and "longitude" in r:
            places.append(Place(r.get("name", ""), r.get("country", ""), r.get("admin1", ""),
                                float(r["latitude"]), float(r["longitude"])))
    return places


def fetch_weather(lat: float, lon: float, temp_unit: str, now: float, get: Get = http_get_json) -> dict:
    data = get(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code",
        "hourly": "precipitation_probability,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": temp_unit, "timezone": "auto", "forecast_days": 1,
    })
    current, hourly, daily = data["current"], data["hourly"], data["daily"]
    current_hour = current["time"][:13]
    precip_from: Optional[str] = None
    precip_kind: Optional[str] = None
    for t, prob, code in zip(hourly["time"], hourly["precipitation_probability"], hourly["weather_code"]):
        if t[:13] < current_hour or prob is None or prob < PRECIP_THRESHOLD:
            continue
        precip_from = "now" if t[:13] == current_hour else t[11:16]
        precip_kind = "snow" if condition_key(int(code)) == "snow" else "rain"
        break
    return {
        "fetched_at": now, "lat": lat, "lon": lon,
        "temp": round(current["temperature_2m"]), "code": int(current["weather_code"]),
        "tmax": round(daily["temperature_2m_max"][0]), "tmin": round(daily["temperature_2m_min"][0]),
        "precip_from": precip_from, "precip_kind": precip_kind,
    }


@dataclass
class WeatherView:
    temp: int
    tmax: int
    tmin: int
    condition: str
    precip_from: Optional[str]
    precip_kind: Optional[str]
    age_s: Optional[float]


def weather_view(cache: Any, now: float) -> Optional[WeatherView]:
    if not isinstance(cache, dict) or "fetched_at" not in cache:
        return None
    age = now - float(cache["fetched_at"])
    if age > MAX_AGE:
        return None
    return WeatherView(cache["temp"], cache["tmax"], cache["tmin"], condition_key(cache["code"]),
                       cache.get("precip_from"), cache.get("precip_kind"),
                       age if age > AGE_MARKER_AFTER else None)
