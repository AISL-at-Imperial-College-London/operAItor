"""Monitoring agent: runs fault-detection algorithms concurrently, then diagnoses.

Detectors are independent async callables ``detector(observation) -> dict``;
add more (e.g. a dynamic PCA, an autoencoder, a KG consistency check) by
appending to ``MonitoringAgent.detectors``. They run in parallel with
``asyncio.gather``; CPU-bound ones are pushed onto worker threads.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from fdd import DEFAULT_MODEL_PATH, PCAMonitor, diagnose, rule_checks
from integrated_plant.alarms import default_alarm_definitions

# Product-quality alarms (priority 3: distillate purity, bottoms acetone). They recover
# slowly after a disturbance; on their own they only trigger the agents if PCA confirms.
QUALITY_ALARMS = {d.alarm_id for d in default_alarm_definitions() if d.priority >= 3}
# priority-1 process alarms (pump-trip alarms are covered by the failed-train evidence)
P1_ALARMS = {d.alarm_id for d in default_alarm_definitions() if d.priority == 1 and not d.tag.startswith("pump_")}

Detector = Callable[[dict], Awaitable[dict]]


def pca_detector(model: PCAMonitor) -> Detector:
    async def detect(observation: dict) -> dict:
        score = await asyncio.to_thread(model.score_observation, observation)
        return {"name": "pca", "alarm": score.violation, "score": score}
    return detect


async def rule_detector(observation: dict) -> dict:
    rules = rule_checks(observation)
    return {"name": "rules", "alarm": rules["abnormal"], "rules": rules}


@dataclass
class MonitorMemory:
    """Cross-cycle state the stateless graph needs (debounce and cooldown)."""
    consecutive_pca: int = 0
    last_action_min: float | None = None
    last_signature: str | None = None
    wait_until_min: float | None = None   # verification window: twin-predicted recovery time
    handled_trains: set[str] = field(default_factory=set)
    pending_trains: set[str] = field(default_factory=set)
    handled_p1: set[str] = field(default_factory=set)     # priority-1 alarms already seen at a decision
    pending_p1: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self.consecutive_pca = 0
        self.last_action_min = None
        self.last_signature = None
        self.wait_until_min = None
        self.handled_trains.clear()
        self.pending_trains.clear()
        self.handled_p1.clear()
        self.pending_p1.clear()


class MonitoringAgent:
    """LangGraph node: detection + diagnosis + decision whether to hand off."""

    def __init__(self, pca: PCAMonitor | None = None, *, persistence: int = 3,
                 cooldown_min: float = 5.0, extra_detectors: list[Detector] | None = None,
                 rules_trigger: bool = True, quality_needs_pca: bool = True):
        self.pca = pca
        self.persistence = persistence
        self.cooldown_min = cooldown_min
        self.rules_trigger = rules_trigger  # False: rules only inform diagnosis (PCA-only ablation)
        self.quality_needs_pca = quality_needs_pca  # quality-only alarms act only when PCA confirms
        self.memory = MonitorMemory()
        self.detectors: list[Detector] = [rule_detector]
        if pca is not None:
            self.detectors.append(pca_detector(pca))
        self.detectors += list(extra_detectors or [])

    @classmethod
    def from_default_model(cls, path: str | Path = DEFAULT_MODEL_PATH, **kwargs) -> "MonitoringAgent":
        p = Path(path)
        return cls(PCAMonitor.load(p) if p.exists() else None, **kwargs)

    async def __call__(self, state: dict) -> dict:
        obs = state["observation"]
        results = await asyncio.gather(*(d(obs) for d in self.detectors))
        by_name = {r["name"]: r for r in results}
        rules = by_name["rules"]["rules"]
        score = by_name.get("pca", {}).get("score")

        mem = self.memory
        mem.consecutive_pca = mem.consecutive_pca + 1 if score is not None and score.violation else 0
        pca_fault = mem.consecutive_pca >= self.persistence
        extra_alarm = any(r["alarm"] for n, r in by_name.items() if n not in ("rules", "pca"))
        # Hard rule evidence triggers directly: failed pump, safety alarm (priority 1-2), SIF, saturated loop.
        # A quality alarm on its own (priority 3) only counts once PCA confirms the fault.
        quality = [a for a in rules["active_alarms"] if a in QUALITY_ALARMS]
        hard_alarms = [a for a in rules["active_alarms"] if a not in QUALITY_ALARMS]
        rules_hard = bool(rules["failed_trains"] or hard_alarms or rules["saturated_loops"] or rules["sif_latched"])
        rules_fault = rules_hard or (bool(quality) and (pca_fault or not self.quality_needs_pca))
        advisory = quality if quality and not rules_fault and not pca_fault else []
        # PCA is the trigger. Rule evidence (alarms, failed pumps, saturated loops) only informs the
        # diagnosis, unless rules_trigger is set or no PCA model is loaded (then rules are the fallback).
        rules_may_trigger = self.rules_trigger or self.pca is None
        fault = bool((rules_may_trigger and rules_fault) or pca_fault or extra_alarm)

        diagnosis = diagnose(score if pca_fault else None, rules)
        if score is not None and not pca_fault:
            diagnosis["contributors"] = score.top_contributors(6)
        now = float(obs["time_min"])
        # Give each action time to take effect before acting again. The PCA
        # diagnosis label can change cycle to cycle, so it must not bypass the
        # cooldown; only hard new evidence (a newly failed pump) does.
        cooled = mem.last_action_min is None or now - mem.last_action_min >= self.cooldown_min
        # After an accepted action, give the plant the recovery time the digital twin predicted.
        waiting = mem.wait_until_min is not None and now < mem.wait_until_min - 1e-9
        cooled = cooled and not waiting
        mem.handled_trains &= set(rules["failed_trains"])  # recovered trains can fail anew
        # Hard new evidence overrides the cooldown and the verification wait: a newly failed pump,
        # or a new priority-1 (safety) alarm that was not there when the agents last decided
        # (e.g. a second fault developing while the first one is being handled).
        p1 = {a for a in rules["active_alarms"] if a in P1_ALARMS}
        mem.handled_p1 &= p1
        new_hard_fault = bool(set(rules["failed_trains"]) - mem.handled_trains) or bool(p1 - mem.handled_p1)
        should_act = fault and not rules["plant_trip"] and (cooled or new_hard_fault)
        mem.pending_trains = set(rules["failed_trains"])
        mem.pending_p1 = p1

        detection = {
            "time_min": now, "fault": fault, "should_act": should_act,
            "pca": score.to_dict() if score is not None else None,
            "pca_persistent": pca_fault, "consecutive_pca": mem.consecutive_pca,
            "verifying_until": mem.wait_until_min if waiting else None,
            "advisory": advisory,  # quality alarms not acted on (PCA has not confirmed a fault)
            "rules": rules,
            "detectors": {n: bool(r["alarm"]) for n, r in by_name.items()},
        }
        return {"detection": detection, "diagnosis": diagnosis}

    def mark_acted(self, time_min: float, signature: str, wait_min: float | None = None) -> None:
        """Record an action; ``wait_min`` = expected recovery time (verification window)."""
        mem = self.memory
        mem.last_action_min = time_min
        mem.last_signature = signature
        mem.wait_until_min = time_min + wait_min if wait_min else None
        mem.handled_trains |= mem.pending_trains
        mem.handled_p1 |= mem.pending_p1
