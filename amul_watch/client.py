from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

from amul_watch.rate_limit import RateLimiter, retry_delay

log = logging.getLogger(__name__)

BASE_URL = "https://shop.amul.com"
PRODUCT_API = f"{BASE_URL}/api/1/entity/ms.products"
PINCODE_API = f"{BASE_URL}/entity/pincode"
SETTINGS_API = f"{BASE_URL}/entity/ms.settings/_/setPreferences"
INFO_JS = f"{BASE_URL}/user/info.js"
ENQUIRIES_API = f"{BASE_URL}/api/1/entity/ms.product_enquiries"
STORE_ID = "62fa94df8c13af2e242eba16"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def _looks_like_cloudflare(raw: str) -> bool:
    snippet = raw[:2000].lower()
    return (
        snippet.lstrip().startswith("<!doctype")
        or snippet.lstrip().startswith("<html")
        or "cf-browser-verification" in snippet
        or "just a moment" in snippet
        or "cloudflare" in snippet and "challenge" in snippet
    )


def find_curl(configured: str | None = None) -> str:
    """curl is the transport: its TLS handshake passes Cloudflare where urllib often does not.

    Bundled with macOS, Windows 10+ (curl.exe), most Linux distros and the Docker image.
    """
    for candidate in (configured, os.environ.get("AMUL_CURL"), "curl"):
        if candidate:
            found = shutil.which(candidate)
            if found:
                return found
    raise AmulAPIError(
        "curl not found on PATH. Install curl (it ships with macOS and Windows 10+) "
        "or set AMUL_CURL to its full path."
    )


def _curl_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def curl_config(url: str, headers: dict[str, str]) -> str:
    """URL and headers as a curl config file (owner-only), so cookies and tokens never
    appear in the process list the way command-line arguments do."""
    fd, path = tempfile.mkstemp(prefix="amul-curl-", suffix=".cfg")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"url = {_curl_quote(url)}\n")
        for key, value in headers.items():
            fh.write(f"header = {_curl_quote(f'{key}: {value}')}\n")
    return path


