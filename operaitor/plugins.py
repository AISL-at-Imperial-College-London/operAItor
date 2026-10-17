"""Python agents for custom pipelines: your own fault detectors and your own validator.

A custom pipeline (agents/pipeline.py) may carry Python code, written in the console. The code is a
module that defines any of:

    DETECTORS = [...]   fault detectors, run next to PCA and the rule checks on every observation. Each is
                        an object with ``async def detect(self, observation) -> dict`` (or a plain function
                        ``detect(observation)``), returning ``{"alarm": bool, "detail": str}`` or a bool.
                        An alarm triggers the agents like a PCA fault does.
    VALIDATOR = ...     checks each proposed action instead of the digital twin / ADMIT / LLM judge (choose
                        "Your Python validator" in the pipeline settings). ``async def validate(self, proposal)
                        -> {"accept": bool, "reason": str, "checks": [{"check", "ok", "detail"}]}``.
                        ``proposal`` is a ``Proposal``: the commands, the agents' context, and
                        ``await proposal.simulate(minutes)`` to run the action on a copy of the plant.

Code runs inside the OperAItor server process with your permissions, so it is only accepted when OperAItor
runs on your own computer (``plugins_allowed``): never on a public server (PLANT_HOSTED=1), and not when the
server listens on the network unless PLANT_ALLOW_PLUGINS=1 says so.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import inspect
import os
import traceback
import types
from typing import Any

MAX_CODE_CHARS = 200_000
DETECTOR_TIMEOUT_S = 5.0
VALIDATOR_TIMEOUT_S = 300.0
SOURCE_NAME = "<your pipeline>"
RESERVED = {"rules", "pca"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def plugins_allowed(hosted: bool | None = None) -> tuple[bool, str]:
    """May this server run visitors' Python code? (allowed, reason when not)."""
    if hosted is None:
        hosted = os.environ.get("PLANT_HOSTED") == "1"
    if hosted:
        return False, "this is a public server: it never runs code from visitors. Run OperAItor on your own computer to use Python agents"
    flag = os.environ.get("PLANT_ALLOW_PLUGINS", "")
    if flag == "0":
        return False, "Python agents are switched off on this server (PLANT_ALLOW_PLUGINS=0)"
    host = os.environ.get("PLANT_BIND_HOST", "127.0.0.1")
    if flag != "1" and host not in LOOPBACK:
        return False, (f"this server listens on the network ({host}), so it does not run Python agents; "
                       "set PLANT_ALLOW_PLUGINS=1 if everyone who can reach it may run code on this machine")
    return True, ""


class PluginError(ValueError):
    """The code cannot be used; the message says why (with the line in your code when known)."""


def _user_line(exc: BaseException) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == SOURCE_NAME]
    return f"line {frames[-1].lineno}: " if frames else ""


def _describe(exc: BaseException) -> str:
    return f"{_user_line(exc)}{type(exc).__name__}: {exc}"[:500]


# ----------------------------------------------------------------------------- what code can use

@dataclass
class Proposal:
    """One proposed action, as a custom validator sees it."""
    commands: list[dict]
    reasoning: str
    attempt: int                 # 1 for the first proposal of this decision, then 2, 3 ... after rejections
    observation: dict            # the snapshot the agents decided on (measurements, controllers, pump_trains ...)
    detection: dict              # monitoring result: PCA scores, rule checks, which detectors fired
    diagnosis: dict              # ranked fault hypotheses
    brief: dict | None           # the planning agent's brief (target fault, objective, levers)
    knowledge: list[dict]        # knowledge-graph evidence
    limits: dict                 # alarm and SIF (trip) limits of the plant
    _simulate: Any = field(default=None, repr=False)
    _checks: Any = field(default=None, repr=False)

    async def simulate(self, minutes: float = 30.0) -> dict:
        """Apply the commands to a copy of the plant and simulate ``minutes`` ahead.

        Returns the digital twin's measurements: ``terminal_status`` (RUNNING, SAFE_TRIP ...),
        ``restored`` (faulty variables back within limits), ``time_to_safe_min``, ``time_to_restore_min``,
        ``unsafe_fraction``, ``final`` (state at the end) and ``pred`` (t, T², SPE, safe, met per minute)."""
        return await self._simulate(float(minutes))

    def standard_checks(self, result: dict) -> list[dict]:
        """The digital-twin validator's checks on a ``simulate`` result (no trip, safe in time, ...)."""
        return self._checks(result)


