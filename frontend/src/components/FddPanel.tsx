import { useEffect, useState } from "react";
import { send, usePlant } from "../store";
import type { AgentEvent } from "../types";
import { Chart } from "./Chart";
import { AdmitCard } from "./AdmitPanel";
import { PipelineCard } from "./Pipeline";
import { C } from "./Trends";

const pretty = (v: string) => v.replace(/^meas\./, "").replace(/^op\./, "OP ").replace(/_/g, " ");

function describePlan(e: AgentEvent) {
  if (e.stage === "plan" && e.brief) {
    return <><b>target {e.brief.target_fault}</b> <span className="dim">— {e.brief.objective}</span></>;
  }
  if (e.stage === "kg") return <span className="dim">{e.nodes?.length ?? 0} KG nodes: {(e.nodes ?? []).slice(0, 6).join(", ")}…</span>;
  if (e.stage === "rejected") return <span className="alm-unack">gave up: {e.reason}</span>;
  if (e.stage === "twin_skipped") return <span className="dim">twin skipped: {e.reason}</span>;
  if (e.stage === "twin") return <span className={e.restored ? "" : "alm-ack"}>twin: {e.restored ? `faulty variables back within limits after ${e.time_to_restore_min?.toFixed(1)} min` : e.safe_reached ? "keeps it safe, still faulty" : "does not keep it safe"}</span>;
  if (e.stage === "verification") return <span className="dim">{e.reason}</span>;
  if (e.stage === "llm_diagnosis") return <span>LLM diagnosis: <b>{e.most_likely ?? "unclear"}</b> <span className="dim">— {e.summary ?? e.error}</span></span>;
  if (e.stage === "admit") {
    const bad = (e.records ?? []).filter((r) => !r.admissible);
    return <span className={e.admissible ? "" : "alm-ack"}>ADMIT: {e.admissible ? "admissible" : "inadmissible"}
      {e.hazard_id ? ` · hazard ${e.hazard_id}` : ""}
      {bad.map((r) => ` · ${r.action}: ${r.violations.map((v) => v.code).join(", ")}`).join("")}</span>;
  }
  if (e.stage === "alarm_ack") return <span className="dim">acknowledged {(e.alarms ?? []).join(", ")} — {e.reason}</span>;
  if (e.stage === "llm_error") return <span className="alm-unack">LLM call failed, nothing applied: {e.error}</span>;
  if (e.stage === "validation") { const bad = e.checks?.filter((c) => !c.ok) ?? []; return <span className={e.accepted ? "" : "alm-ack"}>{e.accepted ? "ACCEPT" : `REJECT: ${bad[0]?.check} — ${bad[0]?.detail}`}</span>; }
  if (e.stage === "fallback") return <span className="alm-ack">{e.reason}: {e.plan?.commands.map((c) => Object.values(c).join(" ")).join(", ")}</span>;
  if (e.stage === "reprompt") return <span className="alm-ack">retry {e.attempt}/{e.reprompt_max}: {String(e.feedback?.fail_reason ?? "")}</span>;
  if (e.plan) {
    const cmds = e.plan.commands.map((c) => Object.values(c).join(" ")).join(", ");
    const cites = e.kg_citations?.length ? ` [KG: ${e.kg_citations.join(", ")}]` : "";
    return <><b>{cmds || "(empty)"}</b> <span className="dim">— {e.plan.reasoning}{cites}</span></>;
  }
  if (e.errors) return <span className="alm-unack">{e.errors.join("; ")}</span>;
  if (e.result) return <span>{e.result.summary}</span>;
  return null;
}

