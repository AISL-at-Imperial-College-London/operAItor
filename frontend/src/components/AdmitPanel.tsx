import { useEffect, useMemo, useState } from "react";
import { usePlant } from "../store";
import type { AdmitGateResult, AdmitRecordResult, AgentEvent } from "../types";

// ADMIT gate trace: what each proposed action is checked on, and why it passed or was blocked.

const GATE_HELP: Record<string, string> = {
  T0: "Record integrity: well-formed record, known action, valid parameters, retry key; forbidden actions",
  T1: "Evidence: cited ids exist in the platform ledger, arrived before the decision, are fresh; a commit needs trusted evidence for every required tag",
  T2: "Hazard: a passive action (hold / observe) must name the active hazard",
  T3: "Admissibility: authority, operating mode, SOP steps in order, confidence floor, declared reversibility, recovery plan",
  T4: "Consequence: the plan and 'do nothing' projected on the plant; rejected if a SIF limit is crossed or the margin is worse than doing nothing",
  T6: "Trace: always recorded; warnings only (thin trace, unverified recovery)",
};

const fmt = (v: unknown) => (typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3)) : String(v));

function Chips({ items, mark }: { items: string[] | undefined; mark?: (s: string) => string }) {
  if (!items?.length) return <span className="dim">none</span>;
  return <>{items.map((x) => <span key={x} className={`pill ${mark ? mark(x) : ""}`}>{x}</span>)}</>;
}

function gateDetail(g: AdmitGateResult, r: AdmitRecordResult) {
  if (g.skipped) return <span className="dim">skipped: no simulation layer in this configuration</span>;
  if (g.violations.length) return <>{g.violations.map((v) => <div key={v.code + v.message}><code>{v.code}</code> {v.message}</div>)}</>;
  if (g.tier === "T4" && r.trajectories) {
    const t = r.trajectories;
    const d = (k: string, label: string) => t[k] ? <div>{label}: {t[k].crossed ? <b className="alm-unack">crosses {t[k].crossed_var} at {((t[k].crossed_at_s ?? 0) / 60).toFixed(1)} min</b>
      : <>stays inside, min margin {fmt(t[k].min_margin)}</>}</div> : null;
    return <>{d("null_trajectory", "do nothing")}{d("action_trajectory", "with the plan")}{d("response_window_trajectory", "unattended 5 min")}
      {t.recovery_rehearsal && <div>recovery rehearsal ({t.recovery_rehearsal.recovery_action ?? "none named"}): {String(t.recovery_rehearsal.recovered)}</div>}</>;
  }
  if (g.tier === "T6") {
    const w = (g.info?.warnings as string[] | undefined) ?? [];
    return <span className="dim">{w.length ? `warnings: ${w.join(", ")}` : "recorded, ledger verified"}</span>;
  }
  return <span className="dim">pass</span>;
}

