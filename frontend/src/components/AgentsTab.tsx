import { useEffect, useState, type ReactNode } from "react";
import { send, usePlant } from "../store";
import type { AgentEvent } from "../types";
import { AdmitCard } from "./AdmitPanel";
import { LlmKeyCard } from "./LlmKey";
import { Chart } from "./Chart";
import { PipelineCard } from "./Pipeline";
import { C } from "./Trends";

const pretty = (v: string) => v.replace(/^meas\./, "").replace(/^op\./, "OP ").replace(/_/g, " ");
const cmdText = (c: Record<string, unknown>) => Object.values(c).join(" ");

/** One line per agent event, for the timeline. */
export function describeEvent(e: AgentEvent): ReactNode {
  switch (e.stage) {
    case "plan": return e.brief ? <><b>target {e.brief.target_fault}</b> <span className="dim">— {e.brief.objective}</span></> : null;
    case "kg": return <span className="dim">{e.nodes?.length ?? 0} knowledge-graph nodes</span>;
    case "rejected": return <span className="bad-text">gave up: {e.reason}</span>;
    case "twin_skipped": return <span className="dim">validation skipped: {e.reason}</span>;
    case "twin": return <span className={e.restored ? "" : "warn-text"}>digital twin: {e.restored
      ? `faulty variables back within limits after ${e.time_to_restore_min?.toFixed(1)} min`
      : e.safe_reached ? "keeps the plant safe, still faulty" : "does not keep the plant safe"}</span>;
    case "verification": return <span className="dim">{e.reason}</span>;
    case "llm_diagnosis": return <span>diagnosis: <b>{e.most_likely ?? "unclear"}</b> <span className="dim">— {e.summary ?? e.error}</span></span>;
    case "admit": {
      const bad = (e.records ?? []).filter((r) => !r.admissible);
      return <span className={e.admissible ? "" : "warn-text"}>ADMIT {e.admissible ? "admitted" : "blocked"}
        {bad.map((r) => ` · ${r.action}: ${r.violations.map((v) => v.code).join(", ")}`).join("")}</span>;
    }
    case "judge": return <span className={e.accept ? "" : "warn-text"}>LLM judge {e.accept ? "accepted" : "rejected"} <span className="dim">— {e.reason}</span></span>;
    case "alarm_ack": return <span className="dim">acknowledged {(e.alarms ?? []).join(", ")}</span>;
    case "llm_error": return <span className="bad-text">LLM call failed, nothing applied: {e.error}</span>;
    case "validation": {
      const bad = e.checks?.filter((c) => !c.ok) ?? [];
      return <span className={e.accepted ? "" : "warn-text"}>{e.accepted ? "accepted" : `rejected: ${bad[0]?.check} — ${bad[0]?.detail}`}</span>;
    }
    case "fallback": return <span className="warn-text">{e.reason}: {e.plan?.commands.map(cmdText).join(", ")}</span>;
    case "reprompt": return <span className="warn-text">retry {e.attempt}/{e.reprompt_max}: {String(e.feedback?.fail_reason ?? "")}</span>;
    default:
      if (e.plan) return <><b>{e.plan.commands.map(cmdText).join(", ") || "(empty)"}</b> <span className="dim">— {e.plan.reasoning}</span></>;
      if (e.errors) return <span className="bad-text">{e.errors.join("; ")}</span>;
      return null;
  }
}

/** Collapsible section with a consistent header. */
function Section({ title, hint, open = false, children, aside }:
  { title: string; hint?: string; open?: boolean; children: ReactNode; aside?: ReactNode }) {
  return (
    <details className="section card" open={open}>
      <summary><span className="section-name">{title}</span>{hint && <span className="dim small"> {hint}</span>}
        <span className="spacer" />{aside}</summary>
      <div className="section-body">{children}</div>
    </details>
  );
}

