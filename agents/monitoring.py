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

    def reset(self) -> None:
        self.consecutive_pca = 0
        self.last_action_min = None
        self.last_signature = None
        self.wait_until_min = None
        self.handled_trains.clear()
        self.pending_trains.clear()


class MonitoringAgent:
    """LangGraph node: detection + diagnosis + decision whether to hand off."""

    def __init__(self, pca: PCAMonitor | None = None, *, persistence: int = 3,
                 cooldown_min: float = 5.0, extra_detectors: list[Detector] | None = None,
                 rules_trigger: bool = True):
        self.pca = pca
        self.persistence = persistence
        self.cooldown_min = cooldown_min
        self.rules_trigger = rules_trigger  # False: rules only inform diagnosis (PCA-only ablation)
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
        fault = bool((self.rules_trigger and rules["abnormal"]) or pca_fault or extra_alarm)

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
        new_hard_fault = bool(set(rules["failed_trains"]) - mem.handled_trains)
        should_act = fault and not rules["plant_trip"] and (cooled or new_hard_fault)
        mem.pending_trains = set(rules["failed_trains"])

        detection = {
            "time_min": now, "fault": fault, "should_act": should_act,
            "pca": score.to_dict() if score is not None else None,
            "pca_persistent": pca_fault, "consecutive_pca": mem.consecutive_pca,
            "verifying_until": mem.wait_until_min if waiting else None,
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
