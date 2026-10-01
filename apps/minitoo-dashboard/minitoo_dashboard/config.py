from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths


@dataclass
class Config:
    device_mac: str = ""
    city_name: str = ""
    city_lat: Optional[float] = None
    city_lon: Optional[float] = None
    temp_unit: str = "celsius"
    lang: str = "en"
    calendars: str = "all"
    send_delay_ms: int = 20
    claude_limits: str = "statusline"
    codex: str = "off"

    @property
    def has_city(self) -> bool:
        return self.city_lat is not None and self.city_lon is not None

    def calendar_list(self) -> Optional[List[str]]:
        if self.calendars.strip().lower() == "all":
            return None
        return [c.strip() for c in self.calendars.split(",") if c.strip()]


def _clean(value: str) -> str:
    value = value.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return None


def _clamp_int(value: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except ValueError:
        return default


def read_kv(path: Path) -> Dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    result: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = _clean(value)
    return result


def parse_config(text: str) -> Config:
    cfg = Config()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), _clean(value)
        if key == "DEVICE_MAC":
            cfg.device_mac = value.upper()
        elif key == "CITY_NAME":
            cfg.city_name = value
        elif key == "CITY_LAT":
            cfg.city_lat = _float(value)
        elif key == "CITY_LON":
            cfg.city_lon = _float(value)
        elif key == "TEMP_UNIT" and value in ("celsius", "fahrenheit"):
            cfg.temp_unit = value
        elif key == "LANG" and value in ("en", "ru"):
            cfg.lang = value
        elif key == "CALENDARS" and value:
            cfg.calendars = value
        elif key == "CLAUDE_LIMITS" and value in ("statusline", "direct"):
            cfg.claude_limits = value
        elif key == "CODEX" and value in ("on", "off"):
            cfg.codex = value
        elif key == "SEND_DELAY_MS":
            cfg.send_delay_ms = _clamp_int(value, 20, 0, 200)
    return cfg


def format_config(cfg: Config) -> str:
    lines = [
        "# MiniToo dashboard config. Edit freely; the daemon re-reads it every second.",
        f"DEVICE_MAC={cfg.device_mac}",
        f"CITY_NAME={cfg.city_name}",
    ]
    if cfg.has_city:
        lines += [f"CITY_LAT={cfg.city_lat}", f"CITY_LON={cfg.city_lon}"]
    lines += [
        f"TEMP_UNIT={cfg.temp_unit}",
        f"LANG={cfg.lang}",
        f"CALENDARS={cfg.calendars}",
        f"SEND_DELAY_MS={cfg.send_delay_ms}",
        f"CLAUDE_LIMITS={cfg.claude_limits}",
        f"CODEX={cfg.codex}",
    ]
    return "\n".join(line.replace("\n", " ") for line in lines) + "\n"


def load_config(path: Optional[Path] = None) -> Config:
    try:
        return parse_config(Path(path or paths.config_path()).read_text(encoding="utf-8"))
    except OSError:
        return Config()


def save_config(cfg: Config, path: Optional[Path] = None) -> None:
    target = Path(path or paths.config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(format_config(cfg), encoding="utf-8")
    tmp.replace(target)


def clauddy_alert(path: Optional[Path] = None) -> Optional[Tuple[int, int]]:
    kv = read_kv(Path(path or paths.clauddy_config_path()))
    try:
        return int(kv["CLAUDDY_CLOCK_ALERTING"]), int(kv["CLAUDDY_DEVICE_ID"])
    except (KeyError, ValueError):
        return None


def detect_temp_unit() -> str:
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleTemperatureUnit"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "celsius"
    return "fahrenheit" if out == "Fahrenheit" else "celsius"