/** Which agent configuration this session runs (validator after the action agent). */
function ConfigSelect() {
  const session = usePlant((s) => s.session);
  const agent = usePlant((s) => s.agent);
  const presets = session?.llm.presets;
  if (!presets) return null;
  const on = session.llm.active;
  if (!on) return <span className="dim small">needs an LLM (connect one below)</span>;
  return (
    <label className="inline-field" title={on ? "How each proposed action is checked before it reaches the plant" : "Needs an LLM (below)"}>
      <select value={on ? (agent?.preset ?? session.llm.preset) : ""} disabled={!on}
        onChange={(e) => send({ type: "set_agent_preset", preset: e.target.value })}>
        {!on && <option value="">no LLM</option>}
        {Object.entries(presets).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
    </label>
  );
}

const MODE_TEXT: Record<string, [string, string]> = {
  off: ["Off", "The agents do nothing"],
  advisory: ["Suggest", "The agents propose actions; you approve or dismiss each one"],
  autonomous: ["Act on their own", "The agents apply validated actions to the plant"],
};

function ModeSwitch() {
  const agent = usePlant((s) => s.agent);
  if (!agent) return null;
  return (
    <div className="seg small" role="radiogroup" aria-label="Agent mode">
      {agent.modes.map((m) => (
        <button key={m} role="radio" aria-checked={agent.mode === m} className={agent.mode === m ? "on" : ""}
          title={MODE_TEXT[m]?.[1]} onClick={() => send({ type: "agent_mode", mode: m })}>{MODE_TEXT[m]?.[0] ?? m}</button>
      ))}
    </div>
  );
}

/** Every agent setting in one place: what the agents may do, how actions are checked, the LLM behind them. */
function AgentSettings() {
  const agent = usePlant((s) => s.agent);
  const snap = usePlant((s) => s.snap);
  const session = usePlant((s) => s.session);
  const on = !!session?.llm.active;
  return (
    <section className="card settings">
      <h3>Agent settings</h3>
      <div className="settings-grid">
        <span className="settings-label">The agents</span>
        <div><ModeSwitch /> <span className="dim small">{agent ? MODE_TEXT[agent.mode]?.[1] : ""}</span></div>
        <span className="settings-label">Check each action with</span>
        <div><ConfigSelect /></div>
        <span className="settings-label">While they decide</span>
        <label className="check"><input type="checkbox" checked={snap?.hold_for_agents ?? true}
          onChange={(e) => send({ type: "hold_for_agents", on: e.target.checked })} /> pause the plant</label>
      </div>
      {on ? <LlmKeyCard /> : (
        <details className="sub connect-llm" open={!on}>
          <summary>{session?.llm.reason ? `No LLM: ${session.llm.reason}` : "No LLM connected"}
            <span className="dim small"> · without one the agents only monitor and diagnose</span></summary>
          <LlmKeyCard />
        </details>
      )}
    </section>
  );
}

/** Where the current (latest) decision stands: diagnosis → plan → proposal → validation → outcome. */
function CurrentDecision() {
  const log = usePlant((s) => s.agentLog);
  const p = usePlant((s) => s.pipeline);
  const agent = usePlant((s) => s.agent);
  const last = usePlant((s) => s.lastMonitor);
  const usage = usePlant((s) => s.llmUsage);
  const session = usePlant((s) => s.session);
  const inCycle = log.filter((e) => e.cycle === p.cycle && p.cycle > 0);
  const find = (stage: string) => [...inCycle].reverse().find((e) => e.stage === stage);
  const diag = find("llm_diagnosis");
  const plan = find("plan");
  const proposal = find("proposal");
  const verdict = [...inCycle].reverse().find((e) => ["admit", "judge", "twin", "twin_skipped"].includes(e.stage));
  const llm = agent?.llm_model;
  return (
    <section className="card decision">
      <h3>Current decision
        <span className="dim small">{p.cycle ? ` #${p.cycle} at ${p.t?.toFixed(1)} min${p.attempts > 1 ? ` · ${p.attempts} proposals` : ""}` : ""}</span>
      </h3>
      <div className={`banner-inline ${last?.fault ? "bad" : last?.advisory?.length ? "warn" : "ok"}`}>
        {last ? (last.fault ? `Fault confirmed by PCA · ${last.diagnosis.summary}`
          : last.advisory?.length ? `Quality alarm ${last.advisory.join(", ")} (not acted on: PCA has not confirmed a fault)`
          : "Normal operation: PCA within limits") : "Waiting for the first monitoring cycle…"}
        {last?.pca && !last.fault && last.consecutive_pca > 0 && <span className="dim"> · {last.consecutive_pca} of 3 samples over the limit</span>}
      </div>
      {!p.cycle ? (
        <p className="dim small">When PCA confirms a fault the agents diagnose it, plan, propose an action and validate it before it
          reaches the plant. {llm ? `LLM: ${llm}.` : session?.llm.reason
            ? <span className="warn-text">Agents cannot act: {session.llm.reason}.</span>
            : "Connect an LLM in the settings above (free options: Groq, Gemini, OpenRouter or Ollama) to let them act."}</p>
      ) : (
        <ol className="steps">
          <li><span>Diagnosis</span>{diag ? <><b>{diag.most_likely ?? "unclear"}</b><small>{diag.summary}</small></> : <small className="dim">{last?.diagnosis.summary ?? "—"}</small>}</li>
          <li><span>Plan</span>{plan?.brief ? <><b>{plan.brief.target_fault}</b><small>{plan.brief.objective}</small></> : <small className="dim">—</small>}</li>
          <li><span>Proposal</span>{proposal?.plan ? <><b>{proposal.plan.commands.map(cmdText).join(", ") || "(none)"}</b><small>{proposal.plan.reasoning}</small></> : <small className="dim">—</small>}</li>
          <li><span>Validation</span>{verdict ? <b className={verdict.stage === "admit" ? (verdict.admissible ? "ok-text" : "warn-text") : ""}>{describeEvent(verdict)}</b> : <small className="dim">—</small>}</li>
          <li><span>Outcome</span><b>{p.outcome ?? "in progress"}</b></li>
        </ol>
      )}
      {llm && usage && <div className="dim small">LLM {llm}: {usage.calls} calls · {(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens · ≈ ${usage.cost_usd.toFixed(4)}</div>}
    </section>
  );
}

function Pending() {
  const pending = usePlant((s) => s.pending);
  const list = Object.values(pending);
  if (!list.length) return null;
  return (
    <section className="card pending">
      <h3>Waiting for your approval</h3>
      {list.map((p) => (
        <div className="proposal" key={p.proposal_id}>
          <div>{describeEvent(p)}</div>
          <div className="row">
            <button className="btn primary sm" onClick={() => send({ type: "agent_approve", proposal_id: p.proposal_id })}>Approve</button>
            <button className="btn sm" onClick={() => send({ type: "agent_dismiss", proposal_id: p.proposal_id })}>Dismiss</button>
          </div>
        </div>
      ))}
    </section>
  );
}

function FaultDetection() {
  const monitor = usePlant((s) => s.monitor);
  const version = usePlant((s) => s.trendVersion);
  const last = usePlant((s) => s.lastMonitor);
  const agent = usePlant((s) => s.agent);
  const contributors = last?.diagnosis.contributors ?? last?.pca?.top_contributors ?? [];
  const max = Math.max(1e-9, ...contributors.map((c) => c.score));
  return (
    <>
      {agent && !agent.pca_loaded && <p className="warn-text">No PCA model loaded (<code>operaitor train-pca</code>); rule checks only.</p>}
      <div className="grid charts">
        <Chart bare title="Hotelling T²" data={monitor} version={version} logY height={150}
          series={[{ key: "t2", label: "T²", color: C.a }, { key: "t2_limit", label: "99% limit", color: C.limit, dash: true }]} />
        <Chart bare title="SPE (Q)" data={monitor} version={version} logY height={150}
          series={[{ key: "spe", label: "SPE", color: C.d }, { key: "spe_limit", label: "99% limit", color: C.limit, dash: true }]} />
      </div>
      <div className="grid two">
        <div>
          <h4>Top contributing variables {last?.pca && !last.pca_persistent ? <span className="dim">(informational)</span> : null}</h4>
          {contributors.length ? contributors.map((c) => (
            <div className="contrib" key={c.variable} title={`T² ${c.t2.toFixed(2)} · SPE ${c.spe.toFixed(2)}`}>
              <span className="contrib-name">{pretty(c.variable)}</span>
              <span className="contrib-bar"><i style={{ width: `${(100 * c.score) / max}%` }} /></span>
              <span className="num small">{c.score.toFixed(2)}</span>
            </div>
          )) : <p className="dim">No PCA data yet</p>}
        </div>
        <div>
          <h4>Rule-based hypotheses</h4>
          {last?.diagnosis.hypotheses.length ? (
            <ol className="hyps">{last.diagnosis.hypotheses.map((h) => (
              <li key={h.fault}><b>{h.fault}</b> <span className="dim">({h.score.toFixed(2)}) {h.evidence.join(" · ")}</span></li>))}</ol>
          ) : <p className="dim">None</p>}
          {agent?.pca_meta && <p className="dim small">PCA model: {agent.pca_meta.n_components} components,
            {" "}{(100 * agent.pca_meta.explained_variance).toFixed(1)}% variance</p>}
        </div>
      </div>
    </>
  );
}

function LlmDiagnosis() {
  const log = usePlant((s) => s.agentLog);
  const llmOn = usePlant((s) => !!s.session?.llm.active);
  const [busy, setBusy] = useState(false);
  const d = [...log].reverse().find((e) => e.stage === "llm_diagnosis");
  useEffect(() => setBusy(false), [d]);
  return (
    <>
      {llmOn && <div className="row"><button className="btn sm" disabled={busy} onClick={() => { setBusy(true); send({ type: "diagnose_now" }); }}>
        {busy ? "Asking…" : "Diagnose now"}</button></div>}
      {!d ? <p className="dim">{llmOn ? "No LLM diagnosis yet. It runs when a fault is confirmed, or diagnose now."
        : "Needs an LLM: connect one in Agent settings above."}</p> : !d.ok ? (
        <p className="warn-text">LLM diagnosis failed: {d.error}</p>
      ) : (
        <>
          <p><span className="num dim">{d.time_min.toFixed(1)} min</span> <b>{d.most_likely ?? "unclear"}</b>
            {d.fault_class && <span className="pill">{d.fault_class}</span>}{d.on_demand && <span className="dim"> · on demand</span>}</p>
          <p>{d.summary}</p>
          {d.t2_spe_reading && <p className="dim">T² / SPE: {d.t2_spe_reading}</p>}
          <ol className="hyps">
            {(d.hypotheses ?? []).map((h) => (
              <li key={h.fault}><b>{h.fault}</b> <span className="dim">({Math.round(100 * h.probability)} %)</span>
                {h.evidence.length > 0 && <div className="small">for: {h.evidence.join(" · ")}</div>}
                {h.against.length > 0 && <div className="small dim">against: {h.against.join(" · ")}</div>}</li>
            ))}
          </ol>
          {d.operator_checks?.length ? <p className="small"><b>Check:</b> {d.operator_checks.join(" · ")}</p> : null}
        </>
      )}
    </>
  );
}

function PlanningBrief() {
  const brief = usePlant((s) => s.brief);
  if (!brief) return <p className="dim">No plan yet.</p>;
  return (
    <>
      <p>Target: <b>{brief.fault_label ?? brief.target_fault}</b> <code>{brief.target_fault}</code>
        {brief.alternatives?.length ? <span className="dim"> · alternatives: {brief.alternatives.join(", ")}</span> : null}</p>
      <p>{brief.objective}</p>
      {brief.symptoms?.length ? <p className="dim">Symptoms (knowledge graph): {brief.symptoms.join(", ")}</p> : null}
      <div className="levers">{brief.candidate_levers.map((l, i) =>
        <span key={i} className="pill" title={l.why}>{l.type}{l.target ? ` ${l.target}` : ""}</span>)}</div>
      {brief.constraints.length > 0 && <ul className="constraints">{brief.constraints.map((c, i) => <li key={i}>{c}</li>)}</ul>}
      {brief.planner_error && <p className="warn-text">LLM planner failed, knowledge-graph planner used: {brief.planner_error}</p>}
    </>
  );
}

function Timeline() {
  const log = usePlant((s) => s.agentLog);
  const events = [...log].filter((e) => e.stage !== "node").reverse().slice(0, 80);
  if (!events.length) return <p className="dim">No agent activity yet.</p>;
  return (
    <ul className="timeline">
      {events.map((e, i) => (
        <li key={i}>
          <span className="num dim">{e.time_min?.toFixed(1)}</span>
          <span className={`stage stage-${e.stage}`}>{e.stage.replace(/_/g, " ")}</span>
          <span className="body">{describeEvent(e)}{e.latency_s != null && <span className="dim"> · {Number(e.latency_s).toFixed(1)} s</span>}</span>
        </li>
      ))}
    </ul>
  );
}

export function AgentsTab() {
  const agent = usePlant((s) => s.agent);
  const admit = agent?.preset?.startsWith("llm_admit");
  const human = usePlant((s) => s.snap?.operator_mode === "human");
  return (
    <div className="stack">
      {human ? (
        <div className="fault-banner warn">The agents are off: you are the operator. What is shown below is from before
          the switch. Switch back to <b>Agents</b> in the header to hand over.</div>
      ) : <AgentSettings />}
      <Pending />
      <PipelineCard />
      <CurrentDecision />
      {admit && <AdmitCard />}
      <Section title="Fault detection" hint="PCA T² / SPE, contributions and rule checks" open>
        <FaultDetection />
      </Section>
      <Section title="LLM diagnosis" hint="ranked hypotheses from T², SPE, contributions and rule evidence"><LlmDiagnosis /></Section>
      <Section title="Planning brief" hint="target fault, objective, knowledge-graph levers"><PlanningBrief /></Section>
      {!admit && <AdmitCard />}
      <Section title="Agent timeline" hint="every step of every decision" open><Timeline /></Section>
    </div>
  );
}