def _run_curl(cmd: list[str], *, input_text: str | None = None, timeout: int = 40,
              config: str | None = None) -> subprocess.CompletedProcess[str]:
    """A hung request becomes a retryable AmulAPIError, never an exception that escapes the poller."""
    try:
        return subprocess.run(cmd + ["--max-time", str(timeout)], input=input_text,
                              capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired as exc:
        raise AmulAPIError("request timed out", retryable=True) from exc
    finally:
        if config:
            try:
                os.unlink(config)
            except OSError:
                pass


class AmulAPIError(Exception):
    def __init__(self, message: str, *, status: int | None = None, body: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retryable = retryable


class AmulClient:
    def __init__(
        self,
        cfg: dict[str, Any] | None = None,
        *,
        delay_seconds: float | None = None,
        cookie_jar: Path | None = None,
    ) -> None:
        cfg = cfg or {}
        dmin = float(cfg.get("request_delay_min", 1.0))
        dmax = float(cfg.get("request_delay_max", 2.0))
        if delay_seconds is not None:
            dmin = dmax = delay_seconds
        self._limiter = RateLimiter(min_interval=0.2, delay_range=(dmin, dmax))
        self.max_retries = int(cfg.get("api_max_retries", 3))
        self._curl = str(cfg.get("curl_path") or "")
        self._session_tid = os.environ.get("AMUL_SESSION_TID", "bootstrap")
        # Manual cookie is optional. Without it the client keeps its own anonymous
        # session in a cookie jar, which is all stock checks need.
        self.cookie = os.environ.get("AMUL_COOKIE", "").strip()
        self.ms_ga = os.environ.get("AMUL_MS_GA", "").strip() or _random_ga_id()
        from amul_watch.config import COOKIE_JAR

        # One jar per long-lived caller: the poller and one-off commands must not share a
        # session, because the session's selected pincode decides what stock is returned.
        self.cookie_jar: Path = cookie_jar or COOKIE_JAR
        self._active_pin: str | None = None
        self._wanted_pin: str | None = None
        self._pin_store: dict[str, str] = {}
        self._reselecting = False

    def _curl_base(self) -> list[str]:
        cmd = [find_curl(self._curl), "-sS", "--globoff"]
        if not self.cookie:
            self.cookie_jar.parent.mkdir(parents=True, exist_ok=True)
            cmd += ["-b", str(self.cookie_jar), "-c", str(self.cookie_jar)]
        return cmd

    def has_session(self) -> bool:
        if self.cookie:
            return True
        try:
            return "jsessionid" in self.cookie_jar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    def bootstrap_session(self) -> None:
        """Open the shop homepage once so the jar holds a fresh anonymous session."""
        if self.cookie:
            return
        try:
            self.cookie_jar.unlink(missing_ok=True)
        except OSError:
            pass
        self._curl_text(f"{BASE_URL}/en/", referer=f"{BASE_URL}/")
        self._session_tid = "bootstrap"
        self._active_pin = None
        if not self.has_session():
            raise AmulAPIError("could not start an Amul session (no jsessionid cookie returned)", retryable=True)
        log.info("started a new anonymous Amul session")
        # A new session starts in the shop's default region; select the pincode that was
        # being read, so a retried request never returns another region's stock.
        wanted = self._wanted_pin
        if wanted in self._pin_store and not self._reselecting:
            self._reselecting = True
            try:
                self.use_pincode(wanted, self._pin_store[wanted])
            finally:
                self._reselecting = False

    def reload_from_env(self) -> None:
        self.cookie = os.environ.get("AMUL_COOKIE", self.cookie).strip()
        self.ms_ga = os.environ.get("AMUL_MS_GA", self.ms_ga).strip()
        tid = os.environ.get("AMUL_SESSION_TID")
        if tid:
            self._session_tid = tid

    def _make_tid(self) -> str:
        ts = str(int(time.time() * 1000))
        rand = str(random.randint(100, 999))
        base = f"{STORE_ID}:{ts}:{rand}:{self._session_tid}"
        digest = hashlib.sha256(base.encode()).hexdigest()
        return f"{ts}:{rand}:{digest}"

    def _headers(self, *, referer: str, with_json: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "User-Agent": USER_AGENT,
            "frontend": "1",
            "ms-ga": self.ms_ga,
            "tid": self._make_tid(),
            "base_url": referer,
            "Referer": referer,
            "Origin": BASE_URL,
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-amul-b2c-access-key": "shop.amul.com",
        }
        if with_json:
            headers["Content-Type"] = "application/json"
            headers["Cache-Control"] = "no-cache"
            headers["Pragma"] = "no-cache"
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _request_once(
        self,
        method: str,
        url: str,
        *,
        referer: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        with_json: bool = False,
    ) -> tuple[Any, int]:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        headers = self._headers(referer=referer, with_json=with_json or body is not None)
        if not self.has_session():
            self.bootstrap_session()
        base = self._curl_base()  # may raise (curl missing) before any temp file exists
        config = curl_config(url, headers)
        cmd = [*base, "-K", config, "-X", method, "-w", "\n__HTTP__%{http_code}"]
        if body is not None:
            cmd.extend(["--data-raw", json.dumps(body)])
        proc = _run_curl(cmd, config=config)
        if proc.returncode != 0:
            raise AmulAPIError(f"curl failed: {proc.stderr.strip() or proc.stdout.strip()}", retryable=True)

        out = proc.stdout
        status = 200
        if "\n__HTTP__" in out:
            out, _, code = out.rpartition("\n__HTTP__")
            try:
                status = int(code.strip())
            except ValueError:
                status = 200
        raw = out.strip()
        return raw, status

    def _request(
        self,
        method: str,
        url: str,
        *,
        referer: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        with_json: bool = False,
    ) -> Any:
        last_error: AmulAPIError | None = None
        rebooted = False
        for attempt in range(1, self.max_retries + 1):
            self._limiter.wait()
            try:
                raw, status = self._request_once(method, url, referer=referer, params=params, body=body, with_json=with_json)
            except AmulAPIError as exc:
                last_error = exc
                if exc.retryable and attempt < self.max_retries:
                    time.sleep(retry_delay(attempt))
                    continue
                raise

            if status in (401, 403) or raw == "Unauthorized":
                if not self.cookie and not rebooted:
                    # Anonymous sessions expire; a fresh one is one homepage fetch away.
                    rebooted = True
                    self.bootstrap_session()
                    continue
                raise AmulAPIError(
                    "Unauthorized: Amul session expired",
                    status=status or 401,
                    body=raw,
                )
            if status == 406 or status >= 500:
                last_error = AmulAPIError(f"HTTP {status}", status=status, body=raw[:500], retryable=True)
                if attempt < self.max_retries:
                    log.warning("Amul HTTP %s, retry %s/%s", status, attempt, self.max_retries)
                    time.sleep(retry_delay(attempt))
                    continue
                raise last_error
            if status >= 400:
                raise AmulAPIError(f"HTTP {status}", status=status, body=raw[:500])

            stripped = raw.strip()
            if not stripped:
                return {}

            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                if "Unauthorized" in stripped:
                    raise AmulAPIError("Unauthorized: Amul session expired", status=401, body=stripped)
                if _looks_like_cloudflare(stripped):
                    raise AmulAPIError(
                        "Cloudflare challenged the request. Usually clears on its own; if it "
                        "persists, open shop.amul.com in a browser, Copy as cURL, then run "
                        "`amul-watch session import`",
                        status=403,
                        body=stripped[:500],
                    )
                # setPreferences returns plain text: "Updated successfully"
                if 200 <= status < 300:
                    return {"message": stripped}
                raise AmulAPIError("Non-JSON response from Amul", body=stripped[:500])

        if last_error:
            raise last_error
        raise AmulAPIError("Request failed")

    def _curl_text(self, url: str, *, referer: str) -> str:
        self._limiter.wait()
        headers = self._headers(referer=referer)
        base = self._curl_base()
        config = curl_config(url, headers)
        proc = _run_curl([*base, "-K", config], config=config)
        if proc.returncode != 0:
            raise AmulAPIError(f"curl failed: {proc.stderr.strip()}", retryable=True)
        return proc.stdout

    def refresh_session_tid(self) -> None:
        try:
            text = self._curl_text(f"{INFO_JS}?_v={int(time.time() * 1000)}", referer=f"{BASE_URL}/en/browse/protein")
        except Exception as exc:
            log.warning("info.js fetch failed: %s", exc)
            return
        match = re.search(r"session\s*=\s*(\{.*?\})\s*;", text, re.DOTALL)
        if not match:
            return
        try:
            session = json.loads(match.group(1))
        except json.JSONDecodeError:
            return
        tid = session.get("tid")
        if tid:
            self._session_tid = str(tid)
            os.environ["AMUL_SESSION_TID"] = self._session_tid

    def lookup_pincode(self, pincode: str) -> dict[str, str]:
        """Pincode delivery zone: record id (product API substore param) + region store alias."""
        params = {
            "limit": "50",
            "filters[0][field]": "pincode",
            "filters[0][value]": str(pincode),
            "filters[0][operator]": "regex",
            "cf_cache": "1h",
        }
        data = self._request("GET", PINCODE_API, referer=f"{BASE_URL}/", params=params)
        records = data.get("records") or []
        if not records:
            raise AmulAPIError(f"No substore for pincode {pincode}")
        rec = records[0]
        record_id = str(rec.get("_id") or "")
        substore = rec.get("substore")
        store_key: str | None = None
        if isinstance(substore, dict):
            store_key = str(substore.get("alias") or substore.get("name") or substore.get("_id") or "")
        elif isinstance(substore, str):
            store_key = substore
        if not record_id:
            raise AmulAPIError(f"Could not resolve pincode record for {pincode}")
        if not store_key:
            raise AmulAPIError(f"No store key for pincode {pincode}")
        return {
            "pincode": str(rec.get("pincode") or pincode),
            "store": store_key,
            "record_id": record_id,
        }

    def set_store_preference(self, store: str, *, referer: str | None = None) -> dict[str, Any]:
        """Switch Amul session region (up-ncr, haryana, karnataka, …)."""
        ref = referer or f"{BASE_URL}/en/cart"
        result = self._request(
            "PUT",
            SETTINGS_API,
            referer=ref,
            body={"data": {"store": store}},
            with_json=True,
        )
        log.info("setPreferences store=%s → %s", store, result.get("message", "ok"))
        return result

    def set_geolocation_preference(self, pincode: str, *, referer: str | None = None) -> dict[str, Any]:
        """Set delivery pincode on server session (browser also sets localStorage pincode/city)."""
        ref = referer or f"{BASE_URL}/en/cart"
        pin = str(pincode)
        geo = {
            "zip": pin,
            "zip_code": pin,
            "postal_code": pin,
            "pin_code": pin,
            "source": "browser",
            "time": int(time.time() * 1000),
        }
        result = self._request(
            "PUT",
            SETTINGS_API,
            referer=ref,
            body={"data": {"geolocation": geo}},
            with_json=True,
        )
        log.info("setPreferences geolocation pin=%s → %s", pin, result.get("message", "ok"))
        return result

    def activate_pincode_session(self, pincode: str, *, referer: str | None = None) -> dict[str, str]:
        """Match browser pin change: region store + geolocation pin (same region needs both)."""
        info = self.lookup_pincode(pincode)
        ref = referer or f"{BASE_URL}/en/cart"
        self._active_pin = None
        self.set_store_preference(info["store"], referer=ref)
        self.set_geolocation_preference(info["pincode"], referer=ref)
        self.refresh_session_tid()
        self._pin_store[str(pincode)] = info["store"]
        self._active_pin = self._wanted_pin = str(pincode)
        return info

    def use_pincode(self, pincode: str, store: str) -> None:
        """Point the session at this pincode's region before reading its stock.

        The product API answers for the session's selected region and ignores the
        substore parameter, so without this every pincode would report the stock of
        whichever pincode was selected last.
        """
        pin = str(pincode)
        self._pin_store[pin] = store
        self._wanted_pin = pin
        if self._active_pin == pin:
            return
        self._active_pin = None  # unknown until both calls succeed
        self.set_store_preference(store)
        self.set_geolocation_preference(pin)
        self._active_pin = pin

    def resolve_substore(self, pincode: str) -> tuple[str, str | None]:
        info = self.activate_pincode_session(pincode)
        return info["record_id"], info["store"]

    def get_product(self, alias: str, substore_id: str) -> dict[str, Any] | None:
        q = json.dumps({"alias": alias}, separators=(",", ":"))
        params = {"q": q, "limit": "1", "substore": substore_id, "v": "5"}
        referer = f"{BASE_URL}/en/product/{alias}"
        wanted = self._wanted_pin
        if self._active_pin is None and wanted in self._pin_store:
            self.use_pincode(wanted, self._pin_store[wanted])
        data = self._request("GET", PRODUCT_API, referer=referer, params=params)
        records = data.get("records") or data.get("data") or []
        if records:
            return records[0]
        if isinstance(data, dict) and data.get("alias"):
            return data
        return None

    def create_product_enquiry(
        self,
        *,
        product_name: str,
        email: str,
        contact: str,
        quantity: str = "1",
        message: str = "Please notify when in stock",
    ) -> dict[str, Any]:
        body = {
            "data": {
                "product": product_name,
                "email": email,
                "contact": contact,
                "quantity": str(quantity),
                "message": message,
            }
        }
        return self._request(
            "POST",
            ENQUIRIES_API,
            referer=f"{BASE_URL}/en/browse/protein",
            body=body,
            with_json=True,
        )


def _random_ga_id() -> str:
    """Shape of the analytics client id the site sends in the ms-ga header."""
    return f"{random.randint(10**9, 10**10 - 1)}.{int(time.time())}"
