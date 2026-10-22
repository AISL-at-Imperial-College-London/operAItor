"""The v1.2 PI tuning: the PV filter, the named tuning sets, and no loss of fault tolerance against v1.1."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from operaitor.plant.control import PIController, RegulatoryControlSystem as R


def test_the_pv_filter_is_first_order_and_off_by_default():
    raw = PIController(kp=1.0, ti_h=0.0, bias=0.0, u_min=-10, u_max=10)
    assert raw.output(1.0, 0.0, 1 / 60) == 1.0              # no filter: the raw error
    f = PIController(kp=1.0, ti_h=0.0, bias=0.0, u_min=-10, u_max=10, filter_h=2 / 60)
    f.reset()
    f.output(0.0, 0.0, 1 / 60)                               # the filter starts at the first measurement
    out = f.output(0.0, 3.0, 1 / 60)                         # PV steps 0 -> 3: the filter moves by a = dt/(tau + dt) = 1/3
    assert f.pv_filtered == pytest.approx(1.0) and out == pytest.approx(-1.0)
    f.reset()
    assert f.pv_filtered is None


def test_v1_1_is_kept_exactly_and_selectable():
    """PLANT_TUNING=v1.1 reproduces every run up to release 1.1 (the ESCAPE paper's results)."""
    assert R.TUNINGS["v1.1"] == {
        "cstr_level": (-6.0, 2.0, 0.50, 0.0), "cstr_temp": (+0.030, 0.12, 0.30, 0.0),
        "cstr_pressure": (-0.10, 0.30, 0.015, 0.0), "reboiler_level": (-6.0, 2.0, 0.35, 0.0),
        "drum_level": (-12.0, 2.0, 0.30, 0.0), "column_pressure": (-4.5, 0.20, 0.002, 0.0),
        "top_temp": (-7.0, 0.15, 0.30, 0.0), "bottom_temp": (+0.0325, 0.15, 0.30, 0.0)}
    code = "from operaitor.plant.control import RegulatoryControlSystem as R; print(R.TUNING is R.TUNINGS['v1.1'])"
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PLANT_TUNING": "v1.1"},
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "True"
    assert R.TUNING is R.TUNINGS[os.environ.get("PLANT_TUNING", "v1.2")]


# Probe-phase trip times with v1.1 [min] (faults never lifted, nobody acts): v1.2 must not trip earlier.
V11_TRIPS = {"S06": 177.8, "S08": 23.2, "D01": 49.9, "D04": 46.7}


@pytest.mark.parametrize("sid", sorted(V11_TRIPS))
def test_v1_2_does_not_trip_earlier_than_v1_1(sid, monkeypatch):
    from operaitor.benchmark.profit_campaign import BY_ID, PROBE_WINDOW, lift_schedule, run_scenario
    monkeypatch.setattr(R, "TUNING", R.TUNINGS["v1.2"])
    r = asyncio.run(run_scenario(BY_ID[sid], "probe", lift_schedule(BY_ID[sid], None), PROBE_WINDOW))
    assert r["trip_min"] is None or r["trip_min"] >= V11_TRIPS[sid] - 1.0, (sid, r["trip_min"])
