import { useState } from "react";
import { send, usePlant } from "../store";

const money = (x: number | null | undefined) =>
  x == null ? "—" : `${x < 0 ? "−" : ""}$${Math.abs(Math.round(x)).toLocaleString("en-US")}`;
const pretty = (s: string) => s.replace(/_/g, " ");

/** Human-operator test: a benchmark scenario replayed on the live plant for a person to answer. */
export function HumanTestPanel() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const test = usePlant((s) => s.humanTest);
  const [scenario, setScenario] = useState("random");
  const [blind, setBlind] = useState(true);
  const human = snap?.operator_mode === "human";
  const running = test?.state === "running";
  const scenarios = meta?.human_scenarios ?? [];

  return (
    <details className="sub human-test" open={human || !!test}>
      <summary>Human test <span className="dim small">you answer the benchmark faults, the agents are off</span></summary>
      {!running && (
        <div className="human-setup">
          <p className="hint">
            Replays one of the {scenarios.length} benchmark scenarios on a fresh plant: the same faults at the same times,
            scored the same way as the agents (margin + inventory change − $20k per plant stop over the scenario
            window). Watch the alarms and trends and act from the Operate panel; SIF resets after the faults clear are
            requested for you every 5 min, as in the benchmark.
          </p>
          <label className="field"><span>Scenario</span>
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              <option value="random">Random (recommended for a blind test)</option>
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>{blind ? `${s.id} · ${s.faults.length} fault${s.faults.length > 1 ? "s" : ""}`
                  : `${s.id} · ${s.faults.map(pretty).join(" + ")}`}</option>
              ))}
            </select></label>
          <label className="check" title="Hide what was injected (messages, Faults panel, scenario) until the test ends">
            <input type="checkbox" checked={blind} onChange={(e) => setBlind(e.target.checked)} /> Blind: hide the faults until the end</label>
          <button className="btn primary" onClick={() => send({ type: "human_test_start", scenario, blind })}
            title="Switches to human-operator mode, loads a fresh plant and starts it">Start human test</button>
        </div>
      )}
      {test && <HumanTestStatus />}
    </details>
  );
}

function HumanTestStatus() {
  const test = usePlant((s) => s.humanTest)!;
  const running = test.state === "running";
  const pct = Math.min(100, (100 * test.time_min) / test.window_min);
  return (
    <div className="human-status">
      <div className="kv-line">
        <b>{test.scenario === "hidden" ? "Scenario hidden" : test.scenario}</b>{" "}
        <span className={`pill ${running ? "pill-warn" : "pill-ok"}`}>{test.state}</span>
        {test.blind && <span className="pill">blind</span>}
      </div>
      <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}
        title={`${test.time_min.toFixed(1)} of ${test.window_min.toFixed(0)} min`}><span style={{ width: `${pct}%` }} /></div>
      <table className="kv small">
        <tbody>
          <tr><td>Time</td><td className="num">{test.time_min.toFixed(1)} / {test.window_min.toFixed(0)} min</td></tr>
          <tr><td>Profit so far</td><td className={`num ${test.profit < 0 ? "neg" : "pos"}`}>{money(test.profit)}</td></tr>
          <tr><td>Plant stop</td><td>{test.stop_min != null ? <span className="alm-unack">at {test.stop_min.toFixed(1)} min ({test.stop_kind})</span> : "none"}</td></tr>
          <tr><td>Faults injected</td><td className="num">{running && test.blind ? `${test.faults_injected} so far` : `${test.faults_injected} of ${test.faults_total}`}</td></tr>
          <tr><td>Your actions</td><td className="num">{test.n_actions}</td></tr>
        </tbody>
      </table>
      {running && <button className="btn" onClick={() => send({ type: "human_test_stop" })}>Stop and score now</button>}
      {test.faults && (
        <div className="human-reveal">
          <h4>{running ? "Faults" : "What happened"}</h4>
          {test.faults.map((f, i) => (
            <div key={i} className="kv-line small"><span className={`pill ${f.injected ? "pill-bad" : ""}`}>{f.injected ? "injected" : "pending"}</span>{" "}
              {pretty(f.fault)} <span className="dim">@ {f.onset_min.toFixed(0)} min{f.lift_min != null ? `, cleared at ${f.lift_min.toFixed(0)} min` : ""}</span></div>
          ))}
          {test.interaction && <p className="hint">{test.interaction}</p>}
        </div>
      )}
      {test.result && <HumanResult />}
      {test.actions.length > 0 && (
        <details className="sub"><summary>Your actions ({test.n_actions})</summary>
          {test.actions.map((a, i) => (
            <div key={i} className="kv-line small"><span className="num dim">{a.t.toFixed(1)} min</span>{" "}
              <span className={a.ok ? "" : "alm-unack"}>{a.summary}</span></div>
          ))}
        </details>
      )}
      {test.report_path && <div className="dim small">Report: <code>{test.report_path.split(/[\\/]/).pop()}</code></div>}
    </div>
  );
}

function HumanResult() {
  const test = usePlant((s) => s.humanTest)!;
  const r = test.result!;
  const rows = [{ who: "You", config: "human", profit: r.profit, stopped: test.stop_min != null, stop_min: test.stop_min },
                ...r.benchmark].sort((a, b) => b.profit - a.profit);
  const rank = rows.findIndex((x) => x.config === "human") + 1;
  return (
    <div className="human-result">
      <h4>Score</h4>
      <p className="small">
        {money(r.profit)} over the window (margin {money(r.margin)}, inventory {money(r.inventory)}
        {r.penalty ? `, plant stop ${money(r.penalty)}` : ""}).
        {r.saving_vs_no_agents != null && <> Versus doing nothing: <b className={r.saving_vs_no_agents >= 0 ? "pos" : "neg"}>
          {r.saving_vs_no_agents >= 0 ? "+" : ""}{money(r.saving_vs_no_agents)}</b>.</>}
        {!r.complete && " Stopped early: the score covers only part of the window, so it is not ranked against the benchmark, which scores the whole window."}
        {r.complete && r.benchmark.length > 1 && ` Rank ${rank} of ${rows.length} against the benchmark runs (protocol v2) on this scenario.`}
      </p>
      {r.complete && r.benchmark.length > 0 && (
        <div className="table-scroll">
          <table className="data">
            <thead><tr><th>Who</th><th className="num">Profit</th><th>Plant stop</th></tr></thead>
            <tbody>
              {rows.map((x, i) => (
                <tr key={i} className={x.config === "human" ? "me" : ""}>
                  <td>{x.who}</td><td className="num">{money(x.profit)}</td>
                  <td className="small">{x.stopped ? `at ${x.stop_min?.toFixed(1)} min` : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
