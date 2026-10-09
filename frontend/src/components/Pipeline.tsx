import { Fragment } from "react";
import { PIPELINE_NODES, usePlant, type NodeStatus } from "../store";

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
