"""Notifier types: every way an alert can leave this program.

A notifier is a named entry under `notifiers:` in notifications.yaml:

    notifiers:
      me-email:  {type: email, to: [me@example.com]}
      phone:     {type: ntfy, topic: my-amul-alerts}
      desktop:   {type: desktop}

Routes then refer to those names. Adding a new *type* is one function plus one line in
NOTIFIER_TYPES; see docs/EXTENDING.md.

String values may reference environment variables as ${NAME}, so secrets stay in the
.env file (or the container environment) instead of the YAML.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import smtplib
import subprocess
import sys
import webbrowser
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from typing import Any, Callable

from amul_watch.http_util import post_json, post_text

log = logging.getLogger(__name__)


@dataclass
class Alert:
    """What gets sent. `kind` is stock (0 to in-stock), qty, system or test."""

    kind: str
    title: str
    message: str
    url: str = ""
    product: str = ""
    alias: str = ""
    pincodes: list[str] = field(default_factory=list)
    qty: int | None = None
    price: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotifierError(RuntimeError):
    pass


_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def expand(value: Any) -> Any:
    """Replace ${VAR} with the environment value. Missing vars become empty strings."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    return value


def _require(spec: dict[str, Any], key: str) -> str:
    val = str(spec.get(key) or "").strip()
    if not val:
        raise NotifierError(f"missing `{key}`")
    return val


# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #

def send_email(spec: dict[str, Any], alert: Alert, cfg: dict[str, Any]) -> None:
    """SMTP. Server settings live once under `email:` in notifications.yaml."""
    smtp = expand(cfg.get("email") or {})
    to = [str(a).strip() for a in (spec.get("to") or []) if str(a).strip()]
    if not to:
        raise NotifierError("email notifier has no `to` addresses")
    host = _require(smtp, "host")
    port = int(smtp.get("port") or 587)
    msg = EmailMessage()
    msg["Subject"] = alert.title
    msg["From"] = str(smtp.get("from") or smtp.get("username") or "amul-watch@localhost")
    msg["To"] = ", ".join(to)
    msg.set_content(alert.message)
    if smtp.get("ssl") or port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if smtp.get("starttls", True):
            server.starttls()
    with server:
        if smtp.get("username"):
            server.login(str(smtp["username"]), str(smtp.get("password") or ""))
        server.send_message(msg)


