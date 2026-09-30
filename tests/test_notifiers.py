from __future__ import annotations

import sys

from amul_watch import notifiers as n
from amul_watch.notifiers import Alert


def test_env_expansion(monkeypatch) -> None:
    monkeypatch.setenv("AMUL_TEST_TOKEN", "s3cret")
    assert n.expand({"a": "x-${AMUL_TEST_TOKEN}", "b": ["${AMUL_TEST_TOKEN}"]}) == {"a": "x-s3cret", "b": ["s3cret"]}
    assert n.expand("${AMUL_UNSET_VAR}") == ""


def test_validate_reports_missing_keys() -> None:
    assert n.validate({"type": "telegram", "chat_id": "1"}) == ["missing `bot_token`"]
    assert n.validate({"type": "nope"})[0].startswith("unknown type")
    assert n.validate({"type": "desktop"}) == []


def test_unset_env_reference_is_saveable_but_flagged(monkeypatch) -> None:
    monkeypatch.delenv("AMUL_UNSET_TOKEN", raising=False)
    spec = {"type": "slack_webhook", "url": "${AMUL_UNSET_TOKEN}"}
    assert n.validate(spec, resolve=False) == []
    assert "AMUL_UNSET_TOKEN" in n.validate(spec)[0]


def test_send_isolates_failures(monkeypatch) -> None:
    calls = []

    def ok(spec, alert, cfg):
        calls.append(alert.title)

    def boom(spec, alert, cfg):
        raise RuntimeError("down")

    monkeypatch.setitem(n.NOTIFIER_TYPES, "ok", (ok, ""))
    monkeypatch.setitem(n.NOTIFIER_TYPES, "boom", (boom, ""))
    cfg = {"notifiers": {"x": {"type": "boom"}, "y": {"type": "ok"}, "z": {"type": "ok", "enabled": False}}}
    results = n.send(cfg, ["x", "y", "z", "missing"], Alert(kind="test", title="T", message="m"))
    assert results["x"] == "error: down"
    assert results["y"] == "ok"
    assert results["z"] == "skipped: disabled"
    assert results["missing"].startswith("error")
    assert calls == ["T"]


def test_command_notifier_gets_alert_env(tmp_path) -> None:
    out = tmp_path / "out.txt"
    script = f"import os; open({str(out)!r}, 'w').write(os.environ['AMUL_PRODUCT'] + '|' + os.environ['AMUL_PINCODES'])"
    spec = {"type": "command", "command": [sys.executable, "-c", script]}
    n.send_command(spec, Alert(kind="stock", title="t", message="m", product="Rose", pincodes=["560001"]), {})
    assert out.read_text() == "Rose|560001"
