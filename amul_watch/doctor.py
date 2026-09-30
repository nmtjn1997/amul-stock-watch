"""`amul-watch doctor`: every precondition, checked in the order a new user hits them."""

from __future__ import annotations

import sys

from amul_watch import config as c


def _line(ok: bool | None, text: str) -> None:
    mark = {True: "OK  ", False: "FAIL", None: "WARN"}[ok]
    print(f"{mark}  {text}")


def run() -> int:
    failures = 0
    _line(sys.version_info >= (3, 10), f"python {sys.version.split()[0]} (needs 3.10+)")

    from amul_watch.client import AmulAPIError, find_curl

    try:
        _line(True, f"curl at {find_curl()}")
    except AmulAPIError as exc:
        _line(False, str(exc))
        return 1

    for path in (c.DEFAULT_CONFIG, c.NOTIFICATIONS_CONFIG):
        if path.is_file():
            _line(True, f"{path}")
        else:
            _line(False, f"{path} missing: run `amul-watch init`")
            failures += 1
    if failures:
        return 1

    c.load_session_env()
    cfg = c.load_config()
    from amul_watch.notification_routes import resolve_channel_names
    from amul_watch.notifiers import notifier_map, validate
    from amul_watch.pincodes import collect_pincodes
    from amul_watch.watchlist_util import ordered_watchlist

    pins = collect_pincodes(None, cfg)
    _line(bool(pins) or None, f"{len(pins)} enabled pincode(s)")
    watched = sum(len(ordered_watchlist(cfg, p["pincode"])) for p in pins)
    _line(bool(watched) or None, f"{watched} pincode x product watch(es)")

    notifiers = notifier_map(cfg)
    _line(bool(notifiers) or None, f"{len(notifiers)} notifier(s) defined")
    if not cfg.get("system_alerts"):
        _line(None, "system_alerts is empty: nobody is told if Amul becomes unreachable")
    import os
    import shutil

    headless = sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    for name, spec in notifiers.items():
        if spec.get("type") in ("desktop", "browser") and headless:
            _line(None, f"notifier {name} ({spec.get('type')}): no desktop session here (server or Docker), "
                        "it will not reach you. Add ntfy, Telegram or email")
            continue
        if spec.get("type") == "desktop" and sys.platform.startswith("linux") and not shutil.which("notify-send"):
            _line(None, f"notifier {name} (desktop): notify-send not installed (package libnotify-bin)")
            continue
        problems = validate(spec)
        _line(not problems, f"notifier {name} ({spec.get('type')})" + (f": {'; '.join(problems)}" if problems else ""))
        failures += bool(problems)
    for pin in pins:
        for item in ordered_watchlist(cfg, pin["pincode"]):
            names = resolve_channel_names(cfg, pin["pincode"], str(item["alias"]))
            missing = [n for n in names if n not in notifiers]
            if not names:
                _line(None, f"{pin['pincode']} {item.get('label')}: nobody is alerted (add a route or default_alerts)")
            elif missing:
                _line(False, f"{pin['pincode']} {item.get('label')}: route names unknown notifier(s) {missing}")
                failures += 1

    from amul_watch.client import AmulClient
    from amul_watch.session_guard import SessionGuard

    client = AmulClient(cfg, cookie_jar=c.CLI_COOKIE_JAR)
    ok = SessionGuard(client, cfg).check(strict=True)
    if not ok and not client.cookie:
        try:
            client.bootstrap_session()
            ok = SessionGuard(client, cfg).check(strict=True)
        except AmulAPIError:
            ok = False
    _line(ok, "Amul API reachable" + (" (imported cookie)" if client.cookie else " (automatic session)")
          + ("" if ok else ": see data/amul-watch.log. Behind a TLS proxy set CURL_CA_BUNDLE"))
    failures += not ok

    if ok and pins and watched:
        from amul_watch.pincodes import resolve_substore_cached
        from amul_watch.db import StockDB

        pin = pins[0]["pincode"]
        item = ordered_watchlist(cfg, pin)[0]
        try:
            substore, _ = resolve_substore_cached(client, StockDB(c.DB_PATH), pin)
            product = client.get_product(str(item["alias"]), substore)
            _line(bool(product), f"product lookup {item['alias']} @ {pin}"
                  + ("" if product else ": empty (check the alias against the shop URL)"))
            failures += not product
        except AmulAPIError as exc:
            _line(False, f"product lookup @ {pin}: {exc}")
            failures += 1

    print()
    print("all good" if not failures else f"{failures} problem(s)")
    return 0 if not failures else 1
