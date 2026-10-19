"""The website build (.github/scripts/assemble_site.py): the live-demo button is filled in, or removed from the page."""
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = runpy.run_path(str(ROOT / ".github" / "scripts" / "assemble_site.py"))


def test_without_a_demo_address_the_button_is_not_in_the_page(tmp_path):
    SITE["assemble"](tmp_path / "out", "")
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "__DEMO_URL__" not in html and 'id="demo"' not in html and "Open the live demo" not in html
    assert 'class="btn primary" id="download"' in html          # Download is the main button
    assert (tmp_path / "out" / "dashboard" / "index.html").is_file() and (tmp_path / "out" / ".nojekyll").is_file()


def test_a_demo_address_fills_the_button(tmp_path):
    SITE["assemble"](tmp_path / "out", "https://demo.example.org")
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert '<a class="btn primary" id="demo" href="https://demo.example.org">Open the live demo</a>' in html
    assert "__DEMO_URL__" not in html and 'class="btn" id="download"' in html


def test_the_address_comes_from_demo_url_or_the_hugging_face_space():
    assert SITE["demo_url"](" https://x.test ", "a/b") == "https://x.test"
    assert SITE["demo_url"]("", "AISL-at-Imperial/OperAItor_Demo") == "https://aisl-at-imperial-operaitor-demo.hf.space"
    assert SITE["demo_url"]("", "") == ""


def test_the_published_workflow_uses_the_script():
    wf = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "assemble_site.py _site" in wf and "__DEMO_URL__" not in wf
