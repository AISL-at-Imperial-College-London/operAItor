"""Integrated plant knowledge graph: diagnosis-seeded retrieval and free-text search.

Reads knowledge_graph/Integrated_CSTR_Distillation_Knowledge_Graph.jsonld
directly (no GraphDB). Node ids are the local names of the KG IRIs, e.g.
``fault_reflux_pump_trip``, ``controller_R_PIC``, ``alarm_R_L_HH``, ``P-201A``.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

KG_PATH = Path(__file__).resolve().parent.parent / "knowledge_graph" / "Integrated_CSTR_Distillation_Knowledge_Graph.jsonld"

_SKIP_PREFIXES = ("DE_", "TD_", "ID_", "information_")      # data-element plumbing
_USEFUL_TYPES = ("FaultMode", "Symptom", "Alarm", "SafetyInterlock", "OverrideRule",
                 "OperatorAction", "PumpTrain", "ProcessOperator", "ActuatableProperty",
                 "ObservableProperty", "Actuator", "Sensor", "Controller", "Module", "Component")


def _short(uri: str) -> str:
    return uri.split("#")[-1].split("/")[-1]


class KnowledgeGraph:
    def __init__(self, path: Path = KG_PATH):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.nodes: dict[str, dict] = {}
        for graph in data:
            for n in graph.get("@graph", []):
                self.nodes[_short(n["@id"])] = n
        self.out: dict[str, list[tuple[str, str]]] = {k: [] for k in self.nodes}
        self.inc: dict[str, list[tuple[str, str]]] = {k: [] for k in self.nodes}
        for sid, n in self.nodes.items():
            for pred, vals in n.items():
                if pred.startswith("@"):
                    continue
                for v in vals if isinstance(vals, list) else [vals]:
                    if isinstance(v, dict) and "@id" in v and _short(v["@id"]) in self.nodes:
                        oid = _short(v["@id"])
                        self.out[sid].append((_short(pred), oid))
                        self.inc[oid].append((_short(pred), sid))
        self._search_text = {nid: f"{nid} {self.label(nid)} {self.comment(nid)}".lower() for nid in self.nodes}

    # ------------------------------------------------------------ node views
    def types(self, nid: str) -> list[str]:
        t = self.nodes[nid].get("@type", [])
        return [_short(x) for x in (t if isinstance(t, list) else [t])]

    def _text(self, nid: str, key: str) -> str:
        for k, v in self.nodes[nid].items():
            if k.endswith(key):
                vals = v if isinstance(v, list) else [v]
                return " ".join(str(x.get("@value", "")) for x in vals if isinstance(x, dict))
        return ""

    def label(self, nid: str) -> str:
        return self._text(nid, "label")

    def comment(self, nid: str) -> str:
        return self._text(nid, "comment")

    def useful(self, nid: str) -> bool:
        return not nid.startswith(_SKIP_PREFIXES) and (
            any(t in _USEFUL_TYPES for t in self.types(nid))
            or nid.startswith(("act_param_", "obs_param_", "controller_", "pump_train_", "SIF_", "alarm_",
                               "fault_", "sym_", "action_", "PO_", "module_")))

    def compact(self, nid: str) -> dict:
        n = self.nodes[nid]
        literals = {}
        for k, v in n.items():
            if k.startswith("@") or k.endswith(("label", "comment", "sourceFile", "sourceSymbol")):
                continue
            vals = [x.get("@value") for x in (v if isinstance(v, list) else [v])
                    if isinstance(x, dict) and "@value" in x]
            if vals:
                literals[_short(k)] = vals[0] if len(vals) == 1 else vals
        rels: dict[str, list[str]] = {}
        for pred, oid in self.out[nid]:
            if self.useful(oid):
                rels.setdefault(pred, []).append(oid)
        return {"id": nid, "type": [t for t in self.types(nid) if not t.startswith(("Type_", "Instance_"))],
                "label": self.label(nid), "comment": self.comment(nid),
                **({"values": literals} if literals else {}), **({"relations": rels} if rels else {})}

    def neighbours(self, nid: str) -> list[dict]:
        """Labelled one-hop relations in both directions (for chat answers)."""
        rows = [{"relation": p, "direction": "out", "node": o, "label": self.label(o)}
                for p, o in self.out.get(nid, []) if self.useful(o)]
        rows += [{"relation": p, "direction": "in", "node": s, "label": self.label(s)}
                 for p, s in self.inc.get(nid, []) if self.useful(s)]
        return rows

    # ------------------------------------------------------------ retrieval for agents
    def seeds_from(self, diagnosis: dict, rules: dict, use_contributors: bool = True) -> list[str]:
        seeds = [f"fault_{h['fault']}" for h in diagnosis.get("hypotheses", [])[:3]]
        seeds += [f"alarm_{a}" for a in rules.get("active_alarms", [])]
        seeds += [f"controller_{loop}" for loop in rules.get("saturated_loops", [])]
        seeds += [f"pump_train_{t}" for t in rules.get("failed_trains", [])]
        for c in diagnosis.get("contributors", [])[:3] if use_contributors else []:
            var = c["variable"]
            seeds.append(f"obs_param_{var[5:]}" if var.startswith("meas.") else f"controller_{var[3:]}")
        return [s for s in dict.fromkeys(seeds) if s in self.nodes]

    def retrieve(self, diagnosis: dict, rules: dict, use_contributors: bool = True,
                 hops: int = 1, limit: int = 30) -> list[dict]:
        """Typed walk from the diagnosis seeds, plus the operator actions that act on it."""
        seeds = self.seeds_from(diagnosis, rules, use_contributors)
        found, frontier = list(seeds), list(seeds)
        for _ in range(hops):
            nxt = []
            for nid in frontier:
                for _, other in self.out.get(nid, []) + self.inc.get(nid, []):
                    if other not in found and self.useful(other):
                        found.append(other)
                        nxt.append(other)
            frontier = nxt
        for nid in list(found):
            for pred, src in self.inc.get(nid, []):
                if pred == "actsOn" and src not in found:
                    found.append(src)
        found = [n for n in found if not n.startswith("override_")] + [n for n in found if n.startswith("override_")]
        return [self.compact(n) for n in found[:limit]]

    # ------------------------------------------------------------ free-text search for chat
    def search(self, query: str, limit: int = 5) -> list[str]:
        """Rank nodes for a free-text question ("what is R_PIC", "reflux pump")."""
        q = query.strip()
        if q in self.nodes:
            return [q]
        # Plant tags keep their _ and - (R_PIC, P-201A, R_L_HH): exact tag hits rank first.
        tags = [t for t in re.split(r"[^a-z0-9_\-]+", q.lower()) if t and t not in _STOP]
        exact = {n for n in self.nodes for t in tags if len(t) > 2 and (n.lower() == t or n.lower().endswith("_" + t))}
        terms = {t for t in re.split(r"[^a-z0-9]+", " ".join(tags)) if len(t) > 1 and t not in _STOP}
        scored = []
        for nid, text in self._search_text.items():
            if not self.useful(nid):
                continue
            score = sum(3 if re.search(rf"\b{re.escape(t)}\b", nid.lower()) else 1 for t in terms if t in text)
            if score or nid in exact:
                scored.append((score + (10 if nid in exact else 0), -len(nid), nid))
        scored.sort(reverse=True)
        return [n for *_, n in scored[:limit]]


_STOP = {"what", "is", "the", "a", "an", "of", "does", "do", "how", "about", "tell", "me", "explain",
         "describe", "which", "are", "for", "and", "in", "on", "to", "it", "this", "that"}
