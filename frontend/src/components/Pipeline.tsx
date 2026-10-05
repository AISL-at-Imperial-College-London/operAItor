import { Fragment } from "react";
import { PIPELINE_NODES, usePlant, type NodeStatus } from "../store";
import type { AgentEvent } from "../types";

const LABEL: Record<string, [string, string]> = {
  monitoring: ["Monitoring", "PCA T²/SPE + rules → hypotheses"],
  kg_retrieval: ["Knowledge graph", "fault → symptoms → loops → actions"],
  planning: ["Planning", "target, objective, levers"],
  action_propose: ["Action agent", "concrete commands"],
  twin_evaluate: ["Digital twin", "simulate the action ahead"],
  validate: ["Validate", "back within limits → accept"],
  reprompt: ["Reprompter", "feedback → new proposal"],
  dispatch: ["Dispatch", "apply / ask operator"],
};
const STATUS_TEXT: Record<NodeStatus, string> = {
  idle: "", running: "running…", ok: "done", warn: "partial", fail: "failed", skip: "skipped",
};

const cmd = (c: Record<string, unknown>) => Object.values(c).join(" ");
const planText = (e?: AgentEvent) => e?.plan?.commands.map(cmd).join(", ") || "—";

function residuals(t: AgentEvent): string {
  const f = t.final ?? {};
  const bits = [
    ...(f.targets ?? []).filter((x) => !x.ok).map((x) => `${x.name} = ${x.value} (needs ${x.limit})`),
    f.active_alarms?.length ? `alarms ${f.active_alarms.join(", ")}` : "",
    f.saturated_loops?.length ? `saturated ${f.saturated_loops.join(", ")}` : "",
    f.failed_trains?.length ? `failed pumps ${f.failed_trains.join(", ")}` : "",
    f.sif_latched?.length ? `SIF ${f.sif_latched.join(", ")}` : "",
    f.t2_ratio != null && f.t2_ratio > 1 ? `T² ${f.t2_ratio.toFixed(2)}× limit` : "",
    f.spe_ratio != null && f.spe_ratio > 1 ? `SPE ${f.spe_ratio.toFixed(2)}× limit` : "",
  ].filter(Boolean);
  return bits.join(" · ");
}

export function PipelineCard() {
  const p = usePlant((s) => s.pipeline);
  const agent = usePlant((s) => s.agent);
  const held = usePlant((s) => s.snap?.held);
  const status = { ...p.status };
  const mainFlow = PIPELINE_NODES.filter((n) => n !== "reprompt");
  return (
    <div className="card pipeline-card">
      <div className="card-title row">
        <span>Agent pipeline</span>
        <span className="dim small">
          {p.cycle ? `decision #${p.cycle} at t=${p.t?.toFixed(1)} min` : "waiting for the first confirmed fault"}
          {p.outcome ? ` → ${p.outcome}` : p.cycle ? " → in progress" : ""}
          {p.attempts > 1 ? ` · ${p.attempts} proposals` : ""}
        </span>
        <span className="spacer" />
        {held && <span className="pill pill-warn" title="Plant paused until the agents finish this decision">plant held</span>}
      </div>
      <div className="flow" role="list">
        {mainFlow.map((n, i) => (
          <Fragment key={n}>
            {i > 0 && <span className="arrow" aria-hidden>→</span>}
            <div role="listitem" className={`node ${status[n]} ${n === "twin_evaluate" && agent && !agent.twin ? "off" : ""}`}
              title={LABEL[n][1]}>
              <b>{LABEL[n][0]}</b>
              <span className="sub">{LABEL[n][1]}</span>
              <span className="state">{STATUS_TEXT[status[n]]}</span>
            </div>
          </Fragment>
        ))}
      </div>
      <div className="loop">
        <span className={`node small ${status.reprompt}`}>
          ↺ <b>Reprompter</b> {agent?.reprompt_max ? `up to ${agent.reprompt_max} retries` : "off in this preset"}
          {p.attempts > 1 ? ` · attempt ${p.attempts}` : ""}
        </span>
        <span className="dim small">
          validate rejects → feedback to the action agent
          {agent?.fallback_to_safe ? " · retries exhausted → best safe candidate applied as a mitigation" : ""}
        </span>
      </div>
    </div>
  );
}

