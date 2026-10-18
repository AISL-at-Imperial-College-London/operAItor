import { navigate, usePlant } from "../store";
import type { Snapshot } from "../types";
import { Overview } from "./Overview";

// Key process variables, grouped by unit: [label, measurement tag, unit, decimals]
const KEY_VARS: [string, [string, string, string, number][]][] = [
  ["Reactor R-101", [["Temperature", "cstr_temperature_K", "K", 1], ["Pressure", "cstr_pressure_bar", "bar", 3],
                     ["Holdup", "cstr_holdup_kmol", "kmol", 1], ["Acetone x", "cstr_x_acetone", "", 3]]],
  ["Column T-201", [["Pressure", "column_pressure_bar", "bar", 3], ["Top T", "top_temperature_K", "K", 1],
                    ["Bottom T", "bottom_temperature_K", "K", 1], ["Lowest tray liquid", "column_min_tray_holdup_kmol", "kmol", 2]]],
  ["Inventories & quality", [["Reflux drum", "drum_holdup_kmol", "kmol", 1], ["Reboiler", "reboiler_holdup_kmol", "kmol", 1],
                             ["Distillate x", "distillate_x_acetone", "", 3], ["Bottoms x", "bottoms_x_acetone", "", 3]]],
];

function tagState(s: Snapshot, tag: string): string {
  const st = s.alarms.filter((a) => a.tag === tag).map((a) => a.state);
  if (st.includes("ACTIVE_UNACK") || st.includes("RTN_UNACK")) return "unack";
  if (st.includes("ACTIVE_ACK")) return "alarm";
  return "";
}

function KeyVariables({ snap }: { snap: Snapshot }) {
  return (
    <div className="kpi-groups">
      {KEY_VARS.map(([group, vars]) => (
        <section key={group} className="card kpi-group">
          <h3>{group}</h3>
          <div className="kpis">
            {vars.map(([label, tag, unit, d]) => (
              <div key={tag} className={`kpi ${tagState(snap, tag)}`} title={tag}>
                <span className="kpi-label">{label}</span>
                <span className="kpi-value num">{snap.measurements[tag]?.toFixed(d) ?? "—"}<small>{unit}</small></span>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export const LOOP_NAME: Record<string, string> = {
  R_LIC: "Reactor level", R_TIC: "Reactor temperature", R_PIC: "Reactor pressure", RB_LIC: "Reboiler level",
  DR_LIC: "Reflux drum level", COL_PIC: "Column pressure", TOP_TIC: "Top temperature", BOT_TIC: "Bottom temperature",
};

function Loops({ snap }: { snap: Snapshot }) {
  return (
    <section className="card">
      <h3>Control loops <span className="dim small">click a loop to change it</span></h3>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Loop</th><th>Mode</th><th className="num">PV</th><th className="num">Setpoint</th><th>Output</th></tr></thead>
          <tbody>
            {Object.entries(snap.controllers).map(([id, c]) => {
              const frac = (c.last_output - c.mv_min) / (c.mv_max - c.mv_min || 1);
              const sat = frac > 0.97 || frac < 0.03;
              return (
                <tr key={id} className="clickable" tabIndex={0} title={`Change ${id} in the Operate panel`}
                  onClick={() => navigate({ drawer: "operate", loop: id })}
                  onKeyDown={(e) => e.key === "Enter" && navigate({ drawer: "operate", loop: id })}>
                  <td><b>{id}</b> <span className="dim small">{LOOP_NAME[id]}</span></td>
                  <td><span className={`pill ${c.mode === "AUTO" ? "" : "pill-warn"}`}>{c.mode}</span></td>
                  <td className="num">{c.pv?.toPrecision(4) ?? "—"}</td>
                  <td className="num">{c.setpoint.toPrecision(4)}</td>
                  <td><span className={`opbar ${sat ? "sat" : ""}`} title={sat ? "saturated" : ""}>
                    <i style={{ width: `${Math.max(0, Math.min(1, frac)) * 100}%` }} /></span>
                    <span className="num small">{c.last_output.toPrecision(3)}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ActiveAlarms({ snap, onAll }: { snap: Snapshot; onAll: () => void }) {
  const active = snap.alarms.filter((a) => a.state !== "NORMAL").sort((a, b) => a.priority - b.priority);
  if (!active.length) return null;
  return (
    <section className="card alarm-strip">
      <h3>Active alarms <span className="dim">({active.length})</span>
        <span className="spacer" />
        <button className="btn sm ghost" onClick={onAll}>All alarms →</button></h3>
      <ul className="alarm-list">
        {active.slice(0, 6).map((a) => (
          <li key={a.id} className={a.state}>
            <span className={`prio p${a.priority}`}>{a.priority}</span><b>{a.id}</b>
            <span className="dim">{a.description}</span>
            <span className="num">{a.value?.toFixed(3)} / {a.limit} {a.unit}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PlantTab({ onAlarms }: { onAlarms: () => void }) {
  const snap = usePlant((s) => s.snap);
  if (!snap) return null;
  return (
    <div className="stack">
      <ActiveAlarms snap={snap} onAll={onAlarms} />
      <Overview />
      <KeyVariables snap={snap} />
      <Loops snap={snap} />
    </div>
  );
}
