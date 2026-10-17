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

from operaitor.fdd import DEFAULT_MODEL_PATH, PCAMonitor, diagnose, rule_checks
from operaitor.plant.alarms import default_alarm_definitions

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


# --------------------------------------------------------------------------- dynamics
TREND_MIN = 6.0  # history used for rates of change
# key variable -> limits it can run into: (name, value, "high"/"low", "alarm"/"SIF")
_LIMITS = None


def _limits() -> dict:
    global _LIMITS
    if _LIMITS is None:
        from operaitor.plant.safety import SafetyConfig
        c = SafetyConfig()
        lim = {"cstr_pressure_bar": [("R_P_HH SIF", c.cstr_pressure_hh_bar, "high")],
               "cstr_temperature_K": [("R_T_HH SIF", c.cstr_temperature_hh_K, "high")],
               "cstr_holdup_kmol": [("R_L_HH SIF", c.cstr_level_hh_kmol, "high"), ("R_L_LL SIF", c.cstr_level_ll_kmol, "low")],
               "reboiler_holdup_kmol": [("RB_L_HH SIF (plant trip)", c.reboiler_level_hh_kmol, "high"),
                                        ("RB_L_LL SIF", c.reboiler_level_ll_kmol, "low")],
               "drum_holdup_kmol": [("DR_L_HH SIF (plant trip)", c.drum_level_hh_kmol, "high"),
                                    ("DR_L_LL SIF (plant trip)", c.drum_level_ll_kmol, "low")],
               "column_pressure_bar": [("COL_P_HH SIF (plant trip)", c.column_pressure_hh_bar, "high")],
               "column_min_tray_holdup_kmol": [("COL_L_LL SIF (plant trip)", c.column_tray_holdup_ll_kmol, "low")],
               "distillate_x_acetone": [], "top_temperature_K": [], "bottom_temperature_K": []}
        for d in default_alarm_definitions():
            if d.tag in lim:
                lim[d.tag].append((f"{d.alarm_id} alarm", d.limit, d.direction.lower()))
        _LIMITS = lim
    return _LIMITS


def process_trends(history: list) -> dict:
    """Rate of change of the key variables over the recent history and, at that rate, the minutes
    left before the next alarm / SIF limit in the direction of travel."""
    if len(history) < 3:
        return {}
    t0, m0 = history[0]
    t1, m1 = history[-1]
    dt = t1 - t0
    out = {}
    for tag, limits in _limits().items():
        if tag not in m1 or tag not in m0 or dt <= 0:
            continue
        rate = (m1[tag] - m0[tag]) / dt
        row = {"value": round(m1[tag], 4), "rate_per_min": round(rate, 5)}
        ahead = [(n, v) for n, v, d in limits
                 if (d == "high" and rate > 0 and v > m1[tag]) or (d == "low" and rate < 0 and v < m1[tag])]
        if ahead and abs(rate) > 1e-9:
            name, v = min(ahead, key=lambda nv: abs(nv[1] - m1[tag]))
            row.update(next_limit=name, limit_value=v, minutes_to_limit=round(abs(v - m1[tag]) / abs(rate), 1))
            sif = [(n, v) for n, v in ahead if "SIF" in n]
            if sif:
                n, v = min(sif, key=lambda nv: abs(nv[1] - m1[tag]))
                row.update(sif_limit=n, minutes_to_sif=round(abs(v - m1[tag]) / abs(rate), 1))
        out[tag] = row
    return out


