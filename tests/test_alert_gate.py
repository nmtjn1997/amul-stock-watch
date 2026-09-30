"""Alert gate: one alert per restock, retried when nobody could be told, and quantity
updates measured against the last reported value."""

from __future__ import annotations

from amul_watch import product_poll as pp
from amul_watch.db import StockDB
from amul_watch.product_poll import PinPollResult
from amul_watch.stock import ProductStock

ALIAS = "rose"


def _result(pin: str, qty: int, had_data: bool = True) -> PinPollResult:
    stock = ProductStock(alias=ALIAS, name="Rose", url="u", variants=[], best=None, pack_of_30=None,
                         any_in_stock=qty > 0)
    return PinPollResult(pincode=pin, pin_label=pin, alias=ALIAS, product_label="Rose", stock=stock,
                         in_stock=qty > 0, qty=qty, variant_label="", was_in_stock=False, prev_qty=0,
                         key=f"{pin}:{ALIAS}", had_data=had_data)


def _run(monkeypatch, tmp_path, cycles, delivered=True):
    db = StockDB(tmp_path / "t.db")
    sent: list[list[str]] = []

    def fake_fire(cfg, db, client, **kw):
        sent.append(kw["alert_pincodes"])
        return delivered

    monkeypatch.setattr(pp, "fire_transition_alert", fake_fire)
    for results in cycles:
        pp.process_product_alerts({}, db, None, results)
    return sent


def test_one_alert_per_restock(monkeypatch, tmp_path) -> None:
    sent = _run(monkeypatch, tmp_path, [[_result("1", 5)], [_result("1", 5)], [_result("1", 0)], [_result("1", 3)]])
    assert sent == [["1"], ["1"]]


def test_failed_delivery_is_retried_after_backoff(monkeypatch, tmp_path) -> None:
    clock = [1000.0]
    monkeypatch.setattr(pp.time, "time", lambda: clock[0])
    db = StockDB(tmp_path / "t.db")
    sent: list[list[str]] = []
    monkeypatch.setattr(pp, "fire_transition_alert", lambda cfg, db, client, **kw: sent.append(kw["alert_pincodes"]) or False)
    pp.process_product_alerts({}, db, None, [_result("1", 5)])
    pp.process_product_alerts({}, db, None, [_result("1", 5)])   # inside the backoff: no resend
    clock[0] += pp.RETRY_BACKOFF_SECONDS + 1
    pp.process_product_alerts({}, db, None, [_result("1", 5)])
    assert sent == [["1"], ["1"]]


def test_empty_body_does_not_rearm_the_gate(monkeypatch, tmp_path) -> None:
    sent = _run(monkeypatch, tmp_path, [
        [_result("1", 5), _result("2", 5)],
        [_result("1", 5), _result("2", 0, had_data=False)],
        [_result("1", 5), _result("2", 5)],
    ])
    assert sent == [["1", "2"]]


def test_unwatched_pairs_are_forgotten(tmp_path) -> None:
    db = StockDB(tmp_path / "t.db")
    db.mark_alert("1:rose")
    db.mark_alert("2:rose")
    db.prune_alerts({"1:rose"})
    assert db.alert_sent("1:rose") and not db.alert_sent("2:rose")


def test_slow_drain_is_reported(monkeypatch, tmp_path) -> None:
    reported: list[tuple[int, int]] = []
    monkeypatch.setattr(pp, "notify_stock_qty",
                        lambda cfg, **kw: reported.append((kw["prev_qty"], kw["qty"])) or abs(kw["prev_qty"] - kw["qty"]) >= 2)
    _run(monkeypatch, tmp_path, [[_result("1", q)] for q in (10, 9, 8, 7, 6, 5)])
    assert (10, 8) in reported and (8, 6) in reported
