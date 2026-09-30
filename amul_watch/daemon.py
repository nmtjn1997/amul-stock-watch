"""The poll loop, plus pause / resume / status.

One process does everything: `amul-watch serve` runs this loop in a background thread
and the web UI in the foreground; `amul-watch run` runs the loop alone (headless).

Config is re-read at the start of every cycle, so edits from the UI or a text editor
apply within one poll interval without a restart. Pause is a flag file checked every
cycle, so it needs no process management either. Liveness is a heartbeat file the loop
rewrites after each cycle, which works the same on every OS and inside Docker.
"""

from __future__ import annotations

import json
import logging
import random
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Any

from amul_watch.client import AmulClient
from amul_watch.config import (
    DATA_DIR,
    DB_PATH,
    HEARTBEAT_FILE,
    LOG_PATH,
    PAUSED_FILE,
    load_config,
    reload_session_env,
)
from amul_watch.db import StockDB
from amul_watch.pincodes import collect_pincodes
from amul_watch.poll_spread import run_spread_poll, spread_client_cfg
from amul_watch.poller import run_poll
from amul_watch.session_guard import SessionGuard

log = logging.getLogger(__name__)

# Set by the UI's "Force refresh" to start the next cycle now. Only meaningful when the
# poller runs in this process (`serve`); POLLER_RUNNING says whether it does.
WAKE = threading.Event()
POLLER_RUNNING = threading.Event()


def setup_logging(*, to_stdout: bool = True) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)]
    if to_stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(fmt)
        root.addHandler(handler)
    root.setLevel(logging.INFO)


# --------------------------------------------------------------------------- #
# Pause flag
# --------------------------------------------------------------------------- #

def is_paused() -> bool:
    return PAUSED_FILE.is_file()


def pause_info() -> dict[str, Any] | None:
    if not is_paused():
        return None
    try:
        data = json.loads(PAUSED_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def pause(reason: str = "") -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"paused_at": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason.strip() or "paused by user"}
    PAUSED_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"paused": True, **payload}


def resume() -> dict[str, Any]:
    PAUSED_FILE.unlink(missing_ok=True)
    return {"paused": False}


# --------------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------------- #

def _write_heartbeat(state: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HEARTBEAT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({**state, "ts": time.time()}), encoding="utf-8")
        tmp.replace(HEARTBEAT_FILE)
    except OSError:
        pass


def watcher_state() -> dict[str, Any]:
    """What the UI and `status` show. Running = heartbeat newer than ~3 poll intervals."""
    state: dict[str, Any] = {"paused": is_paused(), "running": False, "last_poll": None}
    if state["paused"]:
        state["pause_info"] = pause_info() or {}
    try:
        beat = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    interval = float(load_config().get("poll_interval_seconds", 60))
    age = time.time() - float(beat.get("ts") or 0)
    state["running"] = age < max(180.0, interval * 3)
    state["last_poll"] = beat
    state["heartbeat_age_seconds"] = round(age)
    return state


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #

