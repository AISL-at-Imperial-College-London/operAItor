"""Agent pipelines: the reference configurations, and custom ones people build from them.

A pipeline is the agent architecture one session runs: which validator checks each action, the
planner, the knowledge graph, each LLM agent's system prompt, and the decision settings. The
reference pipelines (``REFERENCE_PIPELINES``) are the published baselines and never change; a
custom pipeline names the reference it starts from (``base``) and overrides parts of it:

    {"name": "my pipeline", "base": "llm_admit_nosim",
     "settings": {"validator": "judge", "reprompt_max": 2},
     "prompts": {"action": {"mode": "append", "text": "Prefer setpoint changes over pump switches."}},
     "kg": "<JSON-LD text in the format of operaitor/data/knowledge_graph/*.jsonld>"}

Pipelines are data only (no code), so a public server can accept them. The console keeps them in
the visitor's browser and sends the active one to the server; the server keeps it in memory for
the session only.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Any

# The baselines a visitor can run (and start a custom pipeline from), with their console labels.
REFERENCE_PIPELINES = {"llm_admit": "LLM + ADMIT gates", "llm_admit_nosim": "LLM + ADMIT (no simulation)",
                       "llm": "LLM + digital twin", "llm_judge": "LLM + LLM judge",
                       "llm_lucky": "LLM, no validation"}

PROMPT_AGENTS = {"diagnosis": "Diagnosis agent", "planning": "Planning agent (LLM planner only)",
                 "action": "Action agent", "judge": "LLM judge (judge validator only)"}
PROMPT_MODES = ("append", "replace")

MAX_PROMPT_CHARS = 20_000
MAX_KG_BYTES = 5_000_000
MAX_NAME_CHARS = 60

# What a custom pipeline may change, with the console's labels. "validator" maps onto the
# AgentConfig switches (twin / judge / admit / admit_sim); the rest are AgentConfig fields.
SETTINGS: dict[str, dict[str, Any]] = {
    "validator": {"label": "Check each action with", "choices": {
        "twin": "Digital twin (simulate the action)", "admit": "ADMIT gates (T0-T4)",
        "admit_nosim": "ADMIT gates, no simulation (T0-T3)", "judge": "LLM judge", "none": "Nothing (apply as proposed)",
        "custom": "Your Python validator (VALIDATOR)"}},
    "planner": {"label": "Planner", "choices": {"kg": "Knowledge-graph rules", "llm": "LLM planning agent"}},
    "diagnoser": {"label": "Diagnosis", "choices": {"llm": "LLM diagnosis agent", "heuristic": "Rules only"}},
    "use_kg": {"label": "Use the knowledge graph", "type": "bool"},
    "reprompt_max": {"label": "Retries after a rejected proposal", "type": "int", "min": 0, "max": 5},
    "acceptance": {"label": "Digital twin: accept when", "choices": {
        "alarms": "faulty variables within alarm limits", "restore": "plant fully back to normal"}},
    "twin_horizon_min": {"label": "Digital twin look-ahead (min)", "type": "float", "min": 5, "max": 60},
    "fallback_to_safe": {"label": "If every retry fails, apply the best safe candidate", "type": "bool"},
    "trigger": {"label": "Decide", "choices": {"cooldown": "while a fault lasts (each cooldown)",
                                               "event": "once per fault episode"}},
    "persistence": {"label": "PCA samples over the limit to confirm a fault", "type": "int", "min": 1, "max": 10},
    "cooldown_min": {"label": "Cooldown between decisions (min)", "type": "float", "min": 1, "max": 30},
}


def validator_of(cfg) -> str:
    if cfg.admit:
        return "admit" if cfg.admit_sim else "admit_nosim"
    if cfg.twin:
        return "twin"
    return "judge" if cfg.judge else "none"


def reference_settings(preset: str) -> dict:
    """The settings a reference pipeline runs with, in the console's terms."""
    from .recovery_graph import make_config
    cfg = make_config(preset)
    out = {k: getattr(cfg, k) for k in SETTINGS if k != "validator"}
    out["validator"] = validator_of(cfg)
    out["diagnoser"] = "llm"  # live sessions with an LLM use the LLM diagnosis agent (sessions.py)
    return out


def reference_prompts() -> dict[str, str]:
    """Each LLM agent's reference system prompt, as it runs now."""
    from .action import _context_level, action_system_prompt
    from .diagnosis_llm import DIAGNOSIS_SYSTEM_PROMPT
    from .judge import JUDGE_SYSTEM_PROMPT
    from .planning import PLANNER_PROMPT
    return {"diagnosis": DIAGNOSIS_SYSTEM_PROMPT.strip(), "planning": PLANNER_PROMPT.strip(),
            "action": action_system_prompt(_context_level()).strip(), "judge": JUDGE_SYSTEM_PROMPT.strip()}