function LlmBadge({ model }: { model: string | null }) {
  const usage = usePlant((s) => s.llmUsage);
  const session = usePlant((s) => s.session);
  if (!model) return <div className="dim small">No LLM connected: the agents monitor and diagnose only, they take no actions.{" "}
    {session?.hosted ? "Add your OpenAI key in the Assistant panel to turn them on." : "Start the server with an OpenAI key (gpt-5-nano) or a local model, or add a key in the Assistant panel."}</div>;
  return (
    <div className="small llm-badge">
      <span className="pill pill-ok">OpenAI {model}</span>{" "}
      {session?.llm.key_source === "visitor" && <span className="dim">(your key) </span>}
      {usage ? <span className="dim">{usage.calls} calls · {(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens · ≈ ${usage.cost_usd.toFixed(4)}</span>
             : <span className="dim">no calls yet</span>}
    </div>
  );
}

export function AgentModeSwitch() {
  const agent = usePlant((s) => s.agent);
  if (!agent) return <span className="dim">agents offline</span>;
  return (
    <div className="segmented" role="radiogroup" aria-label="Agent mode">
      {agent.modes.map((m) => (
        <button key={m} role="radio" aria-checked={agent.mode === m} className={agent.mode === m ? "on" : ""}
          onClick={() => send({ type: "agent_mode", mode: m })}>{m}</button>
      ))}
    </div>
  );
}

function PlanningCard() {
  const brief = usePlant((s) => s.brief);
  if (!brief) return null;
  return (
    <div className="card">
      <div className="card-title">Planning agent <span className="dim">— latest brief from the monitoring hypotheses + KG</span></div>
      <div className="kv-line">Target: <b>{brief.fault_label ?? brief.target_fault}</b> <code>{brief.target_fault}</code>
        {brief.alternatives?.length ? <span className="dim"> · alternatives: {brief.alternatives.join(", ")}</span> : null}</div>
      <div className="kv-line">{brief.objective}</div>
      {brief.symptoms?.length ? <div className="kv-line dim">Symptoms (KG): {brief.symptoms.join(", ")}</div> : null}
      <div className="levers">
        {brief.candidate_levers.map((l, i) => (
          <span key={i} className="pill" title={l.why}>{l.type}{l.target ? ` ${l.target}` : ""}</span>
        ))}
      </div>
      {brief.constraints.length > 0 && <ul className="constraints">{brief.constraints.map((c, i) => <li key={i}>{c}</li>)}</ul>}
      {brief.planner_error && <div className="note warn">LLM planner failed, KG planner used: {brief.planner_error}</div>}
    </div>
  );
}

function LlmDiagnosisCard() {
  const log = usePlant((s) => s.agentLog);
  const [busy, setBusy] = useState(false);
  const d = [...log].reverse().find((e) => e.stage === "llm_diagnosis");
  useEffect(() => setBusy(false), [d]);
  return (
    <div className="card">
      <div className="card-title row">
        <span>LLM diagnosis <span className="dim">— reads T², SPE, the contributions and the rule evidence</span></span>
        <span className="spacer" />
        <button className="btn sm" disabled={busy} onClick={() => { setBusy(true); send({ type: "diagnose_now" }); }}>
          {busy ? "Asking…" : "Ask the LLM now"}</button>
      </div>
      {!d ? <div className="empty">No LLM diagnosis yet. It runs when a fault is confirmed, or ask now.</div> : !d.ok ? (
        <div className="note warn">LLM diagnosis failed: {d.error}</div>
      ) : (
        <>
          <div className="kv-line"><span className="num t">{d.time_min.toFixed(1)} min</span>{" "}
            <b>{d.most_likely ?? "unclear"}</b>{d.fault_class ? <span className="pill">{d.fault_class}</span> : null}
            {d.on_demand ? <span className="dim"> · on demand</span> : null}</div>
          <div className="kv-line">{d.summary}</div>
          {d.t2_spe_reading && <div className="kv-line dim">T² / SPE: {d.t2_spe_reading}</div>}
          <ol className="hyps">
            {(d.hypotheses ?? []).map((h) => (
              <li key={h.fault}><b>{h.fault}</b> <span className="dim">({Math.round(100 * h.probability)} %)</span>
                {h.evidence.length ? <div className="small">for: {h.evidence.join(" · ")}</div> : null}
                {h.against.length ? <div className="small dim">against: {h.against.join(" · ")}</div> : null}</li>
            ))}
          </ol>
          {d.operator_checks?.length ? <div className="small"><b>Check:</b> {d.operator_checks.join(" · ")}</div> : null}
        </>
      )}
    </div>
  );
}

export function FddPanel() {
  const monitor = usePlant((s) => s.monitor);
  const version = usePlant((s) => s.trendVersion);
  const last = usePlant((s) => s.lastMonitor);
  const agent = usePlant((s) => s.agent);
  const log = usePlant((s) => s.agentLog);
  const pending = usePlant((s) => s.pending);

  const contributors = last?.diagnosis.contributors ?? last?.pca?.top_contributors ?? [];
  const maxScore = Math.max(1e-9, ...contributors.map((c) => c.score));
  const pendingList = Object.values(pending);

  return (
    <div className="fdd">
      <PipelineCard />
      <div className="grid fdd-top">
        <div className="card">
          <div className="card-title row">
            <span>Monitoring agent</span><span className="spacer" />
            <AgentModeSwitch />
          </div>
          <div className={`fault-banner ${last?.fault ? "bad" : last?.advisory?.length ? "warn" : "ok"}`}>
            {last ? (last.fault ? `FAULT · ${last.diagnosis.summary}`
              : last.advisory?.length ? `Quality alarm ${last.advisory.join(", ")} · not acted on (PCA has not confirmed a fault)`
              : "Normal operation") : "Waiting for first cycle…"}
          </div>
          <div className="detectors">
            {last && Object.entries(last.detectors).map(([n, alarm]) => (
              <span key={n} className={`pill ${alarm ? "pill-bad" : "pill-ok"}`}>{n}</span>
            ))}
            {last?.pca && <span className="dim">PCA persistence {last.consecutive_pca}/3</span>}
          </div>
          {agent && !agent.pca_loaded && (
            <div className="note warn">No PCA model loaded — run <code>train_pca.py</code>. Rule checks only.</div>
          )}
          {agent?.pca_meta && (
            <div className="dim small">PCA: {agent.pca_meta.n_components} PCs, {(100 * agent.pca_meta.explained_variance).toFixed(1)}% variance</div>
          )}
          {agent && (
            <LlmBadge model={agent.llm_model ?? null} />
          )}
          <div className="card-title" style={{ marginTop: 12 }}>Rule-based hypotheses</div>
          {last?.diagnosis.hypotheses.length ? (
            <ol className="hyps">
              {last.diagnosis.hypotheses.map((h) => (
                <li key={h.fault}><b>{h.fault}</b> <span className="dim">({h.score.toFixed(2)}) {h.evidence.join(" · ")}</span></li>
              ))}
            </ol>
          ) : <div className="empty">None</div>}
        </div>

        <div className="card">
          <div className="card-title">Variable contributions {last?.pca && !last.pca_persistent ? <span className="dim">(informational)</span> : null}</div>
          {contributors.length ? contributors.map((c) => (
            <div className="contrib" key={c.variable} title={`T² ${c.t2.toFixed(2)} · SPE ${c.spe.toFixed(2)}`}>
              <span className="contrib-name">{pretty(c.variable)}</span>
              <span className="contrib-bar"><i style={{ width: `${(100 * c.score) / maxScore}%` }} /></span>
              <span className="num">{c.score.toFixed(2)}</span>
            </div>
          )) : <div className="empty">No PCA data</div>}
        </div>
      </div>

      <LlmDiagnosisCard />
      <AdmitCard />
      <PlanningCard />

      {pendingList.length > 0 && (
        <div className="card pending">
          <div className="card-title">Proposals awaiting approval</div>
          {pendingList.map((p) => (
            <div className="proposal" key={p.proposal_id}>
              <div>{describePlan(p)}</div>
              <div className="row">
                <button className="btn primary sm" onClick={() => send({ type: "agent_approve", proposal_id: p.proposal_id })}>Approve</button>
                <button className="btn sm" onClick={() => send({ type: "agent_dismiss", proposal_id: p.proposal_id })}>Dismiss</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid charts two">
        <Chart title="Hotelling T²" data={monitor} version={version} logY
          series={[{ key: "t2", label: "T²", color: C.a }, { key: "t2_limit", label: "99% limit", color: C.limit, dash: true }]} />
        <Chart title="SPE (Q)" data={monitor} version={version} logY
          series={[{ key: "spe", label: "SPE", color: C.d }, { key: "spe_limit", label: "99% limit", color: C.limit, dash: true }]} />
      </div>

      <div className="card">
        <div className="card-title">Agent timeline</div>
        {log.length === 0 ? <div className="empty">No agent activity yet</div> : (
          <ul className="timeline">
            {[...log].filter((e) => e.stage !== "node").reverse().slice(0, 80).map((e, i) => (
              <li key={i} className={`stage-${e.stage}`}>
                <span className="num t">{e.time_min?.toFixed(1)}</span>
                <span className="stage">{e.stage}</span>
                <span className="body">{describePlan(e)}{e.latency_s != null && <span className="dim"> · {e.latency_s}s</span>}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
