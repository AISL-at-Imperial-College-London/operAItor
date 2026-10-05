import { usePlant } from "../store";
import type { Snapshot } from "../types";

const ROWS: Record<string, [string, string, string, number][]> = {
  "R-101 Reactor": [
    ["Temperature", "cstr_temperature_K", "K", 2], ["Pressure", "cstr_pressure_bar", "bar", 3],
    ["Holdup", "cstr_holdup_kmol", "kmol", 1], ["Acetone x", "cstr_x_acetone", "", 3],
    ["H2 vent", "cstr_h2_vent_kmol_h", "kmol/h", 1],
  ],
  "T-201 Column": [
    ["Pressure", "column_pressure_bar", "bar", 3], ["Top T", "top_temperature_K", "K", 2],
    ["Bottom T", "bottom_temperature_K", "K", 2], ["Reboiler holdup", "reboiler_holdup_kmol", "kmol", 1],
    ["Drum holdup", "drum_holdup_kmol", "kmol", 1], ["Distillate x", "distillate_x_acetone", "", 3],
    ["Bottoms x", "bottoms_x_acetone", "", 3],
  ],
};
const LOOPS: Record<string, string[]> = {
  "R-101 Reactor": ["R_LIC", "R_TIC", "R_PIC"],
  "T-201 Column": ["COL_PIC", "TOP_TIC", "BOT_TIC", "RB_LIC", "DR_LIC"],
};

function tagClass(s: Snapshot, tag: string) {
  const st = s.alarms.filter((a) => a.tag === tag).map((a) => a.state);
  if (st.includes("ACTIVE_UNACK") || st.includes("RTN_UNACK")) return "alm-unack";
  if (st.includes("ACTIVE_ACK")) return "alm-ack";
  return "";
}

export function Faceplates() {
  const snap = usePlant((s) => s.snap);
  if (!snap) return null;
  return (
    <div className="grid faceplates">
      {Object.entries(ROWS).map(([unit, rows]) => (
        <div className="card" key={unit}>
          <div className="card-title">{unit}</div>
          <table className="kv">
            <tbody>
              {rows.map(([label, tag, u, d]) => (
                <tr key={tag}>
                  <td>{label}</td>
                  <td className={`num ${tagClass(snap, tag)}`}>{snap.measurements[tag]?.toFixed(d) ?? "—"} <span className="unit">{u}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="loops">
            <thead><tr><th>Loop</th><th>Mode</th><th>SP</th><th>PV</th><th>OP</th></tr></thead>
            <tbody>
              {LOOPS[unit].map((id) => {
                const c = snap.controllers[id];
                const frac = (c.last_output - c.mv_min) / (c.mv_max - c.mv_min || 1);
                return (
                  <tr key={id}>
                    <td>{id}</td>
                    <td><span className={`pill ${c.mode === "AUTO" ? "" : "pill-warn"}`}>{c.mode}</span></td>
                    <td className="num">{c.setpoint.toPrecision(4)}</td>
                    <td className="num">{c.pv?.toPrecision(4) ?? "—"}</td>
                    <td className="num">
                      <span className={`opbar ${frac > 0.97 || frac < 0.03 ? "sat" : ""}`}><i style={{ width: `${Math.max(0, Math.min(1, frac)) * 100}%` }} /></span>
                      {c.last_output.toPrecision(3)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
      <div className="card">
        <div className="card-title">Pump trains</div>
        <table className="loops">
          <thead><tr><th>Train</th><th>Running</th><th>Standby</th><th></th></tr></thead>
          <tbody>
            {Object.entries(snap.pumps).map(([id, p]) => (
              <tr key={id}>
                <td>{id}</td>
                <td className={p.selected_failed ? "alm-unack" : ""}>{p.active_pump}</td>
                <td>{p.active_pump === p.primary ? p.spare : p.primary}</td>
                <td>{p.selected_failed ? <span className="pill pill-bad">FAILED</span> : <span className="pill pill-ok">OK</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="card-title" style={{ marginTop: 12 }}>Safety</div>
        <div className="kv-line">SIF latched: <b>{snap.sif_latched.join(", ") || "none"}</b></div>
        <div className="kv-line">Plant trip: <b className={snap.plant_trip ? "alm-unack" : ""}>{snap.plant_trip ? "LATCHED" : "no"}</b></div>
        <div className="kv-line">Last action: <span className="dim">{snap.last_action}</span></div>
      </div>
    </div>
  );
}