def resolve_prompt(reference: str, override: dict | None) -> str:
    """The system prompt an agent sends: the reference, extended or replaced by the pipeline's text."""
    if not override or not override.get("text"):
        return reference
    if override.get("mode") == "replace":
        return override["text"]
    return reference.rstrip() + "\n\n# Additional instructions (custom pipeline)\n" + override["text"].strip() + "\n"


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    base: str
    settings: dict = field(default_factory=dict)
    prompts: dict = field(default_factory=dict)   # agent -> {"mode": "append"|"replace", "text": str}
    kg: str | None = None                         # JSON-LD text; None = the reference knowledge graph
    client_id: str | None = None                  # the console's id for it (shows which saved pipeline runs)
    code: str | None = None                       # Python agents (operaitor/plugins.py); own computer only

    @property
    def reference(self) -> bool:
        return not (self.settings or self.prompts or self.kg or self.code)

    def fingerprint(self) -> str:
        """Short hash of everything that changes behaviour (logged with every decision)."""
        blob = json.dumps({"base": self.base, "settings": self.settings, "prompts": self.prompts,
                           "kg": hashlib.sha256(self.kg.encode()).hexdigest() if self.kg else None,
                           **({"code": hashlib.sha256(self.code.encode()).hexdigest()} if self.code else {})},
                          sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:10]

    def public(self, kg_report: dict | None = None) -> dict:
        """What the console shows about the active pipeline (no prompt or KG text)."""
        return {"name": self.name, "base": self.base, "reference": self.reference, "fingerprint": self.fingerprint(),
                "settings": self.settings, "prompts": sorted(self.prompts), "custom_kg": self.kg is not None,
                "client_id": self.client_id, "python": self.code is not None,
                **({"kg_report": kg_report} if kg_report else {})}


def reference_spec(preset: str) -> PipelineSpec:
    return PipelineSpec(name=REFERENCE_PIPELINES.get(preset, preset), base=preset)


def _check_setting(key: str, value: Any) -> tuple[Any, str]:
    spec = SETTINGS[key]
    if "choices" in spec:
        return (value, "") if value in spec["choices"] else (None, f"{key}: choose one of {sorted(spec['choices'])}")
    kind = spec["type"]
    if kind == "bool":
        return (value, "") if isinstance(value, bool) else (None, f"{key}: must be true or false")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{key}: must be a number"
    if kind == "int" and int(value) != value:
        return None, f"{key}: must be a whole number"
    if not spec["min"] <= value <= spec["max"]:
        return None, f"{key}: must be between {spec['min']} and {spec['max']}"
    return (int(value) if kind == "int" else float(value)), ""


def parse_pipeline(raw: Any) -> tuple[PipelineSpec | None, list[str]]:
    """Validate a pipeline from the console (or a file); returns (spec, errors)."""
    if not isinstance(raw, dict):
        return None, ["a pipeline is a JSON object"]
    errors: list[str] = []
    base = raw.get("base")
    if base not in REFERENCE_PIPELINES:
        errors.append(f"base: choose one of {sorted(REFERENCE_PIPELINES)}")
    name = str(raw.get("name") or "").strip()[:MAX_NAME_CHARS] or "custom pipeline"
    settings = {}
    for key, value in (raw.get("settings") or {}).items():
        if key not in SETTINGS:
            errors.append(f"settings: unknown setting {key!r}")
            continue
        v, err = _check_setting(key, value)
        if err:
            errors.append(f"settings.{err}")
        else:
            settings[key] = v
    prompts = {}
    for agent, p in (raw.get("prompts") or {}).items():
        if agent not in PROMPT_AGENTS:
            errors.append(f"prompts: unknown agent {agent!r}; choose from {sorted(PROMPT_AGENTS)}")
            continue
        if not isinstance(p, dict) or p.get("mode", "append") not in PROMPT_MODES or not isinstance(p.get("text", ""), str):
            errors.append(f"prompts.{agent}: give {{\"mode\": \"append\" or \"replace\", \"text\": \"...\"}}")
            continue
        text = p.get("text", "").strip()
        if len(text) > MAX_PROMPT_CHARS:
            errors.append(f"prompts.{agent}: at most {MAX_PROMPT_CHARS} characters")
        elif text:
            prompts[agent] = {"mode": p.get("mode", "append"), "text": text}
    kg = raw.get("kg")
    if kg is not None and not isinstance(kg, str):
        errors.append("kg: give the JSON-LD file's text")
        kg = None
    if kg is not None and len(kg.encode()) > MAX_KG_BYTES:
        errors.append(f"kg: at most {MAX_KG_BYTES // 1_000_000} MB")
    code = raw.get("code")
    if code is not None and not isinstance(code, str):
        errors.append("code: give the Python code as text")
        code = None
    code = code if code and code.strip() else None
    from operaitor.plugins import MAX_CODE_CHARS
    if code is not None and len(code) > MAX_CODE_CHARS:
        errors.append(f"code: at most {MAX_CODE_CHARS:,} characters")
    if settings.get("validator") == "custom" and code is None:
        errors.append('settings.validator: "Your Python validator" needs Python code that defines VALIDATOR')
    if errors:
        return None, errors
    client_id = str(raw.get("id") or "")[:40] or None
    return PipelineSpec(name=name, base=base, settings=settings, prompts=prompts, kg=kg or None, client_id=client_id,
                        code=code), []


