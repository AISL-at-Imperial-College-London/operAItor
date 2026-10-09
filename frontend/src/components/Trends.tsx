import { usePlant } from "../store";
import { Chart, type SeriesDef } from "./Chart";

export const C = {
  a: "#5ea8ff", b: "#f2a65a", c: "#5fd39a", d: "#b38cff", e: "#4fd1d9", f: "#ff7eb6",
  sp: "#9aa4b2", limit: "#ff5d5d",
};

const pv = (key: string, label: string, color = C.a): SeriesDef[] => [
  { key, label, color },
  { key: `${key}_sp`, label: "setpoint", color: C.sp, dash: true },
];

type ChartDef = { title: string; unit: string; series: SeriesDef[] };
const GROUPS: [string, ChartDef[]][] = [
  ["Reactor R-101", [
    { title: "Temperature", unit: "K", series: pv("r_temp", "T") },
    { title: "Pressure", unit: "bar", series: pv("r_press", "P") },
    { title: "Liquid holdup", unit: "kmol", series: pv("r_level", "holdup") },
    { title: "H2 vent flow", unit: "kmol/h", series: [{ key: "h2", label: "H2 vent", color: C.a }] },
  ]],
  ["Column T-201", [
    { title: "Pressure", unit: "bar", series: pv("col_press", "P") },
    { title: "Top temperature", unit: "K", series: pv("top_temp", "T top") },
    { title: "Bottom temperature", unit: "K", series: pv("bottom_temp", "T bottom") },
    { title: "Reflux drum holdup", unit: "kmol", series: pv("dr_level", "drum") },
    { title: "Reboiler holdup", unit: "kmol", series: pv("rb_level", "reboiler") },
  ]],
  ["Quality, flows and valves", [
    { title: "Acetone mole fraction", unit: "", series: [
      { key: "r_x", label: "reactor", color: C.a }, { key: "dist_x", label: "distillate", color: C.c },
      { key: "btm_x", label: "bottoms", color: C.b }] },
    { title: "Flows", unit: "kmol/h", series: [
      { key: "feed", label: "feed", color: C.a }, { key: "outflow", label: "reactor out", color: C.b },
      { key: "reflux", label: "reflux", color: C.c }, { key: "distillate", label: "distillate", color: C.d },
      { key: "bottoms", label: "bottoms", color: C.e }] },
    { title: "Valves and utilities", unit: "fraction", series: [
      { key: "heater", label: "heater", color: C.f }, { key: "steam", label: "steam", color: C.b },
      { key: "cooling", label: "CW valve", color: C.e }, { key: "pcv", label: "H2 PCV", color: C.a },
      { key: "bdv", label: "H2 BDV", color: C.d }, { key: "cool_eff", label: "condenser eff.", color: C.c, dash: true }] },
  ]],
];

export function Trends() {
  const trend = usePlant((s) => s.trend);
  const version = usePlant((s) => s.trendVersion);
  return (
    <div className="stack">
      {GROUPS.map(([group, charts]) => (
        <section key={group}>
          <h2 className="section-title">{group}</h2>
          <div className="grid charts">
            {charts.map((c) => (
              <Chart key={group + c.title} title={c.title} unit={c.unit} series={c.series} data={trend} version={version} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
