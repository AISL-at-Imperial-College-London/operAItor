"""Operator chat agent: ask what things are (KG) and how the plant is moving (live data).

LangGraph tool loop with per-session memory (``InMemorySaver``, one thread per
chat session):

    START ─(LLM configured?)─► assistant ⇄ tools ─► END
                          └──► offline ─► END     (deterministic intent router)

Tools are read-only views over the live plant service, the monitoring/planning
agents' latest output and the knowledge graph. The chat agent sees what an
operator sees: measurements, controllers, alarms and agent output, never the
injected ground truth. It cannot change the plant.
"""
from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from .kg import KnowledgeGraph
from .llm import USAGE, make_llm, message_text

try:  # optional: OfflineChat works without langgraph
    from langgraph.graph.message import add_messages
except ImportError:
    add_messages = None


class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

# Friendly names → history keys (measurement tags, op.<loop>, sp.<loop> or flow/valve keys).
ALIASES: dict[str, tuple[str, ...]] = {
    "cstr_temperature_K": ("reactor temperature", "reactor temp", "t_r", "cstr temperature"),
    "cstr_pressure_bar": ("reactor pressure", "p_r", "cstr pressure"),
    "cstr_holdup_kmol": ("reactor level", "reactor holdup", "reactor inventory", "m_r"),
    "cstr_x_acetone": ("reactor acetone", "conversion", "reactor composition"),
    "cstr_h2_vent_kmol_h": ("h2 vent", "hydrogen vent", "vent flow", "h2 flow"),
    "column_pressure_bar": ("column pressure", "p_col", "condenser pressure"),
    "top_temperature_K": ("top temperature", "top temp", "t_top"),
    "bottom_temperature_K": ("bottom temperature", "bottom temp", "t_bot"),
    "reboiler_holdup_kmol": ("reboiler level", "reboiler holdup", "sump level", "m_b"),
    "drum_holdup_kmol": ("drum level", "reflux drum", "drum holdup", "m_d"),
    "distillate_x_acetone": ("distillate purity", "product purity", "distillate acetone", "x_d"),
    "bottoms_x_acetone": ("bottoms acetone", "bottoms composition", "x_bot"),
    "feed": ("feed", "throughput", "feed flow", "fresh feed"),
    "outflow": ("reactor outflow", "outlet flow", "column feed", "outflow"),
    "reflux": ("reflux flow", "reflux"),
    "distillate": ("distillate flow", "distillate"),
    "bottoms": ("bottoms flow", "bottoms"),
    "heater": ("heater", "heating valve", "jacket"),
    "steam": ("steam", "reboiler steam"),
    "cooling": ("cooling water valve", "condenser valve", "cwv", "cooling"),
    "pcv": ("pcv", "vent valve", "pressure control valve"),
    "bdv": ("bdv", "blowdown"),
}
UNITS = {"_K": "K", "_bar": "bar", "_kmol": "kmol", "_kmol_h": "kmol/h", "_acetone": "mol frac"}


def _unit(key: str) -> str:
    if key in ("feed", "outflow", "reflux", "distillate", "bottoms", "h2"):
        return "kmol/h"
    if key in ("heater", "steam", "cooling", "pcv", "bdv"):
        return "fraction open"
    for suffix, unit in sorted(UNITS.items(), key=lambda kv: -len(kv[0])):
        if key.endswith(suffix):
            return unit
    return ""


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return "n/a" if not math.isfinite(v) else f"{v:.4g}"
    return str(v)


