"""The P&ID document (docs/pid.md) and its cause & effect matrix match the simulator's code."""
from __future__ import annotations

from pathlib import Path

from operaitor.plant import cause_effect as CE
from operaitor.plant.plant import IntegratedPlantDAE
from operaitor.plant.safety import SafetyConfig, SafetySystem

DOC = Path(__file__).resolve().parents[1] / "docs" / "pid.md"


def test_the_document_matches_the_code():
    """Regenerate with `python -m operaitor.plant.cause_effect --write` after changing the plant."""
    assert DOC.read_text(encoding="utf-8").replace("\r\n", "\n") == CE.document()


def test_every_sif_is_in_the_matrix_and_acts():
    rows = {r["sif"]: r for r in CE.matrix()}
    assert set(rows) == set(SafetySystem.CAUSES)
    for sif, r in rows.items():
        assert any(r["effects"].values()), f"{sif} changes no final element"
    trips = {s for s, r in rows.items() if r["plant_trip"]}
    assert trips == {"R_P_HHH", "RB_L_HH", "DR_L_LL", "DR_L_HH", "COL_P_HH", "COL_L_LL"}


def test_each_cause_trips_at_its_documented_limit():
    cfg = SafetyConfig()
    plant = IntegratedPlantDAE()
    nominal = plant.default_inputs()
    base = plant.measurements(plant.nominal_state(nominal))
    for c in CE.CAUSES:
        limit = getattr(cfg, c.limit_attr)
        for value, expect in ((limit, True), (limit * (0.99 if c.direction == "HIGH" else 1.01), False)):
            sis = SafetySystem(cfg)
            res = sis.apply({**base, c.measurement: value}, nominal, nominal)
            assert (c.sif in res.active_causes) is expect, (c.sif, value)


def test_the_drawing_shows_every_tag_in_the_tables():
    svg = (DOC.parents[1] / "frontend" / "public" / "static" / "pid.svg").read_text(encoding="utf-8")
    for tag, element, _ in CE.LOOP_TAGS.values():
        assert f">{tag.split('-')[0]}<" in svg and f">{tag.split('-')[1]}<" in svg, tag   # bubble: letters / number
        assert f">{element}<" in svg, element
    for sif in SafetySystem.CAUSES:
        assert sif.split("_")[0] in svg and sif.rsplit("_", 1)[1] in svg, sif
    for tag in ("R-101", "T-201", "E-201", "E-202", "V-201", "PSV-101", "BDV-101", "XV-102",
                "P-101A/B", "P-102A/B", "P-201A/B", "P-202A/B", "P-203A/B"):
        assert tag in svg, tag


def test_the_console_drawing_is_wired_for_the_live_view():
    """The P&ID view lights the SIS box of a latched SIF and writes each controller's value next to it."""
    import re
    from operaitor.plant.control import RegulatoryControlSystem
    svg = (DOC.parents[1] / "frontend" / "public" / "static" / "pid.svg").read_text(encoding="utf-8")
    covered = {c for group in re.findall(r'data-sif="([^"]+)"', svg) for c in group.split()}
    assert covered == set(SafetySystem.CAUSES)
    assert set(re.findall(r'data-pv="([^"]+)"', svg)) == set(RegulatoryControlSystem.LOOP_SPECS)


def test_the_console_gets_the_matrix():
    from operaitor.server.plant_service import cause_effect
    ce = cause_effect()
    assert [r["sif"] for r in ce["rows"]] == [c.sif for c in CE.CAUSES] and len(ce["rows"]) == len(SafetySystem.CAUSES)
    assert [name for name, _ in ce["effects"]] == [name for name, _, _ in CE.EFFECTS]


def test_the_drawings_are_generated_and_carry_every_final_element():
    """docs/pid/build_pid.py draws the P&ID for the console (SVG) and for editing (draw.io); both must be current."""
    import runpy
    gen = runpy.run_path(str(DOC.parent / "pid" / "build_pid.py"))
    unix = lambda s: s.replace(chr(13) + chr(10), chr(10))  # noqa: E731 - files may be checked out with CRLF
    svg = unix((DOC.parents[1] / "frontend" / "public" / "static" / "pid.svg").read_text(encoding="utf-8"))
    drawio = unix((DOC.parent / "pid" / "pid.drawio").read_text(encoding="utf-8"))
    assert svg == gen["svg"]() and drawio == gen["drawio"](), "run python docs/pid/build_pid.py"
    for _, _, where in CE.EFFECTS:
        for tag in (w.split(" ")[0] for w in where.split(", ")):
            assert f">{tag}<" in svg and tag in drawio, tag
    for c in CE.CAUSES:                                  # one interlock diamond per SIF, numbered as in the matrix
        assert f">{CE.INTERLOCK[c.sif]}<" in svg and f'value="{CE.INTERLOCK[c.sif]}"' in drawio
    assert drawio.count("actuator=solenoid;fillColor=#000000") == 3      # BDV-101, XV-102 (normally closed), legend