function RecordView({ r, ledgerIds }: { r: AdmitRecordResult; ledgerIds: Set<string> }) {
  const rec = r.record, rule = r.rule;
  const params = Object.entries(r.params ?? {}).map(([k, v]) => `${k}=${fmt(v)}`).join(", ");
  return (
    <div className={`admit-record ${r.admissible ? "ok" : "bad"}`}>
      <div className="admit-head">
        <b>{r.action}</b>{params && <span className="dim"> ({params})</span>}
        <span className={`pill ${r.admissible ? "pill-ok" : "pill-bad"}`}>{r.admissible ? "admissible" : `blocked at ${r.first_failing}: ${r.aas_code}`}</span>
      </div>
      <div className="admit-cols">
        <div>
          <div className="admit-sub">Record filed by the agent {!rec.filed && <span className="alm-unack">(none filed)</span>}</div>
          <table className="kv small"><tbody>
            <tr><td>cited evidence</td><td><Chips items={rec.cited_evidence} mark={(x) => ledgerIds.has(x) ? "" : "pill-bad"} /></td></tr>
            <tr><td>steps performed</td><td><Chips items={rec.checks_performed} /></td></tr>
            <tr><td>confidence</td><td>{fmt(rec.confidence)}</td></tr>
            <tr><td>reversibility</td><td>{rec.reversibility || <span className="dim">—</span>}</td></tr>
            <tr><td>recovery plan</td><td>{rec.recovery_plan || <span className="dim">—</span>}</td></tr>
            <tr><td>hazard</td><td>{rec.hazard_id}</td></tr>
            <tr><td>diagnosis</td><td>{rec.diagnosis || <span className="dim">—</span>}</td></tr>
            {rec.rationale && <tr><td>rationale</td><td className="dim">{rec.rationale}</td></tr>}
          </tbody></table>
        </div>
        <div>
          <div className="admit-sub">Rule it is checked against (cartridge)</div>
          <table className="kv small"><tbody>
            <tr><td>class</td><td>{rule.class} · {rule.reversibility}{rule.forbidden ? " · FORBIDDEN" : ""}</td></tr>
            <tr><td>authority needed</td><td><Chips items={rule.required_authority} /></td></tr>
            <tr><td>evidence tags needed</td><td><Chips items={rule.required_evidence_tags} /></td></tr>
            <tr><td>SOP steps (in order)</td><td><Chips items={rule.required_steps} /></td></tr>
            <tr><td>confidence floor</td><td>{fmt(rule.confidence_floor ?? 0)}</td></tr>
            <tr><td>allowed modes</td><td><Chips items={rule.allowed_modes} /></td></tr>
          </tbody></table>
        </div>
      </div>
      <table className="loops admit-gates">
        <thead><tr><th>Gate</th><th>Checks</th><th>Result</th><th>Detail</th></tr></thead>
        <tbody>
          {(r.gates ?? []).map((g) => (
            <tr key={g.tier}>
              <td><b>{g.tier}</b></td>
              <td className="small" title={GATE_HELP[g.tier]}>{g.name}</td>
              <td>{g.skipped ? <span className="pill">skipped</span> : <span className={`pill ${g.passed ? "pill-ok" : "pill-bad"}`}>{g.passed ? "pass" : "fail"}</span>}</td>
              <td className="small">{gateDetail(g, r)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AdmitCard() {
  const log = usePlant((s) => s.agentLog);
  const agent = usePlant((s) => s.agent);
  const events = useMemo(() => log.filter((e): e is AgentEvent => e.stage === "admit" && !!e.records), [log]);
  const [pick, setPick] = useState(-1);
  useEffect(() => setPick(-1), [events.length]);
  const admitOn = agent?.validator ? agent.validator.startsWith("admit") : agent?.preset?.startsWith("llm_admit");
  if (!events.length && !admitOn) return null;
  const e = events.at(pick < 0 ? -1 : pick);
  const cited = new Set((e?.records ?? []).flatMap((r) => r.record?.cited_evidence ?? []));
  const ledgerIds = new Set((e?.ledger ?? []).map((x) => x.id));
  const ctx = e?.context;
  return (
    <details className="section card admit-card" id="admit-card" open>
      <summary><span className="section-name">ADMIT gate</span>
        <span className="dim small"> what each proposed action is checked on before it reaches the plant</span></summary>
      <div className="section-body">
      {events.length > 0 && (
        <label className="field narrow"><span>Check</span>
          <select aria-label="ADMIT check" value={pick < 0 ? events.length - 1 : pick} onChange={(x) => setPick(+x.target.value)}>
            {events.map((v, i) => <option key={i} value={i}>
              {v.time_min.toFixed(1)} min · decision {v.cycle} · try {(v.attempt ?? 0) + 1} · {v.admissible ? "admitted" : "blocked"}</option>)}
          </select></label>
      )}
      {!e ? <div className="empty">No proposal checked yet. When a fault is confirmed and the agent proposes an action,
        its record, the rule and every gate result appear here.</div> : (
        <>
          <div className={`fault-banner ${e.admissible ? "ok" : "bad"}`}>
            {e.admissible ? "Admitted: the plan was applied to the plant" : "Blocked: nothing applied; the violations went back to the action agent"}
            {" "}· {e.simulate ? "T0–T4 (consequence projected on the plant)" : "T0–T3 only (no simulation layer)"}
            {e.wall_s != null && <span className="dim"> · {e.wall_s}s</span>}
          </div>
          {ctx && <div className="kv-line small">
            Decision at <b>{ctx.decided_at_min.toFixed(1)} min</b> · mode <b>{ctx.mode}</b> · active hazard <b>{ctx.hazard_id ?? "none"}</b>
            {" "}· agent authority <Chips items={ctx.authority_scope} />
            {ctx.horizon_min ? <> · projection horizon {ctx.horizon_min.toFixed(0)} min</> : null}
          </div>}
          {(e.records ?? []).map((r, i) => <RecordView key={i} r={r} ledgerIds={ledgerIds} />)}
          {e.remedies && Object.keys(e.remedies).length > 0 && (
            <div className="small"><div className="admit-sub">What ADMIT told the agent to fix</div>
              <ul className="constraints">{Object.entries(e.remedies).map(([c, t]) => <li key={c}><code>{c}</code> {t}</li>)}</ul></div>
          )}
          <details className="admit-ledger">
            <summary>Evidence ledger the agent was shown ({e.ledger?.length ?? 0} entries, hash-chained; cited ones highlighted)</summary>
            <table className="loops small">
              <thead><tr><th>id</th><th>tag</th><th>channel</th><th>trust</th><th>content</th></tr></thead>
              <tbody>
                {(e.ledger ?? []).map((x) => (
                  <tr key={x.id} className={cited.has(x.id) ? "cited" : ""}>
                    <td><code>{x.id}</code></td><td>{x.tag}</td><td>{x.source}</td>
                    <td className={x.trust === "trusted" ? "" : "alm-ack"}>{x.trust}</td><td>{x.content}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
      </div>
    </details>
  );
}
