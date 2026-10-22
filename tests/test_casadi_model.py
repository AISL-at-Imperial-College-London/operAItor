"""The CasADi solver (operaitor/plant/casadi_model.py) solves the same plant as the SciPy reference.

The residuals are mirrored term by term: they must agree to rounding on normal, faulted and extreme states. A
physics change made in one place only fails here. Steps and a whole episode (a reflux pump trip) must agree too.
"""
from __future__ import annotations

from dataclasses import replace
import pickle

import numpy as np
import pytest

from operaitor.plant import EpisodeRunner, IntegratedPlantDAE, nominal_episode
from operaitor.plant.equipment import PumpFault
from operaitor.plant.plant import default_solver
from operaitor.server.plant_service import PlantService

pytest.importorskip("casadi")

PLANT = IntegratedPlantDAE(solver="scipy")
U0 = PLANT.default_inputs()
Y0 = PLANT.nominal_state(U0)
CAS = PLANT.casadi()

INPUTS = {
    "nominal": U0,
    "no reflux": replace(U0, reflux_flow=0.0),
    "blowdown + emergency cooling": replace(U0, cstr_bdv_valve=0.7, cstr_emergency_cooling_factor=1.0, cstr_vent_valve=1.3),
    "cooling loss, leaks, steam high": replace(U0, condenser_cooling_effectiveness=0.3, steam_factor=1.4,
                                               cstr_liquid_leak_kmol_h=5.0, drum_liquid_leak_kmol_h=-1.0,
                                               reboiler_liquid_leak_kmol_h=3.0, steam_pressure_bar=4.0),
    "catalyst loss, cold feed": replace(U0, catalyst_activity=0.4, fresh_feed_temperature_K=280.0, fresh_ipa_feed=130.0),
}


@pytest.mark.parametrize("name", INPUTS)
def test_the_residuals_are_the_same(name):
    u = INPUTS[name]
    rng = np.random.default_rng(7)
    states = [Y0] + [Y0 * (1 + s * rng.standard_normal(Y0.size)) for s in (0.01, 0.05, 0.2)]
    high = Y0.copy(); high[PLANT.cstr.P] = 4.8          # the PSV open
    empty = Y0.copy(); empty[PLANT.column_offset] = 1e-4  # a dry top tray
    for y in states + [high, empty]:
        yd = rng.standard_normal(y.size) * PLANT.differential_mask
        a = PLANT.scaled_residual(0.0, y, yd, u)
        b = CAS.scaled_residual(y, yd, u)
        assert np.all(np.abs(a - b) <= 1e-12 * (1 + np.abs(a))), np.max(np.abs(a - b))


def test_a_step_lands_on_the_same_state():
    u = INPUTS["no reflux"]
    y_s, y_c = Y0.copy(), Y0.copy()
    cas = IntegratedPlantDAE(solver="casadi")
    for _ in range(20):                                    # 2 minutes of a reflux loss, 0.1-min steps
        a = PLANT.adaptive_step(y_s, 0.1 / 60, u)
        b = cas.adaptive_step(y_c, 0.1 / 60, u)
        assert a.success and b.success
        y_s, y_c = a.x, b.x
    assert np.max(np.abs(y_s - y_c) / (1 + np.abs(y_s))) < 1e-8


def test_a_reflux_pump_trip_trips_the_plant_at_the_same_time():
    """Recorded with the SciPy solver: the plant trips on COL_L_LL (dry trays) 12.0 min after the start."""
    env = EpisodeRunner(IntegratedPlantDAE(solver="casadi"), nominal_episode(30), timing=PlantService.TIMING)
    env.spec.pump_faults.faults.append(PumpFault("P-201A", 5 / 60))
    while not env.done:
        env.step()
    assert env.last_safety_result.plant_trip_latched
    assert "COL_L_LL" in env.last_safety_result.latched_causes
    assert abs(60 * env.t_h - 12.0) < 0.05


def test_a_step_newton_cannot_take_is_halved_not_failed():
    """When Newton fails (here: allowed only 2 iterations), the step is split, and the last resort is SciPy's."""
    from operaitor.plant.casadi_model import CasadiPlant
    p = IntegratedPlantDAE(solver="casadi")
    p._casadi = CasadiPlant(p, max_iter=2)
    sol = p.adaptive_step(Y0, 5 / 60, INPUTS["no reflux"])
    assert sol.success and sol.nsub > 1 and np.all(np.isfinite(sol.x))


def test_choosing_the_solver(monkeypatch):
    monkeypatch.delenv("PLANT_SOLVER", raising=False)
    assert default_solver() == "casadi" and IntegratedPlantDAE().solver == "casadi"
    monkeypatch.setenv("PLANT_SOLVER", "scipy")
    assert IntegratedPlantDAE().solver == "scipy"
    with pytest.raises(ValueError):
        IntegratedPlantDAE(solver="fortran")


def test_a_plant_with_its_casadi_model_can_be_pickled():
    """Process pools pickle plants: the compiled functions are rebuilt on the other side."""
    p = IntegratedPlantDAE(solver="casadi")
    p.adaptive_step(Y0, 0.1 / 60, U0)
    q = pickle.loads(pickle.dumps(p))
    assert q._casadi is None and q.adaptive_step(Y0, 0.1 / 60, U0).success
