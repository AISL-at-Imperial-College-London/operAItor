import { useEffect, useState } from "react";
import { send, usePlant } from "../store";

// One speed control: [label, delay per 0.5-min step (ms), pace every N steps, pace sleep (s)].
const SPEEDS: [string, number, number, number][] = [
  ["Max speed", 0, 0, 0], ["Fast", 0, 10, 0.05], ["Medium", 0, 10, 0.5],
  ["Slow · 0.5 min / 0.25 s", 250, 0, 0], ["Real-time-ish · 0.5 min / 1 s", 1000, 0, 0],
];
const speedKey = (d: number, every: number, sleep: number) => `${d}:${every}:${sleep}`;

export function TopBar({ onToggleDrawer }: { onToggleDrawer: () => void }) {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const connected = usePlant((s) => s.connected);
  const last = usePlant((s) => s.lastMonitor);
  const [cfg, setCfg] = useState({ scenario: "nominal", horizon_min: 360, seed: 20261004, stop_on_trip: false });

  useEffect(() => { if (snap) setCfg(snap.config); }, [snap?.episode]); // eslint-disable-line

  const unack = snap?.alarms.filter((a) => a.state.endsWith("UNACK")).length ?? 0;
  const active = snap?.alarms.filter((a) => a.state.startsWith("ACTIVE")).length ?? 0;
  const runState = !snap ? "—" : snap.done ? "ENDED" : snap.running ? "RUNNING" : "PAUSED";
  const current = speedKey(snap?.tick_delay_ms ?? 0, snap?.pace?.every ?? 10, snap?.pace?.sleep_s ?? 0.05);
  const known = SPEEDS.some(([, d, n, sl]) => speedKey(d, n, sl) === current);
  const sifs = snap?.sif_latched ?? [];

  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo" aria-hidden>◆</span>
        <span>Acetone Plant Console</span>
      </div>

      <div className="tb-group episode">
        <select aria-label="Episode" value={cfg.scenario} onChange={(e) => setCfg({ ...cfg, scenario: e.target.value })}>
          {meta?.scenarios.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
        </select>
        <input aria-label="Horizon [min]" title="Horizon [min]" type="number" className="w-num" value={cfg.horizon_min}
          onChange={(e) => setCfg({ ...cfg, horizon_min: +e.target.value })} />
        <input aria-label="Seed" title="Random seed" type="number" className="w-seed" value={cfg.seed}
          onChange={(e) => setCfg({ ...cfg, seed: +e.target.value })} />
        <label className="check" title="Terminate the episode when the plant-trip SIF latches">
          <input type="checkbox" checked={cfg.stop_on_trip} onChange={(e) => setCfg({ ...cfg, stop_on_trip: e.target.checked })} /> stop on trip
        </label>
        <button className="btn" onClick={() => send({ type: "load", ...cfg })}>Load</button>
      </div>

      <div className="tb-group">
        {snap?.running ? (
          <button className="btn primary" onClick={() => send({ type: "pause" })}>Pause</button>
        ) : (
          <button className="btn primary" onClick={() => send({ type: "run" })}>Run</button>
        )}
        <button className="btn" onClick={() => send({ type: "step", minutes: 1 })}>+1 min</button>
        <button className="btn" onClick={() => send({ type: "step", minutes: 5 })}>+5 min</button>
        <select aria-label="Speed" title="Simulation speed" value={current}
          onChange={(e) => {
            const s = SPEEDS.find(([, d, n, sl]) => speedKey(d, n, sl) === e.target.value);
            if (!s) return;
            send({ type: "speed", delay_ms: s[1] });
            send({ type: "pace", every: s[2], sleep_s: s[3] });
          }}>
          {!known && <option value={current}>custom</option>}
          {SPEEDS.map(([l, d, n, sl]) => <option key={l} value={speedKey(d, n, sl)}>{l}</option>)}
        </select>
        <label className="check" title="Pause the plant while the agents are deciding (KG → plan → act → twin → reprompt)">
          <input type="checkbox" checked={snap?.hold_for_agents ?? true}
            onChange={(e) => send({ type: "hold_for_agents", on: e.target.checked })} /> hold for agents
        </label>
      </div>

      <div className="tb-status">
        <span className={`chip ${connected ? "ok" : "bad"}`}>{connected ? "connected" : "offline"}</span>
        <span className={`chip ${runState === "RUNNING" ? "ok" : runState === "ENDED" ? "warn" : ""}`}>{runState}</span>
        {snap?.held && <span className="chip warn" title="Plant paused while the agents decide">HELD · agents deciding</span>}
        <span className="chip mono">t = {snap?.time_min.toFixed(1) ?? "—"} min</span>
        {snap && snap.episode_status !== "RUNNING" && (
          <span className="chip warn" title="Episode outcome">{snap.episode_status}</span>
        )}
        {snap?.plant_trip && <span className="chip bad" title="A plant-trip SIF has latched">PLANT TRIP</span>}
        {sifs.length > 0 && <span className="chip bad" title="Latched safety functions">SIF {sifs.join(", ")}</span>}
        <span className={`chip ${unack ? "bad blink" : active ? "warn" : ""}`}>{active} alarms{unack ? ` · ${unack} unack` : ""}</span>
        <span className={`chip ${last?.fault ? "bad" : "ok"}`}>{last?.fault ? "FAULT" : "FDD ok"}</span>
        <button className="btn drawer-toggle" onClick={onToggleDrawer}>Controls</button>
      </div>
    </header>
  );
}
