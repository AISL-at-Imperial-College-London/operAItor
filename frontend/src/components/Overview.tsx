import type { CSSProperties } from "react";
import { usePlant } from "../store";
import type { Snapshot } from "../types";

// Artwork pixel coordinates (1670 × 942) of the baked-in value boxes, lamps and
// pump badges, detected from static/process_overview.png. Live elements are
// drawn slightly larger than the artwork boxes so they fully mask them.
const W = 1670, H = 942, CROP_TOP = 62; // hide the artwork's static header bar
const PAD_W = 6, PAD_H = 5;

const VALUES: [string, number, number, number, number][] = [
  ["r_temp", 198, 367, 157, 34], ["r_press", 198, 406, 158, 33], ["r_level", 198, 442, 157, 32],
  ["r_x", 198, 482, 157, 35], ["heater", 245.5, 651, 103, 34], ["h2", 638, 148, 139, 32],
  ["col_press", 802, 306, 145, 33], ["top_temp", 802, 372, 146, 35], ["feed_temp", 787, 453, 156, 34],
  ["bottom_temp", 802, 562, 145, 32], ["reflux", 1208, 332, 123, 32], ["cooling", 1478.5, 133, 109, 32],
  ["dr_level", 1592, 244, 122, 31], ["dist_x", 1590, 281, 121, 32], ["distillate", 1591, 318, 122, 31],
  ["steam", 1460, 634, 105, 33], ["rb_level", 1212, 711, 151, 31], ["bottoms", 1469, 730, 120, 32],
  ["btm_x", 1469, 763, 120, 32],
];

const LAMPS: [number, number, string | null][] = [
  [299, 366, "cstr_temperature_K"], [299, 404, "cstr_pressure_bar"], [300, 443, "cstr_holdup_kmol"],
  [299, 480, null], [548, 147, null], [897, 308, "column_pressure_bar"], [896, 374, "top_temperature_K"],
  [896, 454, null], [896, 564, "bottom_temperature_K"], [1126, 333, null], [1406, 132, null],
  [1510, 246, "drum_holdup_kmol"], [1510, 282, "distillate_x_acetone"], [1510, 318, null], [174, 650, null],
  [1387, 634, null], [1127, 711, "reboiler_holdup_kmol"], [1388, 730, null], [1388, 764, "bottoms_x_acetone"],
];

// The artwork's "Executive Alarms" / legend box only shows 4 SIFs; it is covered by a live panel
// with all 11 safety functions (artwork pixels: left, top, width, height).
const SIF_BOX = [22, 762, 846, 168] as const;
export const SIFS: [string, string, boolean][] = [
  ["R_T_HH", "Reactor T high", false], ["R_P_HH", "Reactor P high", false], ["R_P_HHH", "Reactor P high-high", true],
  ["R_L_HH", "Reactor level high", false], ["R_L_LL", "Reactor level low", false], ["RB_L_HH", "Reboiler level high", true],
  ["RB_L_LL", "Reboiler level low", false], ["DR_L_HH", "Drum level high", true], ["DR_L_LL", "Drum level low", true],
  ["COL_P_HH", "Column P high", true], ["COL_L_LL", "Column trays dry", true],
];

const PUMPS: [string, "A" | "B", number, number][] = [
  ["FEED", "A", 154, 308], ["FEED", "B", 230, 310], ["CSTR_OUTLET", "A", 498, 729], ["CSTR_OUTLET", "B", 578.5, 729],
  ["REFLUX", "A", 1154, 478], ["REFLUX", "B", 1232, 478.5], ["DISTILLATE", "A", 1343, 540], ["DISTILLATE", "B", 1421, 540],
  ["BOTTOMS", "A", 1155, 875], ["BOTTOMS", "B", 1235.5, 874.5],
];

const f = (v: number | null | undefined, d: number) => (v == null ? "—" : v.toFixed(d));

