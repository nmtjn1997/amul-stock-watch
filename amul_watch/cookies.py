from __future__ import annotations

import re


def extract_cookie(raw: str) -> str:
    text = raw.strip()
    patterns = [
        r"-b\s+\$?'([^']+)'\s",
        r'-b\s+\$?"([^"]+)"\s',
        r"(?i)-b\s+\$?'([^']+)'",
        r'(?i)-b\s+\$?"([^"]+)"',
        r"(?i)-H\s+\$?'cookie:\s*([^']+)'",
        r'(?i)-H\s+\$?"cookie:\s*([^"]+)"',
        r"(?i)^cookie:\s*([^\n]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    if "jsessionid=" in text and ";" in text:
        return text.strip()
    if re.search(r"(?i)\bcurl\b", text):
        raise SystemExit("No cookie found in that curl: use Copy as cURL on a shop.amul.com request")
    return text


def extract_ms_ga(raw: str) -> str | None:
    m = re.search(r"(?i)ms-ga:\s*([^\s'\"]+)", raw)
    return m.group(1).strip() if m else None
