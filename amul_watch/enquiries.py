from __future__ import annotations

import logging
import time
from typing import Any

from amul_watch.client import AmulAPIError, AmulClient

log = logging.getLogger(__name__)

DEFAULT_ENQUIRY_MESSAGE = "Please notify when in stock"


def _enquiry_name(item: dict[str, Any]) -> str:
    """The product name exactly as the shop shows it (the form matches on it)."""
    return str(item.get("enquiry_name") or item.get("label") or item.get("alias", ""))


def register_product_enquiries(
    client: AmulClient,
    cfg: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register Amul's own "notify me" form for each watchlist product x enquiry email."""
    from amul_watch.notification_routes import enquiry_emails

    emails = enquiry_emails(cfg)
    phone = str(cfg.get("phone") or "").strip()
    message = str((cfg.get("enquiry") or {}).get("message") or DEFAULT_ENQUIRY_MESSAGE)
    quantity = str((cfg.get("enquiry") or {}).get("quantity") or "1")
    from amul_watch.watchlist_util import ordered_watchlist

    watchlist = ordered_watchlist(cfg)

    summary: dict[str, Any] = {"registered": 0, "errors": [], "skipped": 0}
    if not emails:
        summary["errors"].append("no enquiry_emails in notifications.yaml")
        return summary
    if not phone:
        summary["errors"].append("set `phone:` in config.yaml (the Amul form requires a contact number)")
        return summary

    for item in watchlist:
        product_name = _enquiry_name(item)
        for email in emails:
            if dry_run:
                print(f"DRY  {email}\t{product_name}")
                summary["registered"] += 1
                continue
            try:
                resp = client.create_product_enquiry(
                    product_name=product_name,
                    email=email,
                    contact=phone,
                    quantity=quantity,
                    message=message,
                )
                eid = (resp.get("data") or {}).get("_id", "ok")
                log.info("enquiry registered %s → %s (%s)", product_name[:40], email, eid)
                print(f"OK   {email}\t{product_name}")
                summary["registered"] += 1
                time.sleep(float(cfg.get("request_delay_min", 1.0)))
            except AmulAPIError as exc:
                msg = f"{email} / {product_name}: {exc}"
                summary["errors"].append(msg)
                print(f"FAIL {msg}")

    return summary
