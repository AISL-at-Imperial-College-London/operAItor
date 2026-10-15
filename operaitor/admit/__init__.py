"""ADMIT integration (optional: ``pip install "admit-bench @ git+https://github.com/Refiant-Inc/admit-bench.git"``).

* ``AcetoneWorld`` (world.py): ADMIT's T4 consequence world, projecting on the real simulator.
* ``AdmitGate`` (gate.py): ADMIT's hard gates as the validator of the ``llm_admit`` agents.
* ``cartridge/``: the acetone plant as an ADMIT cartridge (``operaitor admit build`` writes
  it, ``operaitor admit run`` evaluates agents on it with admitbench).
"""
from __future__ import annotations


def available() -> tuple[bool, str]:
    try:
        import admitbench  # noqa: F401
    except ImportError:
        return False, 'admit-bench is not installed (pip install "admit-bench @ git+https://github.com/Refiant-Inc/admit-bench.git")'
    return True, "admit-bench " + getattr(__import__("admitbench"), "__version__", "?")
