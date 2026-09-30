"""amul-watch command line.

`--home` is handled before anything imports amul_watch.config, because every path in
the program is derived from it at import time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

EPILOG = """
getting started:
  amul-watch init            create config in the home directory
  amul-watch serve           web UI + poller in one process (http://127.0.0.1:8847)
  amul-watch service install run `serve` in the background at every login

day to day:
  amul-watch status          is the poller alive, when did it last poll
  amul-watch stock           live table straight from the Amul API (no alerts)
  amul-watch inventory       last known stock from the local DB
  amul-watch alerts          what is watched right now and who it alerts
  amul-watch simulate stock --pincode 560001 [--product rose-lassi] [--dry-run]
  amul-watch pause | resume  stop and start polling (the UI stays up)

diagnostics:
  amul-watch doctor          check curl, config, notifiers and the Amul connection
  amul-watch paths           where config, data and logs live
"""


def _early_home(argv: list[str]) -> None:
    for i, arg in enumerate(argv):
        if arg == "--home" and i + 1 < len(argv):
            os.environ["AMUL_WATCH_HOME"] = argv[i + 1]
        elif arg.startswith("--home="):
            os.environ["AMUL_WATCH_HOME"] = arg.split("=", 1)[1]


def _client_guard_db():
    from amul_watch.client import AmulClient
    from amul_watch.config import CLI_COOKIE_JAR, DB_PATH, load_config, load_session_env
    from amul_watch.db import StockDB
    from amul_watch.session_guard import SessionGuard

    load_session_env()
    cfg = load_config()
    client = AmulClient(cfg, cookie_jar=CLI_COOKIE_JAR)
    return cfg, client, SessionGuard(client, cfg), StockDB(DB_PATH)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_init(args: argparse.Namespace) -> int:
    from amul_watch.config import HOME, init_home

    written = init_home(force=bool(args.force))
    for path in written:
        print(f"wrote {path}")
    if not written:
        print(f"config already exists in {HOME} (use --force to overwrite)")
    print("\nnext: edit the two YAML files or run `amul-watch serve` and use the web UI")
    return 0


def cmd_paths(_args: argparse.Namespace) -> int:
    from amul_watch import config as c

    for name in ("HOME", "DEFAULT_CONFIG", "NOTIFICATIONS_CONFIG", "SESSION_ENV", "DATA_DIR", "LOG_PATH", "DB_PATH"):
        print(f"{name:<21} {getattr(c, name)}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from amul_watch.config import load_config
    from amul_watch.daemon import setup_logging
    from amul_watch.webui import serve

    setup_logging()
    port = int(args.port if args.port is not None else load_config().get("ui_port", 8847))
    return serve(host=args.host, port=port, open_browser=not args.no_open, with_poller=args.cmd == "serve")


def cmd_run(_args: argparse.Namespace) -> int:
    from amul_watch.daemon import poll_loop, setup_logging

    setup_logging()
    try:
        poll_loop()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_poll(_args: argparse.Namespace) -> int:
    from amul_watch.poller import run_poll

    cfg, client, guard, db = _client_guard_db()
    summary = run_poll(client, db, cfg, guard=guard)
    print(json.dumps(summary, indent=2))
    return 0 if not summary.get("errors") else 1


def cmd_stock(_args: argparse.Namespace) -> int:
    from amul_watch.poller import print_stock_table, run_stock_report

    cfg, client, guard, db = _client_guard_db()
    rows = run_stock_report(client, db, cfg, guard=guard)
    print_stock_table(rows)
    return 0 if rows and not any(r["status"] == "ERROR" for r in rows) else 1


def cmd_inventory(args: argparse.Namespace) -> int:
    from amul_watch.inventory import load_inventory_rows, print_inventory_table, run_inventory_live, sync_inventory_csv

    cfg, client, guard, db = _client_guard_db()
    if args.live:
        run_inventory_live(
            cfg, db, client=client, guard=guard, refresh_interval=float(args.interval or 1.0),
            poll_api=bool(args.poll), in_stock_only=bool(args.in_stock), pincode=args.pincode,
            enabled_only=not args.all,
        )
        return 0
    csv_path = sync_inventory_csv(db, cfg)
    rows = load_inventory_rows(db, cfg, in_stock_only=bool(args.in_stock), pincode=args.pincode,
                               enabled_only=not args.all)
    print_inventory_table(rows, csv_path=csv_path)
    return 0


def cmd_pincodes(_args: argparse.Namespace) -> int:
    from amul_watch.config import load_config
    from amul_watch.pincodes import collect_pincodes

    pins = collect_pincodes(None, load_config())
    for loc in pins:
        print(f"{loc['pincode']}\t{loc['label']}")
    if not pins:
        print("no enabled pincodes")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    from amul_watch.config import load_config
    from amul_watch.notification_routes import describe_live_alerts, describe_routes, resolve_product_alias

    cfg = load_config()
    print("\n".join(describe_live_alerts(cfg)))
    print()
    alias = resolve_product_alias(cfg, args.product) if args.product else None
    print("\n".join(describe_routes(cfg, pincode=args.pincode, alias=alias)))
    return 0


def cmd_notifiers(_args: argparse.Namespace) -> int:
    from amul_watch.notifiers import NOTIFIER_TYPES, REQUIRED_KEYS

    for name, (_fn, desc) in NOTIFIER_TYPES.items():
        need = ", ".join(REQUIRED_KEYS.get(name, [])) or "-"
        print(f"{name:<14} {desc:<52} needs: {need}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from amul_watch.simulate import run_simulate

    return run_simulate(args.type, pincode=args.pincode or "", product=args.product or "", dry_run=args.dry_run)


def cmd_pause(args: argparse.Namespace) -> int:
    from amul_watch.daemon import pause

    res = pause(args.reason or "")
    print(f"amul-watch: PAUSED at {res['paused_at']} (the running process idles until resume)")
    return 0


def cmd_resume(_args: argparse.Namespace) -> int:
    from amul_watch.daemon import print_status, resume

    resume()
    print("amul-watch: resumed (next poll within a few seconds)")
    return print_status()


def cmd_status(_args: argparse.Namespace) -> int:
    from amul_watch.daemon import print_status

    return print_status()


def cmd_service(args: argparse.Namespace) -> int:
    from amul_watch import service

    return {"install": service.install, "uninstall": service.uninstall, "status": service.status}[args.action]()


def cmd_session(args: argparse.Namespace) -> int:
    from amul_watch import session_cli

    return session_cli.run(args.action)


def cmd_register_enquiries(args: argparse.Namespace) -> int:
    from amul_watch.enquiries import register_product_enquiries

    cfg, client, _guard, _db = _client_guard_db()
    summary = register_product_enquiries(client, cfg, dry_run=bool(args.dry_run))
    print(json.dumps(summary, indent=2))
    return 0 if not summary.get("errors") else 1


def cmd_doctor(_args: argparse.Namespace) -> int:
    from amul_watch.doctor import run

    return run()


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amul-watch",
        description="Watch shop.amul.com stock per pincode and alert people when products come back.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--home", help="config + data directory (default: AMUL_WATCH_HOME or the OS config dir)")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="create config.yaml and notifications.yaml from examples")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.set_defaults(func=cmd_init)
    sub.add_parser("paths", help="print where config, data and logs live").set_defaults(func=cmd_paths)

    for name, helptext in (("serve", "web UI + poller in one process"), ("ui", "web UI only")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--host", default=os.environ.get("AMUL_WATCH_HOST", "127.0.0.1"),
                       help="bind address (0.0.0.0 to expose; set AMUL_WATCH_UI_PASSWORD)")
        p.add_argument("--port", type=int, default=None, help="default: ui_port in config.yaml (8847)")
        p.add_argument("--no-open", action="store_true", help="do not open a browser")
        p.set_defaults(func=cmd_serve)
    sub.add_parser("run", help="poller only, in the foreground (no UI)").set_defaults(func=cmd_run)
    sub.add_parser("poll", help="one poll cycle now, with alerts (JSON summary)").set_defaults(func=cmd_poll)
    sub.add_parser("stock", help="live stock table from the Amul API (no alerts)").set_defaults(func=cmd_stock)

    p = sub.add_parser("inventory", help="last known stock from the local DB")
    p.add_argument("--all", action="store_true", help="include disabled pincodes/products")
    p.add_argument("--in-stock", action="store_true", help="only in-stock rows")
    p.add_argument("--pincode", help="one pincode")
    p.add_argument("--live", action="store_true", help="redraw every --interval seconds")
    p.add_argument("--poll", action="store_true", help="with --live: also poll the API (alerts off)")
    p.add_argument("--interval", type=float, default=1.0)
    p.set_defaults(func=cmd_inventory)

    sub.add_parser("pincodes", help="list enabled pincodes").set_defaults(func=cmd_pincodes)
    p = sub.add_parser("alerts", help="what is watched and who each watch alerts")
    p.add_argument("--pincode", help="also resolve one pincode...")
    p.add_argument("--product", help="...for this product (short name or alias)")
    p.set_defaults(func=cmd_alerts)
    sub.add_parser("notifiers", help="list notifier types and their settings").set_defaults(func=cmd_notifiers)

    p = sub.add_parser("simulate", help="send a TEST alert through the real notifiers")
    p.add_argument("type", choices=["stock", "system", "routes"])
    p.add_argument("--pincode")
    p.add_argument("--product")
    p.add_argument("--dry-run", action="store_true", help="show who would be alerted, send nothing")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("pause", help="stop polling (process and UI keep running)")
    p.add_argument("--reason", "-r", default="")
    p.set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="start polling again").set_defaults(func=cmd_resume)
    sub.add_parser("status", help="is the poller alive").set_defaults(func=cmd_status)

    p = sub.add_parser("service", help="run `serve` in the background at login (launchd/systemd/Task Scheduler)")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("session", help="Amul session: show state, import a browser cookie, reset")
    p.add_argument("action", choices=["show", "import", "reset"])
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("register-enquiries", help="sign enquiry_emails up for Amul's own notify-me form")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_register_enquiries)
    sub.add_parser("doctor", help="check the install end to end").set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _early_home(argv)
    from amul_watch.config import load_session_env

    load_session_env()
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 1
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