@dataclass
class Plugin:
    detectors: list = field(default_factory=list)   # async callables observation -> detector result
    detector_names: list[str] = field(default_factory=list)
    validator: Any = None
    validator_name: str | None = None

    def summary(self) -> dict:
        return {"detectors": self.detector_names, "validator": self.validator_name}


def _name(obj: Any, fallback: str) -> str:
    n = getattr(obj, "name", None)
    if isinstance(n, str) and n.strip():
        return n.strip()[:60]
    n = getattr(obj, "__name__", None) or type(obj).__name__
    return n if n not in ("function", "type") else fallback


def as_detector(obj: Any, name: str):
    """Wrap a user detector: async, time-limited, never raising (an error is reported, not an alarm)."""
    fn = obj.detect if hasattr(obj, "detect") else obj

    async def detect(observation: dict) -> dict:
        try:
            if inspect.iscoroutinefunction(fn):
                out = await asyncio.wait_for(fn(observation), DETECTOR_TIMEOUT_S)
            else:
                out = await asyncio.wait_for(asyncio.to_thread(fn, observation), DETECTOR_TIMEOUT_S)
        except asyncio.TimeoutError:
            return {"name": name, "alarm": False, "custom": True, "error": f"took longer than {DETECTOR_TIMEOUT_S:g} s"}
        except Exception as exc:  # noqa: BLE001 - user code
            return {"name": name, "alarm": False, "custom": True, "error": _describe(exc)}
        if out is None or isinstance(out, bool):
            out = {"alarm": bool(out)}
        if not isinstance(out, dict):
            return {"name": name, "alarm": False, "custom": True,
                    "error": f"returned {type(out).__name__}; return True/False or {{'alarm': bool, 'detail': str}}"}
        return {"name": name, "alarm": bool(out.get("alarm")), "custom": True, "detail": str(out.get("detail", ""))[:300]}
    return detect


async def run_validator(validator: Any, name: str, proposal: Proposal) -> dict:
    """Call a user validator; any failure is a rejection with the reason (no unchecked action)."""
    fn = validator.validate if hasattr(validator, "validate") else validator
    try:
        if inspect.iscoroutinefunction(fn):
            out = await asyncio.wait_for(fn(proposal), VALIDATOR_TIMEOUT_S)
        else:
            out = await asyncio.wait_for(asyncio.to_thread(fn, proposal), VALIDATOR_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"accept": False, "reason": f"{name} took longer than {VALIDATOR_TIMEOUT_S:g} s", "checks": [], "error": True}
    except Exception as exc:  # noqa: BLE001 - user code
        return {"accept": False, "reason": f"{name} failed: {_describe(exc)}", "checks": [], "error": True}
    if isinstance(out, bool):
        out = {"accept": out}
    if not isinstance(out, dict) or "accept" not in out:
        return {"accept": False, "reason": f"{name} must return {{'accept': bool, 'reason': str}}", "checks": [], "error": True}
    checks = []
    for c in out.get("checks") or []:
        if isinstance(c, dict):
            checks.append({"check": str(c.get("check", ""))[:200], "ok": bool(c.get("ok")), "detail": str(c.get("detail", ""))[:400]})
    return {"accept": bool(out["accept"]), "reason": str(out.get("reason", ""))[:600], "checks": checks}