export function AgentCards() {
  const log = usePlant((s) => s.agentLog);
  const p = usePlant((s) => s.pipeline);
  const agent = usePlant((s) => s.agent);
  const c = p.cycle;
  const inCycle = (stage: AgentEvent["stage"]) => log.filter((e) => e.cycle === c && e.stage === stage);
  const proposals = inCycle("proposal");
  const invalid = inCycle("invalid");
  const twins = inCycle("twin");
  const skipped = inCycle("twin_skipped").at(-1);
  const validations = inCycle("validation");
  const reprompts = inCycle("reprompt");
  const fallback = inCycle("fallback").at(-1);
  const outcome = log.filter((e) => e.cycle === c && ["applied", "pending", "advisory", "rejected"].includes(e.stage)).at(-1);

  return (
    <div className="grid two">
      <div className="card">
        <div className="card-title">Action agent <span className="dim small">{agent?.actor ?? "none"}</span></div>
        {proposals.length === 0 ? <div className="empty">No proposal in this decision yet</div> : proposals.map((e, i) => (
          <div key={i} className="proposal-row">
            <span className="pill">#{(e.attempt ?? 0) + 1}</span> <b>{planText(e)}</b>
            <div className="dim small">{e.plan?.reasoning}</div>
            {e.kg_citations?.length ? <div className="dim small">KG: {e.kg_citations.join(", ")}</div> : null}
          </div>
        ))}
        {invalid.map((e, i) => <div key={i} className="note warn">Plan not executable: {e.errors?.join("; ")}</div>)}
        {inCycle("llm_error").slice(-1).map((e, i) => <div key={i} className="note warn">LLM call failed, rule fallback used: {e.error}</div>)}
        {fallback && <div className="note warn">Fallback: {fallback.reason} — <b>{planText(fallback)}</b></div>}
        {outcome && (
          <div className={`kv-line ${outcome.stage === "rejected" ? "alm-ack" : ""}`}>
            Outcome: <b>{outcome.stage}{outcome.kind === "mitigation" ? " (mitigation, not restoring)" : ""}</b>
            {outcome.result?.summary ? <span className="dim"> — {outcome.result.summary}</span> : null}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">Digital twin <span className="dim small">
          {agent?.twin ? `oracle clone, ${agent.twin_horizon_min} min look-ahead` : "disabled in this preset"}</span></div>
        {twins.length === 0
          ? <div className="empty">{skipped ? `Skipped: ${skipped.reason}` : "No rollout in this decision yet"}</div>
          : twins.map((t, i) => (
            <div key={i} className="proposal-row">
              <span className={`pill ${t.restored ? "pill-ok" : t.safe_reached ? "pill-warn" : "pill-bad"}`}>
                {t.restored ? (agent?.acceptance === "alarms" ? "back within limits" : "restores")
                  : t.safe_reached ? "safe, still faulty" : "not safe"}</span>{" "}
              <b>{planText(t)}</b>
              <div className="dim small">
                {t.restored ? `back within limits after ${t.time_to_restore_min?.toFixed(1)} min` : ""}
                {t.safe_reached ? `${t.restored ? " · " : ""}safe after ${t.time_to_safe_min?.toFixed(1)} min` : "never safe"}
                {" · "}unsafe {(100 * (t.unsafe_fraction ?? 0)).toFixed(0)}% of {t.horizon_min?.toFixed(0)} min
                {t.terminal_status && t.terminal_status !== "RUNNING" ? ` · twin ended ${t.terminal_status}` : ""}
              </div>
              {!t.restored && residuals(t) && <div className="dim small">at the end: {residuals(t)}</div>}
            </div>
          ))}
      </div>

      <div className="card">
        <div className="card-title">Validate agent <span className="dim small">
          {agent?.acceptance === "alarms" ? "accepts if the faulty variables return within their alarm limits"
            : agent?.acceptance === "restore" ? "accepts only if the twin shows the plant fully normal"
            : "accepts if the twin shows the plant safe"}</span></div>
        {validations.length === 0 ? <div className="empty">No verdict in this decision yet</div> : validations.map((v, i) => (
          <div key={i} className="proposal-row">
            <span className={`pill ${v.accepted ? "pill-ok" : "pill-bad"}`}>{v.accepted ? "ACCEPT" : "REJECT"}</span>{" "}
            <b>{planText(v)}</b>
            <ul className="checks">
              {v.checks?.map((ch, j) => (
                <li key={j}><span className={ch.ok ? "check-ok" : "check-bad"}>{ch.ok ? "✓" : "✗"}</span> {ch.check}
                  {ch.detail ? <span className="dim"> — {ch.detail}</span> : null}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-title">Reprompter <span className="dim small">
          {agent?.reprompt_max ? `max ${agent.reprompt_max} retries` : "off in this preset"}</span></div>
        {reprompts.length === 0 ? <div className="empty">No retries in this decision</div> : reprompts.map((r, i) => (
          <div key={i} className="proposal-row">
            <span className="pill pill-warn">retry {i + 1}</span> rejected <b>{planText({ ...r, plan: r.rejected_plan })}</b>
            <div className="dim small">feedback: {String(r.feedback?.fail_reason ?? "")}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
