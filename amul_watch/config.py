"""Where everything lives, and how config is loaded.

Every path is derived from one home directory so the same code runs on macOS, Linux,
Windows and inside Docker without edits:

    AMUL_WATCH_HOME            if set (Docker sets it to /data)
    ~/.config/amul-watch       macOS / Linux default
    %APPDATA%\\amul-watch       Windows default

Layout inside the home:

    config.yaml          pincodes, products, polling knobs
    notifications.yaml   notifiers (who) and alert routes (which pin:product goes where)
    .env                 secrets as KEY=value: SMTP password, bot tokens, optional AMUL_COOKIE
    data/                sqlite state, logs, notification history, cookie jar
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = PACKAGE_DIR / "examples"


def _default_home() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "amul-watch"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "amul-watch"


def home_dir() -> Path:
    raw = os.environ.get("AMUL_WATCH_HOME", "").strip()
    return Path(raw).expanduser() if raw else _default_home()


HOME = home_dir()
DEFAULT_CONFIG = HOME / "config.yaml"
NOTIFICATIONS_CONFIG = HOME / "notifications.yaml"
SESSION_ENV = HOME / ".env"
DATA_DIR = HOME / "data"
DB_PATH = DATA_DIR / "amul-watch.db"
LOG_PATH = DATA_DIR / "amul-watch.log"
STOCK_CSV_PATH = DATA_DIR / "stock-inventory.csv"
COOKIE_JAR = DATA_DIR / "cookies.txt"          # the poller's session
CLI_COOKIE_JAR = DATA_DIR / "cookies-cli.txt"  # one-off commands, so they never move the poller's session
UI_COOKIE_JAR = DATA_DIR / "cookies-ui.txt"    # the UI's inline poll under `amul-watch ui`
PAUSED_FILE = DATA_DIR / "paused.json"
HEARTBEAT_FILE = DATA_DIR / "heartbeat.json"


# Variables set before we started (shell, Docker, systemd) always beat .env.
_REAL_ENV = frozenset(os.environ)


def load_session_env(*, force: bool = False) -> None:
    """Load HOME/.env into the environment. force re-reads it so edits apply, but a
    variable from the real environment is never overwritten."""
    if not SESSION_ENV.is_file():
        return
    for raw in SESSION_ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if key in _REAL_ENV:
            continue
        if force or key not in os.environ:
            os.environ[key] = val


def reload_session_env() -> None:
    load_session_env(force=True)


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name} is not valid YAML, fix it and save again: {exc}") from exc
    return data if isinstance(data, dict) else {}


def load_notifications_config(path: Path | None = None) -> dict[str, Any]:
    return _load_yaml_file(path or NOTIFICATIONS_CONFIG)


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = _load_yaml_file(path or DEFAULT_CONFIG)
    notes = load_notifications_config()
    if notes:
        cfg.update(notes)
    cfg.setdefault("poll_interval_seconds", 60)
    cfg.setdefault("poll_jitter_seconds", 10)
    cfg.setdefault("poll_spread", True)
    cfg.setdefault("poll_spread_min_gap_seconds", 1.0)
    cfg.setdefault("poll_spread_request_delay_min", 0.02)
    cfg.setdefault("poll_spread_request_delay_max", 0.08)
    cfg.setdefault("poll_priority", {})
    cfg.setdefault("request_delay_min", 1.0)
    cfg.setdefault("request_delay_max", 2.0)
    cfg.setdefault("api_max_retries", 3)
    cfg.setdefault("session_check_every_polls", 5)
    cfg.setdefault("ui_port", 8847)
    cfg.setdefault("priority_pincode", "")
    cfg.setdefault("qty_update_min_delta", 2)
    cfg.setdefault("pincodes", [])
    cfg.setdefault("watchlist", [])
    return cfg


def init_home(*, force: bool = False) -> list[Path]:
    """Create the home layout from the bundled examples. Never overwrites unless force."""
    HOME.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for src_name, dest in (
        ("config.example.yaml", DEFAULT_CONFIG),
        ("notifications.example.yaml", NOTIFICATIONS_CONFIG),
        ("env.example", SESSION_ENV),
    ):
        if dest.exists() and (not force or dest == SESSION_ENV):
            continue  # --force never touches .env: it holds the user's secrets
        if dest.exists():
            dest.with_name(dest.name + ".bak").write_text(dest.read_text(encoding="utf-8"), encoding="utf-8")
        dest.write_text((EXAMPLES_DIR / src_name).read_text(encoding="utf-8"), encoding="utf-8")
        if dest == SESSION_ENV:
            try:
                os.chmod(dest, 0o600)
            except OSError:
                pass
        written.append(dest)
    return written
