"""PCA fault detection: T², SPE, limits, contributions and persistence of the model."""
from __future__ import annotations

import numpy as np
import pytest

from operaitor.fdd.pca import PCAMonitor, matrix_from_feature_rows, observation_features, record_features

VARS = [f"v{i}" for i in range(6)]


def _normal_data(n: int = 800, seed: int = 1) -> np.ndarray:
    """Six correlated variables driven by two latent factors plus noise."""
    rng = np.random.default_rng(seed)
    latent = rng.normal(size=(n, 2))
    mix = np.array([[1.0, 0.8, -0.5, 0.0, 0.3, 1.2], [0.0, 0.5, 1.0, 1.0, -0.7, 0.2]])
    return 10 + latent @ mix + 0.05 * rng.normal(size=(n, 6))


def test_fit_finds_the_latent_structure_and_normal_data_stays_inside():
    X = _normal_data()
    pca = PCAMonitor.fit(X, VARS, cpv=0.95)
    assert pca.meta["n_components"] == 2 and pca.meta["explained_variance"] > 0.95
    test = _normal_data(400, seed=2)
    flags = [pca.score_vector(x).violation for x in test]
    assert np.mean(flags) < 0.05                                   # about the 1 % false-alarm design rate


def test_faults_raise_t2_or_spe_and_contributions_point_at_the_variable():
    pca = PCAMonitor.fit(_normal_data(), VARS, cpv=0.95)
    x = _normal_data(1, seed=3)[0].copy()
    x[3] += 2.0                                                     # break the correlation on v3 only
    s = pca.score_vector(x)
    assert s.spe_violation
    assert s.top_contributors(1)[0]["variable"] == "v3"
    assert sum(s.t2_contrib.values()) == pytest.approx(s.t2)        # complete decomposition sums to T²
    assert sum(s.spe_contrib.values()) == pytest.approx(s.spe)
    d = s.to_dict(3)
    assert d["spe_violation"] is True and len(d["top_contributors"]) == 3


def test_limit_modes_and_round_trip(tmp_path):
    X = _normal_data()
    by = {m: PCAMonitor.fit(X, VARS, cpv=0.95, limits=m) for m in ("theory", "empirical", "max")}
    assert by["max"].t2_limit == max(by["theory"].t2_limit, by["empirical"].t2_limit)
    assert by["max"].spe_limit == max(by["theory"].spe_limit, by["empirical"].spe_limit)
    path = tmp_path / "pca.json"
    by["max"].save(path)
    again = PCAMonitor.load(path)
    x = X[5]
    assert again.score_vector(x).t2 == pytest.approx(by["max"].score_vector(x).t2)
    assert again.score({v: x[i] for i, v in enumerate(VARS)}).spe == pytest.approx(by["max"].score_vector(x).spe)


def test_bad_inputs_are_rejected():
    with pytest.raises(ValueError, match="n_samples"):
        PCAMonitor.fit(np.zeros((10, 3)), VARS)
    with pytest.raises(ValueError, match="more samples"):
        PCAMonitor.fit(np.zeros((5, 6)), VARS)
    with pytest.raises(ValueError, match="limits"):
        PCAMonitor.fit(_normal_data(), VARS, limits="nonsense")
    pca = PCAMonitor.fit(_normal_data(), VARS)
    with pytest.raises(KeyError, match="Missing PCA features"):
        pca.score({"v0": 1.0})


def test_constant_variables_do_not_blow_up():
    X = _normal_data()
    X[:, 4] = 7.0                                                   # a variable that never moves
    s = PCAMonitor.fit(X, VARS).score_vector(X[0])
    assert np.isfinite(s.t2) and np.isfinite(s.spe)


def test_features_from_observations_and_records():
    obs = {"measurements": {"cstr_pressure_bar": 3.0, "pump_FEED_failed": 0.0},
           "controllers": {"R_PIC": {"last_output": 0.5}}}
    f = observation_features(obs)
    assert f["meas.cstr_pressure_bar"] == 3.0 and f["op.R_PIC"] == 0.5
    assert record_features({"meas.cstr_pressure_bar": "3.0", "loop.R_PIC.op": "0.5", "other": 1}) == \
        {"meas.cstr_pressure_bar": 3.0, "op.R_PIC": 0.5}
    X, names = matrix_from_feature_rows([{"a": 1.0, "b": 2.0}, {"a": 3.0, "b": 4.0, "c": 5.0}])
    assert names == ["a", "b"] and X.tolist() == [[1.0, 2.0], [3.0, 4.0]]


def test_the_shipped_model_matches_the_plant_observation():
    """The model used by the console loads and scores a real observation of the nominal plant as normal."""
    from operaitor.fdd import DEFAULT_MODEL_PATH
    from operaitor.plant import EpisodeRunner, IntegratedPlantDAE, nominal_episode
    pca = PCAMonitor.load(DEFAULT_MODEL_PATH)
    env = EpisodeRunner(IntegratedPlantDAE(), nominal_episode(duration_min=5))
    s = pca.score_observation(env.observation())
    assert not s.violation