function labels(s: Snapshot): Record<string, string> {
  const t = s.trend;
  return {
    r_temp: `T_R = ${f(t.r_temp, 1)} K`, r_press: `P_R = ${f(t.r_press, 3)} bar`,
    r_level: `M_R = ${f(t.r_level, 1)} kmol`, r_x: `x_R = ${f(t.r_x, 3)}`,
    heater: `HV = ${f((t.heater ?? 0) * 100, 0)} %`, h2: `F_H2 = ${f(t.h2, 1)} kmol/h`,
    col_press: `P_COL = ${f(t.col_press, 3)} bar`, top_temp: `T_TOP = ${f(t.top_temp, 1)} K`,
    feed_temp: `T_FEED = ${f(t.feed_temp, 1)} K`, bottom_temp: `T_BOT = ${f(t.bottom_temp, 1)} K`,
    reflux: `R = ${f(t.reflux, 1)} kmol/h`, cooling: `CWV = ${f((t.cooling ?? 0) * 100, 0)} %`,
    dr_level: `M_D = ${f(t.dr_level, 1)} kmol`, dist_x: `x_D = ${f(t.dist_x, 3)}`,
    distillate: `D = ${f(t.distillate, 1)} kmol/h`, steam: `SV = ${f((t.steam ?? 0) * 100, 0)} %`,
    rb_level: `M_B = ${f(t.rb_level, 1)} kmol`, bottoms: `B = ${f(t.bottoms, 1)} kmol/h`,
    btm_x: `x_BOT = ${f(t.btm_x, 3)}`,
  };
}

const pos = (x: number, y: number, w?: number, h?: number): CSSProperties => ({
  left: `${(100 * x) / W}%`, top: `${(100 * y) / H}%`,
  ...(w ? { width: `${(100 * w) / W}%` } : {}), ...(h ? { height: `${(100 * h) / H}%` } : {}),
});

export function Overview() {
  const snap = usePlant((s) => s.snap);
  if (!snap) return null;
  const text = labels(snap);
  const tagState = (tag: string | null) => {
    if (!tag) return "NORMAL";
    const st = snap.alarms.filter((a) => a.tag === tag).map((a) => a.state);
    return st.includes("ACTIVE_UNACK") ? "ACTIVE_UNACK" : st.includes("RTN_UNACK") ? "RTN_UNACK"
      : st.includes("ACTIVE_ACK") ? "ACTIVE_ACK" : "NORMAL";
  };
  return (
    <div className="card overview-card">
      <div className="overview" style={{ aspectRatio: `${W} / ${H - CROP_TOP}` }}>
        <div className="overview-inner" style={{ top: `${(-100 * CROP_TOP) / (H - CROP_TOP)}%` }}>
          <img src="/static/process_overview.png" alt="Process flow diagram" draggable={false} />
          {VALUES.map(([k, x, y, w, h]) => (
            <div key={k} className="ov-value" style={pos(x, y, w + PAD_W, h + PAD_H)}>{text[k]}</div>
          ))}
          {LAMPS.map(([x, y, tag], i) => (
            <div key={i} className={`ov-lamp ${tagState(tag)}`} style={pos(x, y)} title={tag ?? ""} />
          ))}
          <div className="ov-sifs" style={pos(...SIF_BOX)}>
            <div className="ov-sifs-title">Safety functions (SIF)<span>trip = plant trip · lamps: white normal, amber alarm, red trip</span></div>
            <div className="ov-sifs-grid">
              {SIFS.map(([id, label, trip]) => (
                <div key={id} className={`ov-sif ${snap.sif_latched.includes(id) ? "latched" : ""}`} title={`${id}${trip ? " (plant trip)" : ""}`}>
                  <i />{label}{trip && <b>trip</b>}
                </div>
              ))}
            </div>
          </div>
          {PUMPS.map(([train, side, x, y]) => {
            const p = snap.pumps[train];
            const id = side === "A" ? p.primary : p.spare;
            const selected = p.active_pump === id;
            const failed = side === "A" ? p.primary_failed : p.spare_failed;
            const cls = selected ? (failed ? "fail selected" : "run") : failed ? "fail" : "stby";
            return (
              <div key={id} className={`ov-pump ${cls}`} style={pos(x, y, 72, 33)} title={id}>
                {side} {selected ? (failed ? "FAIL" : "RUN") : failed ? "FAIL" : "STBY"}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