@dataclass
class MonitorMemory:
    """Cross-cycle state the stateless graph needs (debounce and cooldown)."""
    consecutive_pca: int = 0
    consecutive_spe: int = 0
    last_action_min: float | None = None
    last_signature: str | None = None
    wait_until_min: float | None = None   # verification window: twin-predicted recovery time
    handled_trains: set[str] = field(default_factory=set)
    pending_trains: set[str] = field(default_factory=set)
    handled_p1: set[str] = field(default_factory=set)     # priority-1 alarms already seen at a decision
    pending_p1: set[str] = field(default_factory=set)
    # event trigger: one decision per fault episode
    episode_active: bool = False
    quiet_since_min: float | None = None                   # trigger condition false since
    handled_alarms: set[str] = field(default_factory=set) # alarms already seen at a decision in this episode
    pending_alarms: set[str] = field(default_factory=set)
    history: list = field(default_factory=list)           # (t, key measurements) over the last TREND_MIN

    def reset(self) -> None:
        self.consecutive_pca = 0
        self.last_action_min = None
        self.last_signature = None
        self.wait_until_min = None
        self.handled_trains.clear()
        self.pending_trains.clear()
        self.handled_p1.clear()
        self.pending_p1.clear()
        self.history.clear()
        self.episode_active = False
        self.quiet_since_min = None
        self.handled_alarms.clear()
        self.pending_alarms.clear()


class MonitoringAgent:
    """LangGraph node: detection + diagnosis + decision whether to hand off."""

    def __init__(self, pca: PCAMonitor | None = None, *, persistence: int = 3,
                 cooldown_min: float = 5.0, extra_detectors: list[Detector] | None = None,
                 rules_trigger: bool = True, quality_needs_pca: bool = True,
                 trigger: str = "cooldown", episode_clear_min: float = 5.0):
        self.pca = pca
        # "cooldown" (v1): re-decide every cooldown_min while a fault is detected.
        # "event" (v2): decide when an alarm is active or SPE stays above its limit, once per fault
        # episode; a new priority-1 alarm or a newly failed pump opens a new decision. The episode ends after the
        # trigger has been quiet for episode_clear_min.
        self.trigger = trigger
        self.episode_clear_min = episode_clear_min
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
        if self.trigger == "event":
            mem.consecutive_spe = mem.consecutive_spe + 1 if score is not None and score.spe > score.spe_limit else 0
            alarms = set(rules["active_alarms"])
            triggered = bool(alarms) or mem.consecutive_spe >= self.persistence or extra_alarm
            if triggered:
                mem.quiet_since_min = None
            elif mem.episode_active:
                mem.quiet_since_min = now if mem.quiet_since_min is None else mem.quiet_since_min
                if now - mem.quiet_since_min >= self.episode_clear_min - 1e-9:
                    mem.episode_active = False
                    mem.handled_alarms.clear()
            # within an episode only hard new evidence opens a new decision: a new priority-1 (safety)
            # alarm or a newly failed pump; further alarms of the same cascade do not
            new_event = (not mem.episode_active or bool(p1 - mem.handled_p1)
                         or bool(set(rules["failed_trains"]) - mem.handled_trains))
            fault = triggered or extra_alarm
            should_act = triggered and new_event and not rules["plant_trip"]
            mem.pending_alarms = alarms

        mem.history = [h for h in mem.history if h[0] > now - TREND_MIN] + [(now, dict(obs["measurements"]))]
        detection = {
            "time_min": now, "fault": fault, "should_act": should_act,
            "trends": process_trends(mem.history),
            "pca": score.to_dict() if score is not None else None,
            "pca_persistent": pca_fault, "consecutive_pca": mem.consecutive_pca,
            "verifying_until": mem.wait_until_min if waiting else None,
            "advisory": advisory,  # quality alarms not acted on (PCA has not confirmed a fault)
            "rules": rules,
            "detectors": {n: bool(r["alarm"]) for n, r in by_name.items()},
        }
        custom = {n: {k: r.get(k) for k in ("alarm", "detail", "error")} for n, r in by_name.items() if r.get("custom")}
        if custom:  # a custom pipeline's Python detectors (operaitor/plugins.py)
            detection["custom_detectors"] = custom
        return {"detection": detection, "diagnosis": diagnosis}

    def mark_acted(self, time_min: float, signature: str, wait_min: float | None = None) -> None:
        """Record an action; ``wait_min`` = expected recovery time (verification window)."""
        mem = self.memory
        mem.last_action_min = time_min
        mem.last_signature = signature
        mem.wait_until_min = time_min + wait_min if wait_min else None
        mem.handled_trains |= mem.pending_trains
        mem.handled_p1 |= mem.pending_p1
        mem.handled_alarms |= mem.pending_alarms
        mem.episode_active = True