class PlantTools:
    """Read-only tool implementations; ``service`` is a server.plant_service.PlantService."""

    def __init__(self, service, kg: KnowledgeGraph, agent_info: Callable[[], dict] | None = None):
        self.service, self.kg = service, kg
        self.agent_info = agent_info or (lambda: {})

    # ------------------------------------------------------------ variable resolution
    def keys(self) -> list[str]:
        hist = self.service.history
        return list(hist[-1].keys() - {"t"}) if hist else []

    def resolve(self, name: str) -> str | None:
        keys = self.keys()
        n = name.strip().lower().replace("-", "_")
        for k in keys:
            if k.lower() == n or k.lower() == n.replace(" ", "_"):
                return k
        loop = n.upper()
        if f"op.{loop}" in keys:  # a loop tag means its measured PV
            pv = self.service.snapshot()["controllers"][loop]["pv_tag"]
            return pv if pv in keys else f"op.{loop}"
        best, best_len = None, 0
        for key, aliases in ALIASES.items():
            for a in aliases:
                if re.search(rf"\b{re.escape(a)}\b", n) and len(a) > best_len and key in keys:
                    best, best_len = key, len(a)
        if best:
            return best
        m = re.search(r"tray\s*_?(\d+)", n)
        if m:
            kind = "pressure_bar" if "pressure" in n else "temperature_K"
            k = f"tray_{int(m.group(1)):02d}_{kind}"
            return k if k in keys else None
        hits = [k for k in keys if n.replace(" ", "_") in k.lower()]
        return hits[0] if hits else None

    # ------------------------------------------------------------ tools
    def plant_status(self) -> dict:
        s = self.service.snapshot()
        mon = self.service.monitor[-1] if self.service.monitor else None
        return {"time_min": round(s["time_min"], 1), "running": s["running"], "episode": s["episode"],
                "episode_status": s["episode_status"],
                "active_alarms": [f"{a['id']} ({a['description']}, {a['state']}, value {_fmt(a['value'])} vs limit {a['limit']} {a['unit']})"
                                  for a in s["alarms"] if a["state"] != "NORMAL"],
                "failed_pump_trains": [t for t, p in s["pumps"].items() if p["selected_failed"]],
                "sif_latched": s["sif_latched"], "plant_trip": s["plant_trip"],
                "monitoring": ({"fault": mon["fault"], "diagnosis": mon["diagnosis"]["summary"]} if mon else "no cycle yet"),
                "agents": self.agent_info()}

    def current_values(self, variables: list[str] | None = None) -> dict:
        row = self.service.history[-1] if self.service.history else {}
        names = variables or ["reactor temperature", "reactor pressure", "reactor level", "column pressure",
                              "top temperature", "bottom temperature", "drum level", "reboiler level",
                              "distillate purity", "feed"]
        out = {}
        for name in names:
            key = self.resolve(name)
            out[name] = ({"variable": key, "value": row.get(key), "unit": _unit(key)} if key
                         else {"error": f"unknown variable; try list_variables"})
        return {"time_min": row.get("t"), "values": out}

    def trend(self, variable: str, minutes: float = 15.0) -> dict:
        key = self.resolve(variable)
        if key is None:
            return {"error": f"unknown variable {variable!r}; try list_variables"}
        hist = [r for r in self.service.history if r.get(key) is not None]
        if not hist:
            return {"error": "no data yet"}
        t_end = hist[-1]["t"]
        pts = [(r["t"], float(r[key])) for r in hist if r["t"] >= t_end - minutes]
        t0, v0 = pts[0]
        t1, v1 = pts[-1]
        vals = [v for _, v in pts]
        n = len(pts)
        if n > 2:  # least-squares slope
            tm, vm = sum(t for t, _ in pts) / n, sum(vals) / n
            den = sum((t - tm) ** 2 for t, _ in pts) or 1.0
            slope = sum((t - tm) * (v - vm) for t, v in pts) / den
        else:
            slope = (v1 - v0) / max(t1 - t0, 1e-9)
        span = (max(vals) - min(vals)) or abs(v1) * 1e-3 or 1e-9
        rel = slope * max(t1 - t0, 1e-9) / span
        direction = "rising" if rel > 0.5 and v1 > v0 else "falling" if rel < -0.5 and v1 < v0 else "roughly steady"
        step = max(1, n // 10)
        loop_sp = None
        for loop, info in self.service.snapshot()["controllers"].items():
            if info["pv_tag"] == key:
                loop_sp = {"loop": loop, "setpoint": info["setpoint"], "output": info["last_output"], "mode": info["mode"]}
        return {"variable": key, "unit": _unit(key), "window_min": round(t1 - t0, 1),
                "first": round(v0, 5), "last": round(v1, 5), "min": round(min(vals), 5), "max": round(max(vals), 5),
                "slope_per_min": slope, "direction": direction,
                "samples": [[round(t, 1), round(v, 5)] for t, v in pts[::step]], **({"controller": loop_sp} if loop_sp else {})}

    def fault_diagnosis(self) -> dict:
        if not self.service.monitor:
            return {"note": "the monitoring agent has not run yet (is the plant running and are agents enabled?)"}
        m = self.service.monitor[-1]
        pca = m.get("pca") or {}
        briefs = [e for e in self.service.agent_log if e.get("stage") == "plan"]
        return {"time_min": m["time_min"], "fault": m["fault"], "detectors": m.get("detectors"),
                "pca": {k: pca.get(k) for k in ("t2", "t2_limit", "spe", "spe_limit")} if pca else None,
                "pca_persistent": m.get("pca_persistent"), "rules": m.get("rules"),
                "hypotheses": m["diagnosis"]["hypotheses"], "top_contributors": m["diagnosis"].get("contributors", [])[:5],
                "latest_plan_brief": briefs[-1].get("brief") if briefs else None}

    def agent_activity(self, last_n: int = 10) -> dict:
        events = [e for e in self.service.agent_log if e.get("stage") not in ("kg",)]
        out = []
        for e in events[-last_n:]:
            row = {k: e.get(k) for k in ("time_min", "stage", "plan", "errors", "result", "fail_reason", "accepted")
                   if e.get(k) is not None}
            if e.get("brief"):
                row["plan_target"] = e["brief"].get("target_fault")
                row["objective"] = e["brief"].get("objective")
            out.append(row)
        return {"events": out}

    def knowledge_graph(self, query: str) -> dict:
        hits = self.kg.search(query)
        if not hits:
            return {"error": f"nothing in the KG matches {query!r}"}
        top = hits[0]
        return {"match": self.kg.compact(top), "in_simulation": self.kg.vmap.lookup(top),
                "related": self.kg.neighbours(top)[:15], "other_matches": hits[1:]}

    def list_variables(self) -> dict:
        return {"variables": sorted(self.keys()), "aliases": {k: v[0] for k, v in ALIASES.items()}}

    def as_tools(self) -> list[StructuredTool]:
        specs = [
            (self.plant_status, "plant_status", "Overall live status: time, alarms, failed pumps, SIF latches, monitoring verdict, agent mode."),
            (self.current_values, "current_values", "Latest values of named variables (friendly names like 'reactor pressure' or tags like 'cstr_pressure_bar'). Omit to get key variables."),
            (self.trend, "trend", "How a variable has moved over the last `minutes` of simulated time: first/last/min/max, slope per minute, direction, samples, and its controller setpoint if it has one."),
            (self.fault_diagnosis, "fault_diagnosis", "Latest monitoring-agent output: PCA T²/SPE vs limits, rule evidence, ranked fault hypotheses, top contributing variables and the planning agent's latest brief."),
            (self.agent_activity, "agent_activity", "Recent planning/action-agent events: proposals, twin verdicts, applied or pending actions."),
            (self.knowledge_graph, "knowledge_graph", "Look up equipment, loops, alarms, SIFs, fault modes or operator actions in the plant knowledge graph (e.g. 'R_PIC', 'P-201A', 'condenser cooling loss')."),
            (self.list_variables, "list_variables", "All variable names available to current_values and trend."),
        ]
        return [StructuredTool.from_function(fn, name=name, description=desc) for fn, name, desc in specs]


CHAT_PROMPT = """
You are the operator assistant for a live simulated isopropanol→acetone plant: an
endothermic CSTR (R-101) with H2 vent, feeding a 10-tray distillation column (T-201),
with five duty/standby pump trains and regulatory loops R_LIC, R_TIC, R_PIC, COL_PIC,
TOP_TIC, BOT_TIC, RB_LIC and DR_LIC. Times are simulated minutes.

Answer questions about what things are (use `knowledge_graph`; cite node ids) and how
the plant is moving (use `trend`, `current_values`, `plant_status`). For "why" or
"what is wrong" questions use `fault_diagnosis` and `agent_activity`. Never invent
numbers: call a tool. Give values with units, keep answers short, and say when data
is missing. You cannot operate the plant; point the user to the operator panel or
the agents for actions.
""".strip()


def _offline_answer(tools: PlantTools, text: str) -> str:
    """Deterministic intent router used when no LLM is configured."""
    t = text.lower().strip()
    minutes = float(m.group(1)) if (m := re.search(r"(\d+(?:\.\d+)?)\s*min", t)) else 15.0

    def describe_kg(query: str) -> str:
        r = tools.knowledge_graph(query)
        if "error" in r:
            return r["error"] + "."
        n = r["match"]
        rel = "; ".join(f"{x['relation']} {'→' if x['direction'] == 'out' else '←'} {x['label'] or x['node']}"
                        for x in r["related"][:8])
        others = f"\nOther matches: {', '.join(r['other_matches'])}" if r["other_matches"] else ""
        sim = r.get("in_simulation") or {}
        sim_txt = ""
        if sim:
            bits = [f"{k}: {v}" for k, v in sim.items() if k in ("sim", "pv", "mv", "controlled_by", "manipulated_by",
                                                                   "pump_train", "auxiliary", "alarms", "loops")]
            cmd = sim.get("command")
            sim_txt = "\nIn the simulation: " + "; ".join(bits) + (f"; command: {cmd}" if cmd else "")
        return (f"**{n['label'] or n['id']}** (`{n['id']}`, {', '.join(n['type'])})\n{n['comment']}"
                + sim_txt + (f"\nRelations: {rel}" if rel else "") + others)

    def describe_trend(name: str) -> str | None:
        r = tools.trend(name, minutes)
        if "error" in r:
            return None
        ctrl = r.get("controller")
        sp = (f" {ctrl['loop']} is in {ctrl['mode']} with setpoint {_fmt(ctrl['setpoint'])} and output {_fmt(ctrl['output'])}."
              if ctrl else "")
        return (f"`{r['variable']}` over the last {r['window_min']} min: {r['direction']}, "
                f"{_fmt(r['first'])} → {_fmt(r['last'])} {r['unit']} (min {_fmt(r['min'])}, max {_fmt(r['max'])}, "
                f"slope {r['slope_per_min']:+.3g} {r['unit']}/min).{sp}")

    def describe_diagnosis() -> str:
        d = tools.fault_diagnosis()
        if "note" in d:
            return d["note"]
        pca = d["pca"] or {}
        head = "A fault is detected." if d["fault"] else "No fault is currently detected."
        pca_txt = (f" PCA T² {_fmt(pca.get('t2'))} (limit {_fmt(pca.get('t2_limit'))}), "
                   f"SPE {_fmt(pca.get('spe'))} (limit {_fmt(pca.get('spe_limit'))})." if pca else "")
        hyps = "\n".join(f"{i + 1}. {h['fault']}: {'; '.join(h['evidence'][:2])}" for i, h in enumerate(d["hypotheses"][:3]))
        contrib = ", ".join(c["variable"] for c in d["top_contributors"][:4])
        brief = d.get("latest_plan_brief")
        plan = f"\nPlanning agent objective: {brief['objective']}" if brief else ""
        return (head + pca_txt + (f"\nHypotheses:\n{hyps}" if hyps else "")
                + (f"\nTop contributing variables: {contrib}" if contrib else "") + plan)

    if re.search(r"\b(what is|what's|what are|what does|explain|describe|meaning of|tell me about)\b", t):
        subject = re.split(r"what is|what's|what are|what does|explain|describe|meaning of|tell me about", t, maxsplit=1)[-1]
        subject = re.sub(r"\b(do|mean|the|a|an)\b|\?", " ", subject).strip()
        if re.search(r"\b(doing|now|value)\b", t) and (ans := describe_trend(subject)):
            return ans
        return describe_kg(subject or t)
    if re.search(r"\b(why|fault|wrong|diagnos\w*|hypothes\w*|problem|abnormal)\b", t):
        return describe_diagnosis()
    if re.search(r"\b(trend|moving|changing|going|rising|falling|doing|how is|how's|drift\w*)\b", t):
        subject = re.sub(r"\b(how is|how's|the|trend|of|moving|changing|going|doing|over|last|\d+\s*min\w*)\b|\?", " ", t)
        if (ans := describe_trend(subject.strip())):
            return ans
    if "alarm" in t or re.search(r"\b(status|overview|summary)\b", t):
        s = tools.plant_status()
        alarms = "\n".join(f"- {a}" for a in s["active_alarms"]) or "none"
        mon = s["monitoring"]
        mon_txt = mon if isinstance(mon, str) else ("FAULT: " + mon["diagnosis"] if mon["fault"] else "normal")
        return (f"t = {s['time_min']} min, episode `{s['episode']}` ({s['episode_status']}). "
                f"Monitoring: {mon_txt}. Failed pumps: {', '.join(s['failed_pump_trains']) or 'none'}. "
                f"SIF latched: {', '.join(s['sif_latched']) or 'none'}.\nActive alarms:\n{alarms}")
    if re.search(r"\b(agent|action|did|recommend\w*|plan|proposal)\b", t):
        ev = tools.agent_activity(6)["events"]
        if not ev:
            return "The agents have not proposed anything yet."
        return "Recent agent activity:\n" + "\n".join(
            f"- {e.get('time_min', 0):.1f} min {e['stage']}: "
            + (f"target {e['plan_target']}" if e.get("plan_target") else
               json.dumps(e.get("plan", {}).get("commands")) if e.get("plan") else
               e.get("result", {}).get("summary", "") if e.get("result") else e.get("fail_reason") or "")
            for e in ev)
    if (key := tools.resolve(t)) and (ans := describe_trend(key)):
        return ans
    if tools.kg.search(t):
        return describe_kg(t)
    return ("I can answer: *what is X* (knowledge graph), *how is X moving* (trends), *why is there a fault* "
            "(monitoring agent), *alarms / status*, and *what did the agents do*. Configure an LLM for free-form chat.")


class ChatAgent:
    def __init__(self, service, kg: KnowledgeGraph, llm_model: str | None = None, llm: Any = None,
                 agent_info: Callable[[], dict] | None = None):
        self.tools = PlantTools(service, kg, agent_info)
        self.tool_list = self.tools.as_tools()
        self.by_name = {t.name: t for t in self.tool_list}
        self.llm = llm
        self.model = llm_model or "custom"
        self.llm_error = ""
        if self.llm is None and llm_model:
            try:
                self.llm = make_llm(llm_model)
            except RuntimeError as exc:
                self.llm_error = str(exc)
        self.graph = self._build()

    @property
    def mode(self) -> str:
        return "llm" if self.llm is not None else "offline"

    def _build(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph

        bound = self.llm.bind_tools(self.tool_list) if self.llm is not None else None

        async def assistant(state: ChatState) -> dict:
            reply = await bound.ainvoke([SystemMessage(CHAT_PROMPT), *state["messages"]])
            USAGE.record("chat", self.model, reply)
            return {"messages": [reply]}

        async def tools(state: ChatState) -> dict:
            out = []
            for call in state["messages"][-1].tool_calls:
                tool = self.by_name.get(call["name"])
                try:
                    result = tool.invoke(call.get("args") or {}) if tool else {"error": f"unknown tool {call['name']}"}
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                out.append(ToolMessage(json.dumps(result, default=str)[:8000], tool_call_id=call["id"], name=call["name"]))
            return {"messages": out}

        async def offline(state: ChatState) -> dict:
            text = message_text(state["messages"][-1])
            return {"messages": [AIMessage(_offline_answer(self.tools, text))]}

        def route_start(_: ChatState) -> str:
            return "assistant" if bound is not None else "offline"

        def route_assistant(state: ChatState) -> str:
            return "tools" if getattr(state["messages"][-1], "tool_calls", None) else "end"

        g = StateGraph(ChatState)
        g.add_node("assistant", assistant)
        g.add_node("tools", tools)
        g.add_node("offline", offline)
        g.add_conditional_edges(START, route_start, {"assistant": "assistant", "offline": "offline"})
        g.add_conditional_edges("assistant", route_assistant, {"tools": "tools", "end": END})
        g.add_edge("tools", "assistant")
        g.add_edge("offline", END)
        return g.compile(checkpointer=InMemorySaver())

    async def ask(self, text: str, session: str = "default") -> dict:
        cfg = {"configurable": {"thread_id": session}, "recursion_limit": 16}
        before = len((await self.graph.aget_state(cfg)).values.get("messages", []))
        try:
            state = await self.graph.ainvoke({"messages": [HumanMessage(text)]}, cfg)
        except Exception as exc:
            return {"reply": f"Chat failed: {type(exc).__name__}: {exc}", "tools": [], "mode": self.mode}
        new = state["messages"][before:]
        used = [c["name"] for m in new for c in (getattr(m, "tool_calls", None) or [])]
        return {"reply": message_text(new[-1]), "tools": used, "mode": self.mode,
                "model": self.model if self.llm is not None else None}


class OfflineChat:
    """Same tools and intent router without langgraph (used if it is not installed)."""
    mode = "offline"

    def __init__(self, service, kg: KnowledgeGraph, agent_info: Callable[[], dict] | None = None):
        self.tools = PlantTools(service, kg, agent_info)

    async def ask(self, text: str, session: str = "default") -> dict:
        return {"reply": _offline_answer(self.tools, text), "tools": [], "mode": self.mode}
