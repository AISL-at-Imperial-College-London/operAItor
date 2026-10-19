import { useEffect, useRef, useState } from "react";
import { quitApp, send, usePlant } from "../store";
import { FeedbackDialog } from "./Feedback";
import type { Snapshot } from "../types";

export type Tab = "plant" | "trends" | "agents" | "alarms";
export type Drawer = "operate" | "assistant" | "faults";
export const TABS: { id: Tab; label: string }[] = [
  { id: "plant", label: "Plant" }, { id: "trends", label: "Trends" },
  { id: "agents", label: "Agents" }, { id: "alarms", label: "Alarms" },
];
export const DRAWERS: { id: Drawer; label: string; title: string }[] = [
  { id: "operate", label: "Operate", title: "Change setpoints, modes, pumps and emergency devices" },
  { id: "assistant", label: "Assistant", title: "Ask questions about the plant" },
  { id: "faults", label: "Faults", title: "Inject faults to test the operator or the agents" },
];

// [label, delay per 0.5-min step (ms), pace every N steps, pace sleep (s)]
const SPEEDS: [string, number, number, number][] = [
  ["Max speed", 0, 0, 0], ["Fast", 0, 10, 0.05], ["Medium", 0, 10, 0.5],
  ["Slow", 250, 0, 0], ["Real time", 1000, 0, 0],
];
const speedKey = (d: number, every: number, sleep: number) => `${d}:${every}:${sleep}`;

const SCENARIO_LABEL: Record<string, string> = {
  nominal: "Normal operation", variability: "Normal operation with disturbances", surprise: "Surprise (hidden faults)",
};
export const scenarioLabel = (s: string) => SCENARIO_LABEL[s] ?? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");

/** One status for the plant: the most important thing first. */
function plantStatus(snap: Snapshot, fault: boolean): [string, string] {
  if (snap.done) {
    const failed = snap.plant_trip || ["SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE"].includes(snap.episode_status);
    return [failed ? "bad" : "warn", failed ? `Stopped · ${snap.plant_trip ? "plant trip" : snap.episode_status.toLowerCase().replace(/_/g, " ")}` : "Episode finished"];
  }
  if (snap.plant_trip) return ["bad", `Plant trip · ${snap.sif_latched.join(", ")}`];
  if (snap.sif_latched.length) return ["warn", `Safety function ${snap.sif_latched.join(", ")}`];
  if (fault) return ["warn", "Fault detected"];
  return ["ok", "Normal"];
}

function EpisodeMenu() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState({ scenario: "nominal", horizon_min: 360, seed: 20261004, stop_on_trip: true });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (snap) setCfg(snap.config); }, [snap?.episode]); // eslint-disable-line
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div className="menu" ref={ref}>
      <button className="btn" aria-expanded={open} onClick={() => setOpen(!open)} title="Start a new episode">New episode ▾</button>
      {open && (
        <div className="menu-pop" role="dialog" aria-label="New episode">
          <label className="field"><span>Scenario</span>
            <select value={cfg.scenario} onChange={(e) => setCfg({ ...cfg, scenario: e.target.value })}>
              {meta?.scenarios.map((s) => <option key={s} value={s}>{scenarioLabel(s)}</option>)}
            </select></label>
          <details className="sub">
            <summary>Advanced</summary>
            <div className="row">
              <label className="field"><span>Length [min]</span>
                <input type="number" value={cfg.horizon_min} onChange={(e) => setCfg({ ...cfg, horizon_min: +e.target.value })} /></label>
              <label className="field"><span>Random seed</span>
                <input type="number" value={cfg.seed} onChange={(e) => setCfg({ ...cfg, seed: +e.target.value })} /></label>
            </div>
            <label className="check"><input type="checkbox" checked={cfg.stop_on_trip}
              onChange={(e) => setCfg({ ...cfg, stop_on_trip: e.target.checked })} /> End the episode at a plant trip</label>
          </details>
          <button className="btn primary" onClick={() => { send({ type: "load", ...cfg }); setOpen(false); }}>Start fresh plant</button>
        </div>
      )}
    </div>
  );
}

/** Who answers the faults: the agents, or a person (agents off; for testing the console as a human). */
function OperatorModeSwitch() {
  const snap = usePlant((s) => s.snap);
  const test = usePlant((s) => s.humanTest);
  const mode = snap?.operator_mode ?? "agents";
  const locked = test?.state === "running";
  return (
    <div className="seg small op-mode" role="radiogroup" aria-label="Who operates the plant">
      {([["agents", "Agents", "The agents supervise the plant"],
         ["human", "Human operator", "Agents off: you answer the faults (Operate panel → Human test)"]] as const).map(([m, l, t]) => (
        <button key={m} role="radio" aria-checked={mode === m} className={`${mode === m ? "on" : ""} ${m}`}
          title={locked && m === "agents" ? "Stop the human test first" : t} disabled={locked && m === "agents"}
          onClick={() => mode !== m && send({ type: "operator_mode", mode: m })}>{l}</button>
      ))}
    </div>
  );
}

