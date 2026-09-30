"""Assemble the GitHub Pages site in _site/: the landing page, the screenshots, and the
real web UI wired to an in-browser demo API (site/demo/demo-api.js)."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_site"

shutil.rmtree(OUT, ignore_errors=True)
shutil.copytree(ROOT / "site", OUT)
shutil.copytree(ROOT / "docs" / "images", OUT / "images")

ui = (ROOT / "amul_watch" / "web" / "index.html").read_text(encoding="utf-8")
marker = "<head>"
assert marker in ui
ui = ui.replace(
    marker,
    marker + '\n<script src="demo-data.js"></script>\n<script src="demo-api.js"></script>',
    1,
)
ui = ui.replace("<title>Amul Stock Watch</title>", "<title>Amul Stock Watch: live demo</title>", 1)
(OUT / "demo" / "index.html").write_text(ui, encoding="utf-8")
(OUT / ".nojekyll").write_text("", encoding="utf-8")
print(f"built {OUT}")
