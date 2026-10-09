import { Fragment } from "react";
import { usePlant, type NodeStatus } from "../store";
import type { AgentEvent } from "../types";

type Step = { id: string; title: string; sub: string };
const BASE: Step[] = [
  { id: "monitoring", title: "Monitoring", sub: "PCA T² / SPE confirms a fault" },
  { id: "kg_retrieval", title: "Knowledge graph", sub: "fault → symptoms → loops → actions" },
  { id: "planning", title: "Planning", sub: "target, objective, levers" },
  { id: "action_propose", title: "Action agent", sub: "proposes commands" },
];
const DISPATCH: Step = { id: "dispatch", title: "Dispatch", sub: "apply to the plant" };

/** The steps after the action agent depend on the agent configuration. */
function flow(preset: string | undefined): Step[] {
  if (preset?.startsWith("llm_admit")) {
    const sim = preset === "llm_admit";
    return [...BASE, { id: "twin_evaluate", title: "ADMIT gate",
      sub: sim ? "record, evidence, authority, consequence" : "record, evidence, authority (no simulation)" }, DISPATCH];
  }
  if (preset === "llm_judge") return [...BASE, { id: "twin_evaluate", title: "LLM judge", sub: "a second LLM reviews" }, DISPATCH];
  if (preset === "llm_lucky" || preset === "llm_notwin") return [...BASE, DISPATCH];
  if (preset === "passive" || !preset) return [BASE[0]];
  return [...BASE, { id: "twin_evaluate", title: "Digital twin", sub: "simulate the action ahead" },
          { id: "validate", title: "Validate", sub: "faulty variables back in limits" }, DISPATCH];
}

const STATUS_TEXT: Record<NodeStatus, string> = {
  idle: "", running: "running…", ok: "passed", warn: "partial", fail: "failed", skip: "skipped",
};
const GATES = ["T0", "T1", "T2", "T3", "T4"];
const GATE_TIP: Record<string, string> = {
  T0: "T0 record integrity", T1: "T1 evidence", T2: "T2 hazard", T3: "T3 authority / mode / SOP steps", T4: "T4 consequence (projected on the plant)",
};

/** T0–T4 lamps for the latest ADMIT check of this decision (all commands combined). */
function GateLamps({ ev }: { ev: AgentEvent | undefined }) {
  const state = (tier: string) => {
    if (!ev?.records?.length) return "";
    const gs = ev.records.map((r) => r.gates?.find((g) => g.tier === tier)).filter(Boolean);
    if (!gs.length) return "na";
    if (gs.some((g) => g!.skipped)) return "skip";
    return gs.every((g) => g!.passed) ? "pass" : "fail";
  };
  return (
    <span className="gate-lamps" aria-label="ADMIT gates">
      {GATES.map((t) => <i key={t} className={state(t)} title={`${GATE_TIP[t]}: ${state(t) || "not run yet"}`}>{t}</i>)}
    </span>
  );
}

export function PipelineCard() {
  const p = usePlant((s) => s.pipeline);
  const agent = usePlant((s) => s.agent);
  const held = usePlant((s) => s.snap?.held);
  const log = usePlant((s) => s.agentLog);
  const steps = flow(agent?.preset);
  const admit = agent?.preset?.startsWith("llm_admit");
  const lastAdmit = [...log].reverse().find((e) => e.stage === "admit" && e.cycle === p.cycle);
  const passive = steps.length === 1;
  const openTrace = () => {
    const el = document.getElementById("admit-card") as HTMLDetailsElement | null;
    if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth", block: "start" }); }
  };
  return (
    <section className="card pipeline-card">
      <h3>Agent pipeline
        <span className="dim small">
          {p.cycle ? `decision #${p.cycle} at ${p.t?.toFixed(1)} min` : "waiting for a confirmed fault"}
          {p.outcome ? ` → ${p.outcome}` : p.cycle ? " → in progress" : ""}
          {p.attempts > 1 ? ` · ${p.attempts} proposals` : ""}
        </span>
        <span className="spacer" />
        {held && <span className="pill pill-warn" title="Plant paused until the agents finish this decision">plant held</span>}
      </h3>
      <div className="flow" role="list">
        {steps.map((s, i) => {
          const st = p.status[s.id] ?? "idle";
          const isAdmit = admit && s.id === "twin_evaluate";
          return (
            <Fragment key={s.id}>
              {i > 0 && <span className="arrow" aria-hidden>→</span>}
              <div role="listitem" className={`node ${st} ${isAdmit ? "admit-node" : ""}`} title={s.sub}
                onClick={isAdmit ? openTrace : undefined}>
                <b>{s.title}</b>
                <span className="sub">{s.sub}</span>
                {isAdmit && <GateLamps ev={lastAdmit} />}
                <span className="state">{isAdmit && lastAdmit ? (lastAdmit.admissible ? "admitted" : "blocked") : STATUS_TEXT[st]}</span>
                {isAdmit && <span className="link small">see what it checked ↓</span>}
              </div>
            </Fragment>
          );
        })}
      </div>
      {passive ? (
        <p className="dim small">Agents monitor and diagnose only; they take no actions in this configuration.</p>
      ) : (
        <div className="loop">
          <span className={`node small ${p.status.reprompt ?? "idle"}`}>
            ↺ <b>Reprompter</b> {agent?.reprompt_max ? `up to ${agent.reprompt_max} retries` : "off"}
            {p.attempts > 1 ? ` · attempt ${p.attempts}` : ""}
          </span>
          <span className="dim small">
            {admit ? "ADMIT blocks → violation codes and fixes go back to the action agent"
              : "rejected → feedback to the action agent"}
            {agent?.fallback_to_safe && agent.twin ? " · retries exhausted → best safe candidate applied" : ""}
          </span>
        </div>
      )}
    </section>
  );
}
