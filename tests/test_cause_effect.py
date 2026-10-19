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
    svg = (DOC.parent / "pid.svg").read_text(encoding="utf-8")
    for tag, element, _ in CE.LOOP_TAGS.values():
        assert f">{tag.split('-')[0]}<" in svg and f">{tag.split('-')[1]}<" in svg, tag   # bubble: letters / number
        assert f">{element}<" in svg, element
    for sif in SafetySystem.CAUSES:
        assert sif.split("_")[0] in svg and sif.rsplit("_", 1)[1] in svg, sif
    for tag in ("R-101", "T-201", "E-201", "E-202", "V-201", "PSV-101", "BDV-101", "HV-102",
                "P-101A/B", "P-102A/B", "P-201A/B", "P-202A/B", "P-203A/B"):
        assert tag in svg, tag
