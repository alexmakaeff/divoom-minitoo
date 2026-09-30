#!/usr/bin/env python3
"""Claude Code status line command: caches subscription rate limits for the
MiniToo dashboard. Prints nothing, so the status line row stays empty."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minitoo_dashboard import paths, store  # noqa: E402
from minitoo_dashboard.sources import claude  # noqa: E402


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    record = claude.extract(data, time.time())
    if record:
        try:
            store.write_json_atomic(paths.cache_dir() / "claude.json", record)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
