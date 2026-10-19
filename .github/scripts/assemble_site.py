"""Assemble the project website for GitHub Pages: python .github/scripts/assemble_site.py <out_dir>

Copies site/ and the benchmark dashboard, and fills in the "Open the live demo" button from the repository
variables DEMO_URL (any address) or HF_SPACE (owner/space on Hugging Face). Without either, the button is removed
from the page itself, not only hidden by the page's script, so no visitor, crawler or link checker ever meets the
unfilled placeholder; Download becomes the main button.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "__DEMO_URL__"
DEMO_BUTTON = re.compile(r'\n[ \t]*<a [^>]*id="demo"[^>]*>.*?</a>')
DOWNLOAD = 'class="btn" id="download"'


def demo_url(demo: str = "", hf_space: str = "") -> str:
    if demo.strip():
        return demo.strip()
    if hf_space.strip():   # owner/My_Space -> https://owner-my-space.hf.space
        return "https://" + re.sub(r"[/_.]", "-", hf_space.strip().lower()) + ".hf.space"
    return ""


def fill_demo(html: str, url: str) -> str:
    if PLACEHOLDER not in html or not DEMO_BUTTON.search(html) or DOWNLOAD not in html:
        raise SystemExit("site/index.html: the demo or download button changed; update .github/scripts/assemble_site.py")
    if url:
        return html.replace(PLACEHOLDER, url)
    return DEMO_BUTTON.sub("", html, count=1).replace(DOWNLOAD, 'class="btn primary" id="download"', 1)


def assemble(out: Path, url: str) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    (out / "dashboard").mkdir()
    shutil.copy(ROOT / "benchmark_results" / "fault_campaign_dashboard.html", out / "dashboard" / "index.html")
    page = out / "index.html"
    page.write_text(fill_demo(page.read_text(encoding="utf-8"), url), encoding="utf-8")
    (out / ".nojekyll").touch()


if __name__ == "__main__":
    url = demo_url(os.environ.get("DEMO_URL", ""), os.environ.get("HF_SPACE", ""))
    assemble(Path(sys.argv[1]), url)
    print(f"site assembled in {sys.argv[1]}; live demo: {url or 'none (button removed)'}")
