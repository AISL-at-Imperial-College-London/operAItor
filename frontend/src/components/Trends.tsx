import { usePlant } from "../store";
import { Chart, type SeriesDef } from "./Chart";

export const C = {
  a: "#5ea8ff", b: "#f2a65a", c: "#5fd39a", d: "#b38cff", e: "#4fd1d9", f: "#ff7eb6",
  sp: "#9aa4b2", limit: "#ff5d5d",
};

const pv = (key: string, label: string, color = C.a): SeriesDef[] => [
  { key, label, color },
  { key: `${key}_sp`, label: "SP", color: C.sp, dash: true },
];

const CHARTS: { title: string; unit: string; series: SeriesDef[] }[] = [
  { title: "Reactor temperature", unit: "K", series: pv("r_temp", "T_R") },
  { title: "Reactor pressure", unit: "bar", series: pv("r_press", "P_R") },
  { title: "Reactor holdup", unit: "kmol", series: pv("r_level", "M_R") },
  { title: "Column pressure", unit: "bar", series: pv("col_press", "P_COL") },
  { title: "Top temperature", unit: "K", series: pv("top_temp", "T_TOP") },
  { title: "Bottom temperature", unit: "K", series: pv("bottom_temp", "T_BOT") },
  { title: "Reboiler holdup", unit: "kmol", series: pv("rb_level", "M_B") },
  { title: "Reflux drum holdup", unit: "kmol", series: pv("dr_level", "M_D") },
  {
    title: "Compositions (acetone)", unit: "mol frac",
    series: [
      { key: "r_x", label: "x_R", color: C.a },
      { key: "dist_x", label: "x_D", color: C.c },
      { key: "btm_x", label: "x_B", color: C.b },
    ],
  },
  {
    title: "Flows", unit: "kmol/h",
    series: [
      { key: "feed", label: "Feed", color: C.a },
      { key: "outflow", label: "R out", color: C.b },
      { key: "reflux", label: "Reflux", color: C.c },
      { key: "distillate", label: "D", color: C.d },
      { key: "bottoms", label: "B", color: C.e },
    ],
  },
  {
    title: "Valve / utility actuals", unit: "fraction",
    series: [
      { key: "heater", label: "Heater", color: C.f },
      { key: "steam", label: "Steam", color: C.b },
      { key: "cooling", label: "CW valve", color: C.e },
      { key: "pcv", label: "H2 PCV", color: C.a },
      { key: "bdv", label: "H2 BDV", color: C.d },
      { key: "cool_eff", label: "Cond. eff.", color: C.c, dash: true },
    ],
  },
  { title: "H2 vent flow", unit: "kmol/h", series: [{ key: "h2", label: "F_H2", color: C.a }] },
];

export function Trends() {
  const trend = usePlant((s) => s.trend);
  const version = usePlant((s) => s.trendVersion);
  return (
    <div className="grid charts">
      {CHARTS.map((c) => (
        <Chart key={c.title} title={c.title} unit={c.unit} series={c.series} data={trend} version={version} />
      ))}
    </div>
  );
}
