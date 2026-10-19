import { useEffect, useRef, useState } from "react";
import { usePlant } from "../store";

// The P&ID (public/static/pid.svg, the same drawing as docs/pid.md), shown live: each controller's measured value,
// and every safety function that is latched lights up, here and in the cause & effect matrix below it.
const DECIMALS: Record<string, number> = { K: 1, bar: 3, kmol: 1 };

export function PidView() {
  const snap = usePlant((s) => s.snap);
  const meta = usePlant((s) => s.meta);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/static/pid.svg")
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`${r.status}`))))
      .then(setSvg, (e) => setError(`could not load the P&ID (${(e as Error).message})`));
  }, []);

  useEffect(() => {
    const root = ref.current;
    if (!root || !svg || !snap || !meta) return;
    root.querySelectorAll<SVGGElement>("[data-sif]").forEach((g) => {
      g.classList.toggle("on", (g.dataset.sif ?? "").split(" ").some((c) => snap.sif_latched.includes(c)));
    });
    root.querySelectorAll<SVGTextElement>("[data-pv]").forEach((t) => {
      const loop = t.dataset.pv ?? "";
      const pv = snap.controllers[loop]?.pv;
      const unit = meta.loops[loop]?.sp_unit ?? "";
      t.textContent = pv == null ? "" : `${pv.toFixed(DECIMALS[unit] ?? 2)} ${unit}`;
    });
  }, [svg, snap, meta]);

  return (
    <section className="card pid-card">
      <h3>P&amp;ID <span className="dim small">live: controller values in blue; a latched safety function fills red, here and in
        the matrix below</span></h3>
      {error ? <p className="bad-text">{error}</p>
        : <div className="pid" ref={ref} role="img" aria-label="P&ID of the plant"
            dangerouslySetInnerHTML={{ __html: svg ?? "" }} />}
      <CauseEffect />
    </section>
  );
}

function CauseEffect() {
  const ce = usePlant((s) => s.meta?.cause_effect);
  const latched = usePlant((s) => s.snap?.sif_latched ?? []);
  if (!ce) return null;
  return (
    <details className="sub ce" open>
      <summary>Safety functions: cause &amp; effect <span className="dim small">· generated from the safety system</span></summary>
      <div className="tw">
        <table className="ce-table">
          <thead>
            <tr><th>SIF</th><th>Trips at</th>{ce.effects.map(([name, tag]) => <th key={name} title={tag}>{name}</th>)}<th>Plant trip</th></tr>
          </thead>
          <tbody>
            {ce.rows.map((r) => (
              <tr key={r.sif} className={latched.includes(r.sif) ? "on" : ""} title={`${r.description}; resets when ${r.reset}`}>
                <td><b>{r.sif}</b></td><td className="num">{r.trip}</td>
                {ce.effects.map(([name]) => <td key={name} className={r.effects[name] ? "act" : "dim"}>{r.effects[name] || "·"}</td>)}
                <td className={r.plant_trip ? "act" : "dim"}>{r.plant_trip ? "LATCH" : "·"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="dim small">STOP / CLOSE: driven to zero · OPEN: fully open · ≤40 %: limited to 40 % of design · LATCH: the plant
        trips. Hover a row for the reset value; hover a column for the valve or pump on the P&amp;ID.</p>
    </details>
  );
}
