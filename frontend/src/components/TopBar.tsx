import { useEffect, useRef, useState } from "react";
import { send, usePlant } from "../store";
import type { Snapshot } from "../types";

export type Tab = "plant" | "trends" | "agents" | "alarms";
export type Drawer = "operate" | "assistant" | "faults";
export const TABS: { id: Tab; label: string }[] = [
  { id: "plant", label: "Plant" }, { id: "trends", label: "Trends" },
  { id: "agents", label: "Agents" }, { id: "alarms", label: "Alarms" },
];

// [label, delay per 0.5-min step (ms), pace every N steps, pace sleep (s)]
const SPEEDS: [string, number, number, number][] = [
  ["Max speed", 0, 0, 0], ["Fast", 0, 10, 0.05], ["Medium", 0, 10, 0.5],
  ["Slow", 250, 0, 0], ["Real-time-ish", 1000, 0, 0],
];
const speedKey = (d: number, every: number, sleep: number) => `${d}:${every}:${sleep}`;

const PRESET_LABEL: Record<string, string> = {
  passive: "monitoring only", llm: "LLM + digital twin", llm_admit: "LLM + ADMIT", llm_admit_nosim: "LLM + ADMIT (no sim)",
  llm_judge: "LLM + judge", llm_lucky: "LLM, no validation", llm_plan: "LLM planning + twin",
  llm_nokg: "LLM + twin (no KG)", llm_notwin: "LLM (no twin)",
};

/** One status for the plant: the most important thing first. */
function plantStatus(snap: Snapshot, fault: boolean): [string, string] {
  if (snap.done) {
    const how = snap.episode_status.toLowerCase().replace(/_/g, " ");
    const failed = snap.plant_trip || ["SAFE_TRIP", "DAMAGE", "MODEL_LIMIT", "NUMERICAL_FAILURE"].includes(snap.episode_status);
    return [failed ? "bad" : "warn", `Ended · ${how}${snap.plant_trip ? ` (${snap.sif_latched.join(", ")})` : ""}`];
  }
  if (snap.plant_trip) return ["bad", `Plant trip · ${snap.sif_latched.join(", ")}`];
  if (snap.sif_latched.length) return ["warn", `SIF ${snap.sif_latched.join(", ")}`];
  if (fault) return ["warn", "Fault detected"];
  return ["ok", "Normal"];
}

function EpisodeMenu() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState({ scenario: "nominal", horizon_min: 360, seed: 20261004, stop_on_trip: false });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (snap) setCfg(snap.config); }, [snap?.episode]); // eslint-disable-line
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  return (
    <div className="menu" ref={ref}>
      <button className="btn" aria-expanded={open} onClick={() => setOpen(!open)} title="Episode settings">Episode ▾</button>
      {open && (
        <div className="menu-pop" role="dialog" aria-label="Episode">
          <label className="field"><span>Scenario</span>
            <select value={cfg.scenario} onChange={(e) => setCfg({ ...cfg, scenario: e.target.value })}>
              {meta?.scenarios.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
            </select></label>
          <div className="row">
            <label className="field"><span>Length [min]</span>
              <input type="number" value={cfg.horizon_min} onChange={(e) => setCfg({ ...cfg, horizon_min: +e.target.value })} /></label>
            <label className="field"><span>Random seed</span>
              <input type="number" value={cfg.seed} onChange={(e) => setCfg({ ...cfg, seed: +e.target.value })} /></label>
          </div>
          <label className="check"><input type="checkbox" checked={cfg.stop_on_trip}
            onChange={(e) => setCfg({ ...cfg, stop_on_trip: e.target.checked })} /> End the episode at a plant trip</label>
          <label className="check" title="Pause the plant while the agents decide (KG, plan, act, validate)">
            <input type="checkbox" checked={snap?.hold_for_agents ?? true}
              onChange={(e) => send({ type: "hold_for_agents", on: e.target.checked })} /> Pause the plant while agents decide</label>
          <button className="btn primary" onClick={() => { send({ type: "load", ...cfg }); setOpen(false); }}>Load episode</button>
        </div>
      )}
    </div>
  );
}

export function Header({ tab, onTab, drawer, onDrawer }:
  { tab: Tab; onTab: (t: Tab) => void; drawer: Drawer | ""; onDrawer: (d: Drawer) => void }) {
  const snap = usePlant((s) => s.snap);
  const last = usePlant((s) => s.lastMonitor);
  const agent = usePlant((s) => s.agent);
  const pipeline = usePlant((s) => s.pipeline);

  const unack = snap?.alarms.filter((a) => a.state.endsWith("UNACK")).length ?? 0;
  const active = snap?.alarms.filter((a) => a.state.startsWith("ACTIVE")).length ?? 0;
  const current = speedKey(snap?.tick_delay_ms ?? 0, snap?.pace?.every ?? 10, snap?.pace?.sleep_s ?? 0.05);
  const known = SPEEDS.some(([, d, n, sl]) => speedKey(d, n, sl) === current);
  const [cls, status] = snap ? plantStatus(snap, !!last?.fault) : ["", "—"];
  const preset = agent?.preset ?? "passive";
  const deciding = snap?.held || (pipeline.cycle > 0 && !pipeline.outcome);

  return (
    <header className="header">
      <div className="brand"><span className="logo" aria-hidden>◆</span><span className="brand-name">Oper<b className="ai">AI</b>tor <span className="dim brand-sub">acetone plant</span></span></div>
      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "on" : ""} onClick={() => onTab(t.id)}>
            {t.label}
            {t.id === "alarms" && active > 0 && <span className={`count ${unack ? "bad" : "warn"}`}>{active}</span>}
          </button>
        ))}
      </nav>
      <div className="header-status">
        <span className={`status ${cls}`} title="Plant status">{status}</span>
        <span className={`status ${deciding ? "warn" : ""}`} title="Agent configuration">
          {deciding ? "Agents deciding…" : `Agents: ${PRESET_LABEL[preset] ?? preset}`}</span>
      </div>
      <div className="transport">
        <span className="clock num" title="Simulated time">{snap ? `${snap.time_min.toFixed(1)} min` : "—"}</span>
        {snap?.running && !snap.done
          ? <button className="btn primary" onClick={() => send({ type: "pause" })}>Pause</button>
          : <button className="btn primary" onClick={() => send({ type: "run" })} disabled={snap?.done}>Run</button>}
        <button className="btn" onClick={() => send({ type: "step", minutes: 1 })} title="Advance 1 minute">+1 min</button>
        <select aria-label="Speed" title="Simulation speed" value={current}
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
        {([["operate", "Operate"], ["assistant", "Assistant"], ["faults", "Faults"]] as [Drawer, string][]).map(([d, l]) => (
          <button key={d} className={`btn ${drawer === d ? "on" : ""}`} aria-pressed={drawer === d} onClick={() => onDrawer(d)}>{l}</button>
        ))}
      </div>
    </header>
  );
}
