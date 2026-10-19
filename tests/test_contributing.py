"""The ways to contribute are reachable: issue forms parse, and the README, CONTRIBUTING and site link to them."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / ".github" / "ISSUE_TEMPLATE"


def test_every_issue_form_parses_with_a_name_and_a_body():
    for f in FORMS.glob("*.yml"):
        form = yaml.safe_load(f.read_text(encoding="utf-8"))
        if f.name == "config.yml":
            continue
        assert form["name"] and form["description"] and form["body"], f.name
        ids = [b["id"] for b in form["body"] if "id" in b]
        assert len(ids) == len(set(ids)), f"{f.name}: duplicate field ids"


def test_plant_models_can_be_proposed_from_the_readme_contributing_and_site():
    form = yaml.safe_load((FORMS / "plant.yml").read_text(encoding="utf-8"))
    assert {b["id"] for b in form["body"] if "id" in b} >= {"process", "form", "faults", "license"}
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Contribute a plant model" in readme and "template=plant.yml" in readme
    assert "template=plant.yml" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "#contribute-a-plant-model" in (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    for link in ("operaitor/plant/plant.py", "operaitor/plant/control.py", "operaitor/plant/alarms.py",
                 "operaitor/plant/safety.py", "operaitor/data/knowledge_graph", "tests/test_physics_audit.py"):
        assert f"({link})" in readme and (ROOT / link).exists(), link   # what integration involves, linked
