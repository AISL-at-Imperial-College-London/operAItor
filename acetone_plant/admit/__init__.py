"""ADMIT integration (optional extra ``acetone-plant[admit]``).

* ``AcetoneWorld`` (world.py): ADMIT's T4 consequence world, projecting on the real simulator.
* ``AdmitGate`` (gate.py): ADMIT's hard gates as the validator of the ``llm_admit`` agents.
* ``cartridge/``: the acetone plant as an ADMIT cartridge (``acetone-plant admit build`` writes
  it, ``acetone-plant admit run`` evaluates agents on it with admitbench).
"""
from __future__ import annotations


def available() -> tuple[bool, str]:
    try:
        import admitbench  # noqa: F401
    except ImportError:
        return False, 'admit-bench is not installed (pip install "acetone-plant[admit]")'
    return True, "admit-bench " + getattr(__import__("admitbench"), "__version__", "?")
