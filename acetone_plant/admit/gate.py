"""ADMIT as the validator of the recovery graph (agent configuration ``llm_admit``).

For every proposal the action agent files one ADMIT action record per command (cited evidence,
checks performed, confidence, reversibility, recovery plan, hazard / diagnosis). The platform —
never the agent — builds the evidence ledger from the live plant (hash-chained, with trust taken
from the channel: plant measurements, PCA and alarms are trusted, the LLM diagnosis is not).
ADMIT's hard gates then decide admissibility:

  T0 record integrity   T1 evidence   T2 hazard   T3 authority / mode / SOP steps / reversibility
  T4 consequence: null vs plan projected on a clone of the live plant (``AcetoneWorld``)

The proposal is executed only if every record passes; otherwise the violation codes and ADMIT's
own remedies (``admitbench explain``) go back to the action agent through the reprompter.
"""
from __future__ import annotations

from pathlib import Path
import json

from admitbench.cartridge import Case, load_cartridge
from admitbench.doctor import explain
from admitbench.gates import run_gates
from admitbench.physics import Trajectory, World
from admitbench.record import ActionRecord, DecisionContext, EvidenceLog

from acetone_plant.plant import PUMP_TRAINS, SupervisoryAction

from .spec import FAULT_HAZARD, SIF_HAZARD, SYNTHETIC_TAGS
from .world import AcetoneWorld, command_to_grammar

CARTRIDGE_DIR = Path(__file__).resolve().parent / "cartridge"


class NoProjection(World):
    """ADMIT without the simulation layer: T4 has nothing to project, so it never objects.
    Admissibility then rests on T0-T3 (record, evidence, hazard, authority / mode / SOP steps)."""
    name = "no_projection"

    def initial_state(self, overrides=None) -> dict:
        return {}

    def project(self, state, action=None, params=None, horizon_s=None) -> Trajectory:
        return Trajectory(min_margin=1.0)

    def rehearse_recovery(self, *a, **k) -> dict:
        return {"recovered": True, "end_margin": 1.0, "crossed_during_recovery": False, "skipped": True}


def evidence_entries(observation: dict, detection: dict, diagnosis: dict | None, knowledge: list | None,
                     decided_at_s: float, tags: list[str]) -> list[dict]:
    """The platform's evidence ledger at a decision point (same format as cartridge case evidence)."""
    t = decided_at_s - 1.0
    m = observation["measurements"]
    out = []
    for tag in tags:
        if tag in SYNTHETIC_TAGS or tag not in m:
            continue
        out.append({"id": f"ev_{tag}", "tag": tag, "content": f"{tag} = {m[tag]:.5g}", "source": "control_system",
                    "received_at": t, "value": round(float(m[tag]), 5), "quality": "good"})
    pca = detection.get("pca") or {}
    if pca:
        contrib = ", ".join(c["variable"] for c in (pca.get("top_contributors") or [])[:5])
        out.append({"id": "ev_pca", "tag": "FDD-PCA", "source": "fdd", "received_at": t, "quality": "good",
                    "content": (f"Hotelling T2 {pca.get('t2', 0):.3g} (limit {pca.get('t2_limit', 0):.3g}), SPE "
                                f"{pca.get('spe', 0):.3g} (limit {pca.get('spe_limit', 0):.3g}); fault confirmed: "
                                f"{bool(detection.get('pca_persistent'))}; top contributors: {contrib}")})
    rules = detection.get("rules") or {}
    out.append({"id": "ev_alarms", "tag": "ALARMS", "source": "alarm_system", "received_at": t, "quality": "good",
                "content": "active alarms: " + (", ".join(rules.get("active_alarms") or []) or "none")
                + "; SIF latched: " + (", ".join(rules.get("sif_latched") or []) or "none")})
    pumps = []
    for train, info in observation["pump_trains"].items():
        state = "FAILED" if info["selected_failed"] else "running"
        spare = "unavailable" if info.get("spare_failed") else "available"
        pumps.append(f"{train}: selected {info.get('active_pump', PUMP_TRAINS[train].primary_id)} {state}, standby {spare}")
    out.append({"id": "ev_pumps", "tag": "PUMP-STATUS", "source": "control_system", "received_at": t,
                "quality": "good", "content": "; ".join(pumps)})
    trends = detection.get("trends") or {}
    near = sorted(((v["minutes_to_sif"], k, v["sif_limit"]) for k, v in trends.items() if v.get("minutes_to_sif")))
    if near:
        out.append({"id": "ev_trends", "tag": "TRENDS", "source": "fdd", "received_at": t, "quality": "good",
                    "content": "; ".join(f"{k} reaches {s} in {m_:.1f} min" for m_, k, s in near[:4])})
    if diagnosis and diagnosis.get("summary"):
        out.append({"id": "ev_diagnosis", "tag": "DIAGNOSIS", "source": "llm_diagnosis", "received_at": t,
                    "quality": "good", "content": str(diagnosis["summary"])[:400]})
    if knowledge:
        out.append({"id": "ev_kg", "tag": "KG", "source": "knowledge_graph", "received_at": t, "quality": "good",
                    "content": "KG nodes: " + ", ".join(str(n.get("id")) for n in knowledge[:12])})
    return out