def load_plugin(code: str) -> Plugin:
    """Run the code as a module and collect DETECTORS and VALIDATOR. Raises PluginError."""
    if len(code) > MAX_CODE_CHARS:
        raise PluginError(f"the code is longer than {MAX_CODE_CHARS:,} characters")
    try:
        compiled = compile(code, SOURCE_NAME, "exec")
    except SyntaxError as exc:
        raise PluginError(f"line {exc.lineno}: {exc.msg}") from None
    mod = types.ModuleType("operaitor_user_pipeline")
    mod.__file__ = SOURCE_NAME
    try:
        exec(compiled, mod.__dict__)  # noqa: S102 - the user's own code on their own computer (plugins_allowed)
    except Exception as exc:  # noqa: BLE001
        raise PluginError(_describe(exc)) from None
    plugin = Plugin()
    found = False
    dets = mod.__dict__.get("DETECTORS")
    if dets is not None:
        found = True
        if not isinstance(dets, (list, tuple)):
            raise PluginError("DETECTORS must be a list")
        for i, d in enumerate(dets):
            if not (callable(d) or callable(getattr(d, "detect", None))):
                raise PluginError(f"DETECTORS[{i}] needs a detect(observation) method (or is a function)")
            if inspect.isclass(d):
                raise PluginError(f"DETECTORS[{i}] is a class; put an instance in the list: {d.__name__}()")
            name = _name(d, f"detector_{i + 1}")
            if name in RESERVED or name in plugin.detector_names:
                raise PluginError(f"detector name {name!r} is taken; give it another name")
            plugin.detectors.append(as_detector(d, name))
            plugin.detector_names.append(name)
    val = mod.__dict__.get("VALIDATOR")
    if val is not None:
        found = True
        if inspect.isclass(val):
            raise PluginError(f"VALIDATOR is a class; use an instance: VALIDATOR = {val.__name__}()")
        if not (callable(getattr(val, "validate", None)) or callable(val)):
            raise PluginError("VALIDATOR needs a validate(proposal) method")
        plugin.validator, plugin.validator_name = val, _name(val, "validator")
    if not found:
        raise PluginError("define DETECTORS = [...] and/or VALIDATOR = ... (see the example)")
    return plugin


TEMPLATE = '''"""My Python agents for OperAItor (they run only when OperAItor runs on your own computer)."""
from operaitor.plugins import Proposal


class PressureRiseDetector:
    """Raises a fault when the reactor pressure rises faster than 0.05 bar/min."""
    name = "reactor_pressure_rise"

    def __init__(self):
        self.last = None   # (time_min, pressure)

    async def detect(self, observation: dict) -> dict:
        t, p = observation["time_min"], observation["measurements"]["cstr_pressure_bar"]
        rate = 0.0 if self.last is None or t <= self.last[0] else (p - self.last[1]) / (t - self.last[0])
        self.last = (t, p)
        return {"alarm": rate > 0.05, "detail": f"{rate:+.3f} bar/min"}


class SimulateThenCheck:
    """Accepts an action only if 20 minutes of simulation keep the plant safe and bring the faulty variables back."""
    name = "simulate_then_check"

    async def validate(self, proposal: Proposal) -> dict:
        if not proposal.commands:
            return {"accept": False, "reason": "no commands proposed"}
        result = await proposal.simulate(minutes=20)
        checks = proposal.standard_checks(result)
        failed = [c for c in checks if not c["ok"]]
        return {"accept": not failed, "checks": checks,
                "reason": "the simulation passes every check" if not failed else f"{failed[0]['check']}: {failed[0]['detail']}"}


DETECTORS = [PressureRiseDetector()]
VALIDATOR = SimulateThenCheck()   # used when the pipeline's "Check each action with" is "Your Python validator"
'''


def proposal_for(state: dict, ctx, cfg) -> Proposal:
    """Build the Proposal a custom validator gets, from the recovery graph's state."""
    from operaitor.agents.judge import _limits
    from operaitor.agents.recovery_graph import faulty_targets, twin_rollout, validate_outcome

    async def simulate(minutes: float) -> dict:
        twin = await ctx.snapshot_env()
        targets = faulty_targets(state["detection"], state["diagnosis"], ctx.monitor.pca, cfg.pca_band_sigma,
                                 state["observation"])
        horizon = max(1.0, min(float(minutes), 120.0))
        return await asyncio.to_thread(twin_rollout, twin, state["action"], replace(cfg, twin_horizon_min=horizon),
                                       ctx.monitor.pca, targets)

    plan = state.get("plan") or {}
    return Proposal(commands=list(plan.get("commands") or []), reasoning=str(plan.get("reasoning", "")),
                    attempt=int(state.get("attempts") or 1), observation=state["observation"],
                    detection=state["detection"], diagnosis=state["diagnosis"], brief=state.get("brief"),
                    knowledge=state.get("knowledge") or [], limits=_limits(),
                    _simulate=simulate, _checks=lambda m: validate_outcome(m, cfg)["checks"])


__all__ = ["Proposal", "Plugin", "PluginError", "load_plugin", "plugins_allowed", "TEMPLATE"]
