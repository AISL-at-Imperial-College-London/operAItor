"""Fault detection and diagnosis (FDD) for the integrated CSTR/column plant."""
from .pca import PCAMonitor, PCAScore, observation_features, record_features, matrix_from_feature_rows
from .diagnosis import rule_checks, diagnose, VARIABLE_KNOWLEDGE

from pathlib import Path

DEFAULT_MODEL_PATH = str(Path(__file__).resolve().parent.parent / "data" / "pca_model.json")

__all__ = [
    "PCAMonitor", "PCAScore", "observation_features", "record_features",
    "matrix_from_feature_rows", "rule_checks", "diagnose", "VARIABLE_KNOWLEDGE",
    "DEFAULT_MODEL_PATH",
]