def hazard_for(detection: dict) -> tuple[str | None, list[str]]:
    """The hazard the plant is heading for (nearest SIF on the current trends), with the
    diagnosis phrases that count as naming it (T2)."""
    trends = detection.get("trends") or {}
    near = sorted((v["minutes_to_sif"], v["sif_limit"].split()[0]) for v in trends.values() if v.get("minutes_to_sif"))
    latched = (detection.get("rules") or {}).get("sif_latched") or []
    sif = near[0][1] if near else (latched[0] if latched else None)
    if sif not in SIF_HAZARD:
        return None, []
    hid, name, _, _ = SIF_HAZARD[sif]
    phrases = [name] + [f.replace("_", " ") for f, h in FAULT_HAZARD.items() if h == hid]
    return hid, [p for p in phrases if len(p.split()) >= 2]


def operating_mode(detection: dict) -> str:
    rules = detection.get("rules") or {}
    if rules.get("plant_trip"):
        return "emergency"
    if detection.get("pca_persistent") or rules.get("sif_latched") or detection.get("fault"):
        return "upset"
    return "normal"


class AdmitGate:
    def __init__(self, cartridge: str | Path = CARTRIDGE_DIR, horizon_s: float | None = None, simulate: bool = True):
        self.simulate = simulate
        self.cartridge = load_cartridge(str(cartridge))
        self.rulebook = self.cartridge.rulebook
        self.horizon_s = float(horizon_s or self.cartridge.horizon_s)
        self.tags = [t["id"] for t in self.cartridge.system if t.get("kind") == "tag"]
        self._null: tuple[float, dict] = (-1.0, {})  # null projections of the current decision point

    # ------------------------------------------------------------ what the agent is shown
    def brief(self, observation: dict, detection: dict, diagnosis: dict | None, knowledge: list | None) -> dict:
        decided = 60.0 * observation["time_min"]
        hid, accept = hazard_for(detection)
        grammar = {}
        for name in self.rulebook.actions():
            r = self.rulebook.get(name)
            if r.forbidden:
                continue
            grammar[name] = {"class": r.action_class, "reversibility": r.reversibility,
                             "required_params": r.required_params, "required_authority": r.required_authority,
                             "required_evidence_tags": r.required_evidence_tags, "required_steps": r.required_steps,
                             "step_evidence_tags": r.step_evidence_tags, "confidence_floor": r.confidence_floor,
                             "allowed_modes": r.allowed_modes}
        return {"evidence": [{k: e[k] for k in ("id", "tag", "source", "content")} | {"trust": self.cartridge.manifest["trust_map"].get(e["source"], "untrusted")}
                             for e in evidence_entries(observation, detection, diagnosis, knowledge, decided, self.tags)],
                "authority_scope": self.cartridge.manifest["agent"]["authority_scope"],
                "mode": operating_mode(detection), "active_hazard": hid, "grammar": grammar,
                "consequence_projection": "on" if self.simulate else "off",
                "command_mapping": {"SET_SETPOINT": "adjust_setpoint", "SET_THROUGHPUT": "set_throughput",
                                    "START_SPARE": "start_standby_pump", "OPERATE_AUXILIARY H2_BDV > 0": "open_blowdown",
                                    "OPERATE_AUXILIARY H2_BDV = 0": "close_blowdown",
                                    "OPERATE_AUXILIARY EMERGENCY_COOLING > 0": "apply_emergency_cooling",
                                    "OPERATE_AUXILIARY EMERGENCY_COOLING = 0": "stop_emergency_cooling",
                                    "NO_ACTION": "hold_and_monitor"}}

    # ------------------------------------------------------------ the gate
    def evaluate(self, env, action: SupervisoryAction, commands: list[dict], records: list[dict | None],
                 observation: dict, detection: dict, diagnosis: dict | None, knowledge: list | None) -> dict:
        """Run T0–T4 (+T6) for every command's record; blocking, run it in a worker thread."""
        decided = 60.0 * observation["time_min"]
        log = EvidenceLog(self.cartridge.manifest["trust_map"], self.cartridge.manifest.get("freshness_s") or {})
        for e in evidence_entries(observation, detection, diagnosis, knowledge, decided, self.tags):
            log.admit(id=e["id"], content=e["content"], source=e["source"], received_at=e["received_at"],
                      tag=e["tag"], value=e.get("value"), quality=e["quality"])
        mode = operating_mode(detection)
        ctx = DecisionContext(authority_scope=list(self.cartridge.manifest["agent"]["authority_scope"]),
                              mode=mode, decided_at=decided, response_time_s=float(self.cartridge.response_time_s))
        hid, accept = hazard_for(detection)
        case = Case(id="live", title="live decision", mode=mode, hazard_id=hid, diagnosis_accept=accept)
        if not self.simulate:
            world = NoProjection()
        else:
            if self._null[0] != observation["time_min"]:
                self._null = (observation["time_min"], {})
            world = AcetoneWorld(env=env, plan=None if action == SupervisoryAction.no_action() else action,
                                 null_cache=self._null[1])
        results, admissible = [], True
        for cmd, rec in zip(commands or [{"type": "NO_ACTION"}], records or [None]):
            name, params = command_to_grammar(cmd)
            rec = rec or {}
            data = {"action": name, "params": params,
                    "output": {"hazard_id": rec.get("hazard_id") or "none", "diagnosis": rec.get("diagnosis") or ""},
                    "cited_evidence": rec.get("cited_evidence") or [], "checks_performed": rec.get("checks_performed") or [],
                    "confidence": rec.get("confidence", 0.0), "reversibility": rec.get("reversibility") or "",
                    "recovery_plan": rec.get("recovery_plan") or "", "retry_key": rec.get("retry_key"),
                    "rationale": rec.get("rationale") or ""}
            try:
                record = ActionRecord.from_dict(data, raw=json.dumps(data, default=str))
                record_error = None
            except (TypeError, ValueError) as exc:
                record, record_error = None, f"{type(exc).__name__}: {exc}"
            report, artifacts = run_gates(record, case, self.rulebook, log, ctx, world,
                                          parse_error=record_error, horizon_s=self.horizon_s)
            violations = [{"code": v.code, "gate": v.gate, "message": v.message} for v in report.all_violations()]
            admissible &= not report.hard_failed
            results.append({"command": cmd, "action": name, "admissible": not report.hard_failed,
                            "first_failing": report.first_failing, "aas_code": report.aas_code,
                            "violations": violations, "trajectories": artifacts if self.simulate else {}})
        remedies = {}
        for r in results:
            for v in r["violations"]:
                remedies.setdefault(v["code"], explain(v["code"]).split("what to do:", 1)[-1].strip())
        return {"admissible": admissible, "mode": mode, "hazard_id": hid, "records": results, "remedies": remedies,
                "simulate": self.simulate,
                "ledger_head": log.ledger_head}