def config_for(spec: PipelineSpec, **overrides):
    """The AgentConfig a pipeline runs with: its reference preset, then its settings."""
    from .recovery_graph import make_config
    cfg = make_config(spec.base, **overrides)
    s = dict(spec.settings)
    validator = s.pop("validator", None)
    if validator is not None:
        # "custom": none of the built-in validators; the session hands the Python validator to the graph
        cfg = replace(cfg, twin=validator == "twin", judge=validator == "judge",
                      admit=validator.startswith("admit"), admit_sim=validator == "admit")
    return replace(cfg, **s)


# ----------------------------------------------------------------------------- knowledge graphs

# Node families the agents look up by id (kg.py: seeds_from, retrieve; kg_map.py; diagnosis_llm.py).
KG_FAMILIES = {"fault_": "fault modes", "sym_": "symptoms", "alarm_": "alarms", "SIF_": "safety functions",
               "controller_": "control loops", "pump_train_": "pump trains", "action_": "operator actions",
               "obs_param_": "measured variables", "act_param_": "manipulated variables"}


def load_kg(text: str):
    """Parse a JSON-LD knowledge graph; raises ValueError with a readable reason."""
    from .kg import KnowledgeGraph
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(g, dict) for g in data):
        raise ValueError("expected JSON-LD: a list of named graphs, each with an \"@graph\" list of nodes")
    if not any(isinstance(g.get("@graph"), list) for g in data):
        raise ValueError("no \"@graph\" list of nodes found (see the reference knowledge graph for the format)")
    try:
        kg = KnowledgeGraph(data=data)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"a node is malformed ({type(exc).__name__}: {exc}); every node needs an \"@id\"") from None
    if not kg.nodes:
        raise ValueError("the graph has no nodes")
    return kg


def kg_report(kg, reference) -> dict:
    """What the agents will find in a knowledge graph, compared with the reference graph."""
    counts = {label: sum(1 for n in kg.nodes if n.startswith(prefix)) for prefix, label in KG_FAMILIES.items()}
    acts_on = sum(1 for n in kg.nodes for p, _ in kg.out.get(n, []) if p == "actsOn")
    mapped = sum(1 for n in kg.nodes if kg.vmap.lookup(n))
    # The fault modes the diagnosis names (the reference graph's): retrieval is seeded from fault_<name>.
    faults = sorted(n for n in reference.nodes if n.startswith("fault_"))
    missing_faults = [f.removeprefix("fault_") for f in faults if f not in kg.nodes]
    warnings = []
    if missing_faults:
        warnings.append(f"{len(missing_faults)} of the {len(faults)} reference fault modes have no node here, so for "
                        f"them retrieval starts from alarms and variables only: "
                        + ", ".join(missing_faults[:6]) + ("…" if len(missing_faults) > 6 else ""))
    if not counts["operator actions"] or not acts_on:
        warnings.append("no operator actions linked by actsOn: the planner gets no candidate levers from the graph")
    if not mapped:
        warnings.append("no node maps to a plant variable or command (use the reference node ids, e.g. "
                        "controller_R_PIC, pump_train_REFLUX, obs_param_cstr_pressure_bar)")
    out = {"nodes": len(kg.nodes), "relations": sum(len(v) for v in kg.out.values()), "families": counts,
           "acts_on": acts_on, "mapped_to_plant": mapped, "faults_covered": len(faults) - len(missing_faults),
           "reference_faults": len(faults), "reference_nodes": len(reference.nodes), "warnings": warnings}
    return out
