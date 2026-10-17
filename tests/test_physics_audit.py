"""Physics audit of the plant model: conservation during transients, numerical convergence, thermodynamic
sanity and the direction of fault responses on the closed-loop plant.

These complement test_plant_model.py (design point, residuals) and test_physics_consistency.py (safety logic,
setpoints). The known simplifications they document are listed in docs/plant-model.md (Model scope).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import numpy as np
import pytest
from scipy.optimize import brentq

from operaitor.plant.plant import IntegratedPlantDAE
from operaitor.plant.thermo import ThermoSurrogate

P = IntegratedPlantDAE()
U0 = P.default_inputs()
Y0 = P.nominal_state(U0)
COL = P.column
CONDENSER_KMOL_PER_BAR = 20.0  # condenser vapour capacitance (distillation.py)


def _inventory(y):
    """Organic moles (acetone + IPA) and acetone moles held in the plant."""
    c, k = P.split_state(y)
    trays = [k[i * COL.nper_tray: i * COL.nper_tray + 2] for i in range(COL.cfg.n_trays)]
    rb = k[COL.reboiler_offset: COL.reboiler_offset + 2]
    dr = k[COL.drum_offset: COL.drum_offset + 2]
    pc, y1 = k[COL.pressure_offset], k[5]
    organics = c[0] + sum(t[0] for t in trays) + rb[0] + dr[0] + CONDENSER_KMOL_PER_BAR * pc
    acetone = c[1] + sum(t[1] for t in trays) + rb[1] + dr[1] + CONDENSER_KMOL_PER_BAR * pc * y1
    return organics, acetone


def _net_in(y, u):
    """Net organic and acetone flows into the plant (kmol/h): feed and reaction in, products and leaks out."""
    c, k = P.split_state(y)
    xd, xr, xb = k[COL.drum_offset + 4], k[COL.reboiler_offset + 4], c[5]
    out = u.distillate_flow + u.bottoms_flow + u.reboiler_liquid_leak_kmol_h + u.drum_liquid_leak_kmol_h + u.cstr_liquid_leak_kmol_h
    acetone_out = ((u.distillate_flow + u.drum_liquid_leak_kmol_h) * xd + (u.bottoms_flow + u.reboiler_liquid_leak_kmol_h) * xr
                   + u.cstr_liquid_leak_kmol_h * xb)
    return u.fresh_ipa_feed - out, c[7] - acetone_out


def _run(u, minutes, dt_min=0.1):
    y, org_in, ace_in = Y0.copy(), 0.0, 0.0
    for _ in range(int(round(minutes / dt_min))):
        y = P.adaptive_step(y, dt_min / 60.0, u).x
        f_org, f_ace = _net_in(y, u)  # backward Euler: flows at the end of the step
        org_in += f_org * dt_min / 60.0
        ace_in += f_ace * dt_min / 60.0
    return y, org_in, ace_in


TRANSIENTS = {
    "feed +10 %": dict(fresh_ipa_feed=U0.fresh_ipa_feed * 1.10),
    "reboiler leak": dict(reboiler_liquid_leak_kmol_h=5.0),
    "reflux -25 %": dict(reflux_flow=U0.reflux_flow * 0.75),
    "reactor leak": dict(cstr_liquid_leak_kmol_h=4.0),
}


@pytest.mark.parametrize("name", list(TRANSIENTS))
def test_mass_is_conserved_through_transients(name):
    """Total organics close to round-off; acetone closes up to the documented condenser simplification."""
    y, org_in, ace_in = _run(replace(U0, **TRANSIENTS[name]), 10.0)
    org0, ace0 = _inventory(Y0)
    org1, ace1 = _inventory(y)
    assert abs((org1 - org0) - org_in) < 1e-8
    # The lumped condenser vapour has no composition state: its acetone follows the top vapour's composition y1, so
    # the acetone balance is off by exactly 20 kmol/bar * Pc * (change in y1) (docs/plant-model.md, Model scope).
    k0, k1 = P.split_state(Y0)[1], P.split_state(y)[1]
    condenser_term = CONDENSER_KMOL_PER_BAR * k1[COL.pressure_offset] * (k1[5] - k0[5])
    assert abs((ace1 - ace0) - ace_in - condenser_term) < 1e-6
    assert abs(condenser_term) < 0.01 * ace0  # bounded: under 1 % of the acetone held


def test_results_do_not_depend_on_the_time_step():
    u = replace(U0, **TRANSIENTS["feed +10 %"])
    coarse = P.measurements(_run(u, 10.0, 0.1)[0])
    fine = P.measurements(_run(u, 10.0, 0.025)[0])
    tol = {"cstr_temperature_K": 0.01, "top_temperature_K": 0.01, "cstr_pressure_bar": 1e-3, "cstr_x_acetone": 1e-3,
           "distillate_x_acetone": 1e-4, "reboiler_holdup_kmol": 0.01}
    for key, t in tol.items():
        assert abs(coarse[key] - fine[key]) < t, (key, coarse[key], fine[key])


def test_thermodynamics_match_real_acetone_and_ipa():
    th = ThermoSurrogate()
    boil = [brentq(lambda T, i=i: th.psat(T)[i] - 1.01325, 250, 450) for i in (0, 1)]
    assert boil[0] == pytest.approx(329.2, abs=1.0) and boil[1] == pytest.approx(355.4, abs=1.0)  # literature
    alpha = [th.psat(T)[0] / th.psat(T)[1] for T in (330, 355)]
    assert 2.0 < alpha[1] < alpha[0] < 3.2  # acetone/IPA relative volatility, falling with temperature
    bubble = [th.bubble_temperature(x, 1.01325) for x in (0.0, 0.5, 0.94, 1.0)]
    assert bubble == sorted(bubble, reverse=True) and bubble[0] == pytest.approx(boil[1], abs=0.1)
    # Clausius-Clapeyron: the vapour-pressure slope matches the heats of vaporisation used in the energy balances
    for i, dh in enumerate((30_000, 40_000)):
        T, dT = 340.0, 0.01
        slope = (np.log(th.psat(T + dT)[i]) - np.log(th.psat(T - dT)[i])) / (2 * dT)
        assert slope * 8.314 * T * T == pytest.approx(dh, rel=0.01)


def _fault(fault: str, minutes: float = 25.0):
    async def run():
        from operaitor.server.campaigns import FAULT_LIBRARY
        from operaitor.server.plant_service import PlantService
        s = PlantService()
        await s.load({"scenario": "nominal", "horizon_min": minutes + 5, "stop_on_trip": False})
        await s.step(2.0)
        view = lambda: {**s.env.plant.measurements(s.env.y), **s.env.plant.operating_summary(s.env.y, s.env.last_actual)}  # noqa: E731
        before = view()
        await s._inject({k: v for k, v in FAULT_LIBRARY[fault].items() if k != "vary"})
        trip = None
        for _ in range(int(minutes / 0.5)):
            await s.step(0.5)
            if trip is None and s.env.last_safety_result.plant_trip_latched:
                trip = (60 * s.env.t_h, tuple(s.env.last_safety_result.latched_causes))
        return before, view(), trip
    return asyncio.run(run())


@pytest.mark.parametrize("fault, expected", [
    ("reflux_pump_trip", {"column.reflux_flow_kmol_h": "down", "top_temperature_K": "up", "distillate_x_acetone": "down"}),
    ("condenser_cooling_loss", {"column_pressure_bar": "up"}),
    ("catalyst_activity_loss", {"cstr.reaction_rate_kmol_h": "down", "cstr_x_acetone": "down"}),
    ("reactor_heater_stuck_high", {"cstr_temperature_K": "up", "cstr.reaction_rate_kmol_h": "up"}),
    ("cstr_liquid_leak", {"cstr_holdup_kmol": "down"}),
])
def test_faults_move_the_plant_the_physical_way(fault, expected):
    before, after, trip = _fault(fault)
    for key, direction in expected.items():
        moved = after[key] - before[key]
        assert (moved > 0) if direction == "up" else (moved < 0), (fault, key, before[key], after[key])
    if fault == "reflux_pump_trip":  # no reflux: the trays drain and the dry-out SIF trips the plant
        assert trip is not None and "COL_L_LL" in trip[1]
