"""Fault campaigns and the benchmark's scenario, timing and scoring helpers."""
from __future__ import annotations

import pytest

from operaitor.benchmark.economics import MW_ACETONE, PRICES, hourly_margin, inventory_value
from operaitor.benchmark.profit_campaign import (
    BY_ID, LIFT_AFTER_TRIP, LIFT_NO_TRIP, MIN_WINDOW, ONSET, SCENARIOS, TAIL, _window_profit, lift_schedule, window_for,
)
from operaitor.server.campaigns import CAMPAIGNS, FAULT_LIBRARY, build_campaign, random_campaign


# ----------------------------------------------------------------------------- campaigns
@pytest.mark.parametrize("name", sorted(CAMPAIGNS))
def test_every_preset_campaign_builds(name):
    label, items = build_campaign(name)
    assert label == name and items
    assert [it["at_min"] for it in items] == sorted(it["at_min"] for it in items)
    assert all(it["name"] in FAULT_LIBRARY and "kind" in it for it in items)


def test_random_campaigns_are_reproducible_and_respect_ranges():
    a, b, c = random_campaign(seed=4, n=6), random_campaign(seed=4, n=6), random_campaign(seed=5, n=6)
    assert a == b and a != c
    for it in a["items"]:
        for param, (lo, hi) in FAULT_LIBRARY[it["fault"]].get("vary", {}).items():
            assert lo <= it[param] <= hi
    name, items = build_campaign("random", seed=4, n=6)
    assert name == "random_s4" and len(items) == 6


def test_bad_campaigns_are_refused():
    with pytest.raises(ValueError, match="unknown campaign"):
        build_campaign("nope")
    with pytest.raises(ValueError, match="unknown fault"):
        build_campaign({"items": [{"at_min": 1, "fault": "meteor_strike"}]})
    name, items = build_campaign({"name": "mine", "items": [{"at_min": 9, "fault": "reflux_pump_trip"},
                                                             {"at_min": 2, "fault": "feed_pump_trip"}]})
    assert name == "mine" and [it["name"] for it in items] == ["feed_pump_trip", "reflux_pump_trip"]


# ----------------------------------------------------------------------------- benchmark scenarios
def test_the_benchmark_has_twenty_valid_scenarios():
    assert len(SCENARIOS) == 20 and len(BY_ID) == 20
    groups = {s["group"] for s in SCENARIOS}
    assert groups == {"single", "pair_indep", "pair_coupled", "triple_none", "triple_2plus1", "triple_all"}
    for s in SCENARIOS:
        assert all(f["fault"] in FAULT_LIBRARY and f["at_min"] >= ONSET for f in s["faults"])
        assert s["faults"][0]["at_min"] == ONSET


def test_lift_schedule_and_window():
    scn = BY_ID["D01"]                                           # faults at 10 and 13 min
    assert all(v["lift_min"] is None for v in lift_schedule(scn, None).values())      # probe: never lifted
    tripped = lift_schedule(scn, {"trip_min": 15.0})
    assert tripped[0]["lift_min"] == max(15 + LIFT_AFTER_TRIP, 10 + 10) and tripped[1]["lift_min"] == max(25.0, 23.0)
    calm = lift_schedule(scn, {"trip_min": None})
    assert [calm[i]["lift_min"] for i in (0, 1)] == [10 + LIFT_NO_TRIP, 13 + LIFT_NO_TRIP]
    assert window_for(calm) == MIN_WINDOW                         # short scenarios get the minimum window
    late = lift_schedule(scn, {"trip_min": 150.0})
    assert window_for(late) == 160.0 + TAIL


def _tick(t, margin, cstr=50.0, rb=34.5, dr=30.2):
    return {"t": t, "margin_usd_h": margin, "cstr_holdup_kmol": cstr, "reboiler_holdup_kmol": rb, "drum_holdup_kmol": dr}


def test_window_profit_margin_inventory_and_stop_penalty():
    ticks = [_tick(0.5 * (i + 1), 1200.0) for i in range(240)]              # 120 min at $1200/h
    run = {"ticks": ticks, "tripped": False, "trip_min": None}
    parts = _window_profit(run, 120.0, parts=True)
    assert parts["margin"] == pytest.approx(2400.0) and parts["inventory"] == 0 and parts["penalty"] == 0
    assert _window_profit(run, 60.0) == pytest.approx(1200.0)               # truncated to the window
    drained = dict(run, ticks=ticks[:-1] + [_tick(120.0, 1200.0, cstr=40.0)])   # 10 kmol of hold-up sold off
    assert _window_profit(drained, 120.0, parts=True)["inventory"] == pytest.approx(-10 * 60.10 / 1000 * PRICES.ipa_usd_t)
    stopped = dict(run, tripped=True, trip_min=30.0)
    assert _window_profit(stopped, 120.0, parts=True)["penalty"] == -PRICES.trip_penalty_usd
    assert _window_profit(stopped, 20.0, parts=True)["penalty"] == 0        # the stop is after this window


def test_hourly_margin_pays_only_for_on_spec_product():
    base = {"column.distillate_flow_kmol_h": 70.0, "column.distillate_x_acetone": 0.94,
            "column.bottoms_flow_kmol_h": 30.0, "column.bottoms_x_acetone": 0.04,
            "cstr.hydrogen_vent_kmol_h": 70.0, "feed_kmol_h": 100.0,
            "column.reboiler_duty_MW": 1.28, "cstr.heater_duty_MW": 1.51}
    on = hourly_margin(base)
    assert on["on_spec"] and on["acetone"] == pytest.approx(70 * 0.94 * MW_ACETONE / 1000 * PRICES.acetone_usd_t)
    assert on["margin"] == pytest.approx(sum(on[k] for k in ("acetone", "ipa_credit", "h2", "feed", "steam", "heating")))
    off = hourly_margin(dict(base, **{"column.distillate_x_acetone": 0.89}))
    assert not off["on_spec"] and off["acetone"] == 0 and off["on_spec_kmol_h"] == 0
    assert inventory_value(_tick(0, 0, 10, 10, 10)) == pytest.approx(30 * 60.10 / 1000 * PRICES.ipa_usd_t)