def send_slack_webhook(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    post_json(_require(spec, "url"), {"text": alert.message})


def send_slack(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    """Slack bot token. `channel` is a channel id (C...) or a user id (U...) for a DM."""
    token = _require(spec, "token")
    body = post_json(
        "https://slack.com/api/chat.postMessage",
        {"channel": _require(spec, "channel"), "text": alert.message},
        headers={"Authorization": f"Bearer {token}"},
    )
    if isinstance(body, dict) and not body.get("ok", False):
        raise NotifierError(f"slack: {body.get('error', body)}")


def send_discord(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    post_json(_require(spec, "url"), {"content": alert.message[:1900]})


def send_telegram(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    token = _require(spec, "bot_token")
    body = post_json(
        f"https://api.telegram.org/bot{token}/sendMessage",
        {"chat_id": _require(spec, "chat_id"), "text": alert.message, "disable_web_page_preview": True},
    )
    if isinstance(body, dict) and not body.get("ok", False):
        raise NotifierError(f"telegram: {body.get('description', body)}")


def send_ntfy(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    """ntfy.sh (or a self-hosted server): free push to the ntfy phone app, no account."""
    server = str(spec.get("server") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": alert.title.encode("ascii", "ignore").decode() or "Amul stock"}
    if alert.url:
        headers["Click"] = alert.url
    if alert.kind == "stock":
        headers["Priority"] = "high"
    if spec.get("token"):
        headers["Authorization"] = f"Bearer {spec['token']}"
    post_text(f"{server}/{_require(spec, 'topic')}", alert.message, headers=headers)


def send_webhook(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    """Generic JSON POST of the whole alert, for Zapier, n8n, Home Assistant, your own API."""
    headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
    post_json(_require(spec, "url"), alert.as_dict(), headers=headers)


def send_command(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    """Run any local command. Alert fields arrive as AMUL_* environment variables.

    The escape hatch for anything without a built-in type (a personal CLI, a script).
    """
    command = spec.get("command")
    if not command:
        raise NotifierError("missing `command`")
    argv = command if isinstance(command, list) else shlex.split(str(command))
    env = dict(os.environ)
    env.update(
        {
            "AMUL_KIND": alert.kind,
            "AMUL_TITLE": alert.title,
            "AMUL_MESSAGE": alert.message,
            "AMUL_URL": alert.url,
            "AMUL_PRODUCT": alert.product,
            "AMUL_PINCODES": ",".join(alert.pincodes),
            "AMUL_QTY": "" if alert.qty is None else str(alert.qty),
            "AMUL_ALERT_JSON": json.dumps(alert.as_dict()),
        }
    )
    proc = subprocess.run([str(a) for a in argv], env=env, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise NotifierError(f"command exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")


def _applescript_str(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send_desktop(spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    """Notification on the machine running amul-watch. `sticky: true` shows a dialog
    that stays until dismissed (macOS and Windows). Does nothing useful in Docker."""
    title, body = alert.title, alert.message
    sticky = bool(spec.get("sticky"))
    if sys.platform == "darwin":
        if sticky:
            script = (
                f'display dialog "{_applescript_str(body)}" with title "{_applescript_str(title)}" '
                'buttons {"OK"} default button "OK" with icon note'
            )
            subprocess.Popen(["osascript", "-e", script])
        else:
            first = body.splitlines()[0] if body else ""
            script = (
                f'display notification "{_applescript_str(first)}" '
                f'with title "{_applescript_str(title)}" sound name "Glass"'
            )
            subprocess.run(["osascript", "-e", script], check=False, timeout=15)
    elif sys.platform.startswith("win"):
        ps_title = title.replace("'", "''")
        ps_body = body.replace("'", "''")
        wait = 0 if sticky else 20
        script = f"(New-Object -ComObject Wscript.Shell).Popup('{ps_body}', {wait}, '{ps_title}', 64)"
        subprocess.Popen(["powershell", "-NoProfile", "-Command", script])
    else:
        try:
            subprocess.run(["notify-send", "-u", "critical" if sticky else "normal", title, body],
                           check=False, timeout=15)
        except FileNotFoundError as exc:
            raise NotifierError("notify-send not found (install libnotify, or use ntfy in Docker)") from exc


def send_browser(_spec: dict[str, Any], alert: Alert, _cfg: dict[str, Any]) -> None:
    """Open the product page in the default browser on the machine running amul-watch."""
    if alert.url and alert.kind in ("stock", "test"):
        webbrowser.open(alert.url)


SendFn = Callable[[dict[str, Any], Alert, dict[str, Any]], None]

# type -> (function, one-line description shown in the UI and `amul-watch notifiers`)
NOTIFIER_TYPES: dict[str, tuple[SendFn, str]] = {
    "email": (send_email, "email via SMTP (Gmail app password works)"),
    "ntfy": (send_ntfy, "push to the ntfy phone app, no account needed"),
    "telegram": (send_telegram, "Telegram bot message"),
    "discord": (send_discord, "Discord channel webhook"),
    "slack_webhook": (send_slack_webhook, "Slack incoming webhook"),
    "slack": (send_slack, "Slack bot token, channel or person"),
    "webhook": (send_webhook, "POST the alert as JSON to any URL"),
    "command": (send_command, "run a local command with the alert in env vars"),
    "desktop": (send_desktop, "desktop notification on this machine"),
    "browser": (send_browser, "open the product page in the browser"),
}

# Keys each type needs, for validation in the UI and `amul-watch doctor`.
REQUIRED_KEYS: dict[str, list[str]] = {
    "email": ["to"],
    "ntfy": ["topic"],
    "telegram": ["bot_token", "chat_id"],
    "discord": ["url"],
    "slack_webhook": ["url"],
    "slack": ["token", "channel"],
    "webhook": ["url"],
    "command": ["command"],
    "desktop": [],
    "browser": [],
}


def notifier_map(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = cfg.get("notifiers") or {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def validate(spec: dict[str, Any], *, resolve: bool = True) -> list[str]:
    """Problems with one notifier's settings. resolve=False only checks the fields are
    filled in, so a ${VAR} reference can be saved before the variable exists."""
    kind = str(spec.get("type") or "")
    if kind not in NOTIFIER_TYPES:
        return [f"unknown type {kind!r} (known: {', '.join(NOTIFIER_TYPES)})"]
    problems = [f"missing `{k}`" for k in REQUIRED_KEYS.get(kind, []) if not spec.get(k)]
    if resolve and not problems:
        for key in REQUIRED_KEYS.get(kind, []):
            raw = spec.get(key)
            if not expand(raw):
                refs = ", ".join(_ENV_RE.findall(str(raw))) or key
                problems.append(f"`{key}` uses {refs}, which is not set (add it to .env)")
    return problems


def send(cfg: dict[str, Any], names: list[str], alert: Alert) -> dict[str, str]:
    """Deliver one alert to each named notifier. Never raises; returns name -> 'ok' | error."""
    notifiers = notifier_map(cfg)
    results: dict[str, str] = {}
    for name in names:
        spec = notifiers.get(name)
        if spec is None:
            results[name] = "error: no notifier with this name"
            continue
        if spec.get("enabled") is False:
            results[name] = "skipped: disabled"
            continue
        kind = str(spec.get("type") or "")
        entry = NOTIFIER_TYPES.get(kind)
        if entry is None:
            results[name] = f"error: unknown type {kind!r}"
            continue
        try:
            entry[0](expand(spec), alert, cfg)
            results[name] = "ok"
        except Exception as exc:  # one broken notifier must not stop the others
            log.error("notifier %s (%s) failed: %s", name, kind, exc)
            results[name] = f"error: {exc}"
    log.info(
        "ALERT %s %r -> %s",
        alert.kind,
        alert.title,
        ", ".join(f"{n}={r}" for n, r in results.items()) or "(no notifiers)",
    )
    return results
