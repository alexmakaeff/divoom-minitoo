from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parent.parent
CORE_DIR = REPO_ROOT / "core"


def home() -> Path:
    return Path(os.environ.get("MINITOO_DASHBOARD_HOME", str(Path.home() / ".minitoo-dashboard")))


def sessions_dir() -> Path:
    return home() / "sessions"


def cache_dir() -> Path:
    return home() / "cache"


def config_path() -> Path:
    return home() / "config"


def log_path() -> Path:
    return home() / "dashboard.log"


def state_path() -> Path:
    return home() / "state.json"


def paused_path() -> Path:
    return home() / "paused"


def codex_sessions_dir() -> Path:
    return Path.home() / ".codex" / "sessions"  # CODEX_HOME is not seen by the launchd daemon


def clauddy_config_path() -> Path:
    return Path(os.environ.get("CLAUDDY_CONFIG", str(Path.home() / ".clauddy" / "config")))