def poll_loop(stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    cfg = load_config()
    spread = bool(cfg.get("poll_spread", True))
    client = AmulClient(spread_client_cfg(cfg) if spread else cfg)
    db = StockDB(DB_PATH)
    guard = SessionGuard(client, cfg)
    log.info("amul-watch poller started (every %ss)", cfg.get("poll_interval_seconds", 60))
    POLLER_RUNNING.set()

    cycle = 0
    prev_pins = -1
    try:
        while not stop.is_set():
            started = time.monotonic()
            interval = 60.0
            try:
                cycle, prev_pins, interval = _one_cycle(client, db, guard, cycle, prev_pins, stop)
            except Exception as exc:  # a bad config value or a bug must never end polling
                log.exception("poll cycle failed: %s", exc)
                _write_heartbeat({"cycle": cycle, "errors": [f"cycle failed: {exc}"]})
            if stop.is_set():
                break
            _sleep_until_next(stop, started, interval)
    finally:
        POLLER_RUNNING.clear()


def _cfg_number(cfg: dict[str, Any], key: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(cfg.get(key, default)))
    except (TypeError, ValueError):
        log.warning("config %s=%r is not a number, using %s", key, cfg.get(key), default)
        return default


def _one_cycle(client: AmulClient, db: StockDB, guard: SessionGuard, cycle: int, prev_pins: int,
               stop: threading.Event) -> tuple[int, int, float]:
    try:
        cfg = load_config()
    except Exception as exc:
        log.warning("config reload failed, keeping previous: %s", exc)
        cfg = guard.cfg
    guard.cfg = cfg
    interval = _cfg_number(cfg, "poll_interval_seconds", 60, 10)

    if is_paused():
        _write_heartbeat({"paused": True, "cycle": cycle})
        WAKE.clear()
        stop.wait(5)
        return cycle, prev_pins, 0.0

    reload_session_env()
    client.reload_from_env()
    cycle += 1
    n_pins = len(collect_pincodes(client, cfg))
    if n_pins != prev_pins:
        log.info("config: polling %s pincode(s)", n_pins)
        prev_pins = n_pins

    summary: dict[str, Any] = {"checks": 0, "alerts": 0, "errors": []}
    # A spread cycle takes a whole interval, so mark liveness before it starts too.
    _write_heartbeat({"cycle": cycle, "pincodes": n_pins, "polling": True})
    if n_pins:
        every = int(_cfg_number(cfg, "session_check_every_polls", 5, 1))
        session_ok = True
        if cycle == 1 or cycle % every == 0:
            session_ok = guard.ensure_valid()
        if session_ok:
            poll = run_spread_poll if cfg.get("poll_spread", True) else run_poll
            summary = poll(client, db, cfg, guard=guard, check_session=False)
            log.info("poll done checks=%s alerts=%s errors=%s",
                     summary["checks"], summary["alerts"], len(summary["errors"]))
            for err in summary["errors"][:5]:
                log.warning("%s", err)
        else:
            summary["errors"] = ["Amul session unavailable"]

    _write_heartbeat(
        {
            "cycle": cycle,
            "pincodes": n_pins,
            "checks": summary.get("checks", 0),
            "alerts": summary.get("alerts", 0),
            "errors": [str(e) for e in summary.get("errors", [])][:5],
        }
    )
    return cycle, prev_pins, interval


def _sleep_until_next(stop: threading.Event, started: float, interval: float) -> None:
    if interval <= 0:
        return
    cfg = load_config()
    jitter = _cfg_number(cfg, "poll_jitter_seconds", 10, 0)
    deadline = started + interval + random.uniform(0, jitter)
    while not stop.is_set() and time.monotonic() < deadline:
        if WAKE.wait(min(1.0, max(0.0, deadline - time.monotonic()))):
            WAKE.clear()
            log.info("poll requested from the UI")
            return


def start_background(stop: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=poll_loop, args=(stop,), name="amul-poller", daemon=True)
    thread.start()
    return thread


def print_status() -> int:
    state = watcher_state()
    if state["paused"]:
        info = state.get("pause_info") or {}
        print(f"amul-watch: PAUSED since {info.get('paused_at', '?')} ({info.get('reason', '')})")
        print("  resume: amul-watch resume")
        return 0
    beat = state.get("last_poll") or {}
    if state["running"]:
        print(f"amul-watch: running (last poll {state['heartbeat_age_seconds']}s ago, "
              f"{beat.get('pincodes', 0)} pincode(s), {beat.get('checks', 0)} checks)")
        return 0
    if beat:
        print(f"amul-watch: NOT running (last heartbeat {state['heartbeat_age_seconds']}s ago)")
    else:
        print("amul-watch: NOT running (never polled). Start it with: amul-watch serve")
    print(f"  log: {LOG_PATH}")
    return 1