export function Header({ tab, onTab, drawer, onDrawer, onTour }:
  { tab: Tab; onTab: (t: Tab) => void; drawer: Drawer | ""; onDrawer: (d: Drawer) => void; onTour: () => void }) {
  const snap = usePlant((s) => s.snap);
  const last = usePlant((s) => s.lastMonitor);
  const pipeline = usePlant((s) => s.pipeline);
  const desktop = usePlant((s) => !!s.session?.desktop);
  const [feedback, setFeedback] = useState(false);

  const unack = snap?.alarms.filter((a) => a.state.endsWith("UNACK")).length ?? 0;
  const active = snap?.alarms.filter((a) => a.state.startsWith("ACTIVE")).length ?? 0;
  const current = speedKey(snap?.tick_delay_ms ?? 0, snap?.pace?.every ?? 10, snap?.pace?.sleep_s ?? 0.05);
  const known = SPEEDS.some(([, d, n, sl]) => speedKey(d, n, sl) === current);
  const human = snap?.operator_mode === "human";
  const [cls, status] = snap ? plantStatus(snap, !human && !!last?.fault) : ["", "Connecting…"];
  const deciding = !human && (snap?.held || (pipeline.cycle > 0 && !pipeline.outcome));
  const running = !!snap?.running && !snap.done;

  return (
    <header className="header">
      <div className="brand"><img className="logo" src="/favicon.svg" alt="" width={22} height={22} /><span className="brand-name">Oper<b className="ai">AI</b>tor <span className="dim brand-sub">beta</span></span></div>
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" data-tab={t.id} aria-selected={tab === t.id} className={tab === t.id ? "on" : ""} onClick={() => onTab(t.id)}>
            {t.label}
            {t.id === "alarms" && active > 0 && <span className={`count ${unack ? "bad" : "warn"}`}>{active}</span>}
          </button>
        ))}
      </nav>
      <div className="header-status">
        <span className={`status ${cls}`} title="Plant status">{status}</span>
        <OperatorModeSwitch />
        {deciding && <span className="status warn" title="The plant is paused while the agents decide">Agents deciding…</span>}
      </div>
      <div className="transport">
        <span className="clock num" title="Simulated time">{snap ? `${snap.time_min.toFixed(1)} min` : "—"}</span>
        {running
          ? <button className="btn primary" onClick={() => send({ type: "pause" })}>❚❚ Pause</button>
          : <button className="btn primary" onClick={() => send({ type: "run" })}
              title={snap?.done ? "Start the same episode again" : "Run the plant"}>{snap?.done ? "↻ Restart" : "▶ Run"}</button>}
        {!running && !snap?.done && <button className="btn" onClick={() => send({ type: "step", minutes: 1 })} title="Advance 1 minute">+1 min</button>}
        <select aria-label="Simulation speed" title="Simulation speed" value={current}
          onChange={(e) => {
            const s = SPEEDS.find(([, d, n, sl]) => speedKey(d, n, sl) === e.target.value);
            if (!s) return;
            send({ type: "speed", delay_ms: s[1] });
            send({ type: "pace", every: s[2], sleep_s: s[3] });
          }}>
          {!known && <option value={current}>Custom</option>}
          {SPEEDS.map(([l, d, n, sl]) => <option key={l} value={speedKey(d, n, sl)}>{l}</option>)}
        </select>
        <EpisodeMenu />
      </div>
      <div className="drawer-buttons">
        {DRAWERS.map((d) => (
          <button key={d.id} className={`btn ${drawer === d.id ? "on" : ""}`} aria-pressed={drawer === d.id} title={d.title}
            onClick={() => onDrawer(d.id)}>{d.label}</button>
        ))}
        <button className="btn ghost tour-btn" aria-label="Tour" title="Tour: a guided walkthrough of how to play with the plant" onClick={onTour}>?</button>
        <button className="btn ghost" title="Report a bug or give feedback (opens a GitHub issue, filled in)"
          onClick={() => setFeedback(true)}>Feedback</button>
        {desktop && <button className="btn ghost" title="Stop OperAItor on this computer" onClick={quitApp}>Quit OperAItor</button>}
      </div>
      {feedback && <FeedbackDialog onClose={() => setFeedback(false)} />}
    </header>
  );
}
