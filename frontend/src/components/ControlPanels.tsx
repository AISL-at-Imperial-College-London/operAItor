import { useEffect, useState, type ReactNode } from "react";
import { send, usePlant } from "../store";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function Num({ value, onChange, step = "any", min, max }: { value: number; onChange: (v: number) => void; step?: string | number; min?: number; max?: number }) {
  return <input type="number" value={Number.isFinite(value) ? value : ""} step={step} min={min} max={max}
    onChange={(e) => onChange(parseFloat(e.target.value))} />;
}

function Select({ value, options, onChange }: { value: string; options: (string | [string, string])[]; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => {
        const [v, l] = Array.isArray(o) ? o : [o, o];
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

const act = (action: Record<string, unknown>) => send({ type: "action", action });

export function OperatorPanel() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const [throughput, setThroughput] = useState(100);
  const [loop, setLoop] = useState("R_TIC");
  const [sp, setSp] = useState(0);
  const [mode, setMode] = useState("AUTO");
  const [manual, setManual] = useState(0);
  const [aux, setAux] = useState("H2_BDV");
  const [auxVal, setAuxVal] = useState(0);
  const [train, setTrain] = useState("REFLUX");

  // Seed the inputs from the live plant when the selection changes.
  useEffect(() => { if (snap) setThroughput(+snap.throughput_target.toFixed(2)); }, [snap?.episode]); // eslint-disable-line
  useEffect(() => {
    const c = snap?.controllers[loop];
    if (c) { setSp(+c.setpoint.toPrecision(5)); setMode(c.mode); setManual(+c.last_output.toPrecision(4)); }
  }, [loop, snap?.episode]); // eslint-disable-line
  useEffect(() => { if (snap) setAuxVal(snap.auxiliaries[aux] ?? 0); }, [aux, snap?.episode]); // eslint-disable-line

  if (!meta || !snap) return null;
  const L = meta.loops[loop];
  const c = snap.controllers[loop];
  const [alo, ahi] = meta.auxiliaries[aux];

  return (
    <div className="panel-body">
      <p className="hint">Restricted operator authority — the same actions the action agent may use.</p>
      <section>
        <h4>Throughput</h4>
        <div className="row">
          <Field label={`Fresh feed [kmol/h] (${meta.throughput_bounds.join("–")})`}><Num value={throughput} onChange={setThroughput} /></Field>
          <button className="btn" onClick={() => act({ throughput_target: throughput })}>Set</button>
        </div>
      </section>
      <section>
        <h4>Controller</h4>
        <Field label="Loop"><Select value={loop} options={Object.keys(meta.loops)} onChange={setLoop} /></Field>
        <div className="dim small">PV {c.pv?.toPrecision(5)} {L.sp_unit} · OP {c.last_output.toPrecision(4)} {L.mv_unit} · {c.mode}</div>
        <div className="row">
          <Field label={`Setpoint [${L.sp_unit}] (${L.sp_min}–${L.sp_max})`}><Num value={sp} min={L.sp_min} max={L.sp_max} onChange={setSp} /></Field>
          <button className="btn" onClick={() => act({ setpoints: { [loop]: sp } })}>Set SP</button>
        </div>
        <div className="row">
          <Field label="Mode"><Select value={mode} options={["AUTO", "MANUAL"]} onChange={setMode} /></Field>
          <button className="btn" onClick={() => act({ modes: { [loop]: mode } })}>Set mode</button>
        </div>
        <div className="row">
          <Field label={`Manual OP [${L.mv_unit}]`}><Num value={manual} onChange={setManual} /></Field>
          <button className="btn" disabled={c.mode !== "MANUAL"} title={c.mode !== "MANUAL" ? "Loop must be in MANUAL" : ""}
            onClick={() => act({ manual_outputs: { [loop]: manual } })}>Set OP</button>
        </div>
      </section>
      <section>
        <h4>Emergency auxiliaries</h4>
        <div className="row">
          <Field label="Device"><Select value={aux} options={Object.keys(meta.auxiliaries)} onChange={setAux} /></Field>
          <Field label={`Command (${alo}–${ahi})`}><Num value={auxVal} min={alo} max={ahi} step={0.05} onChange={setAuxVal} /></Field>
          <button className="btn" onClick={() => act({ auxiliary_commands: { [aux]: auxVal } })}>Apply</button>
        </div>
      </section>
      <section>
        <h4>Duty / standby pumps</h4>
        <Field label="Train"><Select value={train} options={Object.keys(meta.pump_trains)} onChange={setTrain} /></Field>
        <div className="row">
          <button className="btn" onClick={() => act({ start_spares: [train] })}>Start standby</button>
          <button className="btn" onClick={() => act({ select_primaries: [train] })}>Select primary</button>
        </div>
      </section>
    </div>
  );
}

function CampaignSection() {
  const meta = usePlant((s) => s.meta);
  const campaign = usePlant((s) => s.campaign);
  const snap = usePlant((s) => s.snap);
  const [name, setName] = useState("demo");
  const [rnd, setRnd] = useState({ seed: 0, n: 4, gap_min: 25, duration_min: 15 });
  const presets = Object.entries(meta?.campaigns ?? {});
  const running = campaign?.state === "running";
  const start = () => send({ type: "campaign_start", campaign: name, scenario: "nominal",
                              ...(name === "random" ? rnd : {}) });
  return (
    <section>
      <h4>Fault campaign (automated injection)</h4>
      <div className="row">
        <Field label="Campaign">
          <Select value={name} onChange={setName}
            options={[...presets.map(([k, d]) => [k, `${k} — ${d}`] as [string, string]), ["random", "random — seeded sequence"]]} />
        </Field>
      </div>
      {name === "random" && (
        <div className="row">
          <Field label="Seed"><Num value={rnd.seed} onChange={(v) => setRnd({ ...rnd, seed: v })} /></Field>
          <Field label="Faults"><Num value={rnd.n} onChange={(v) => setRnd({ ...rnd, n: v })} /></Field>
          <Field label="Gap [min]"><Num value={rnd.gap_min} onChange={(v) => setRnd({ ...rnd, gap_min: v })} /></Field>
          <Field label="Duration [min]"><Num value={rnd.duration_min} onChange={(v) => setRnd({ ...rnd, duration_min: v })} /></Field>
        </div>
      )}
      <div className="row">
        <button className="btn danger" onClick={start} title="Loads a fresh nominal episode and starts the plant">
          {running ? "Restart campaign" : "Start campaign"}
        </button>
        {running && <button className="btn" onClick={() => send({ type: "campaign_stop" })}>Stop &amp; report</button>}
      </div>
      {campaign && (
        <div className="campaign">
          <div className="kv-line"><b>{campaign.name}</b> <span className={`pill ${running ? "pill-warn" : "pill-ok"}`}>{campaign.state}</span></div>
          {campaign.items.map((it, i) => {
            const due = it.due_min - (snap?.time_min ?? 0);
            return (
              <div key={i} className="kv-line small">
                <span className={`pill ${it.status === "active" ? "pill-bad" : it.status === "cleared" ? "pill-ok" : ""}`}>{it.status}</span>{" "}
                {it.label} <span className="dim">@ {it.due_min.toFixed(1)} min
                  {it.status === "pending" && it.waiting ? " (waiting for the plant to settle)" : ""}
                  {it.status === "pending" && !it.waiting && due > 0 ? ` (in ${due.toFixed(1)})` : ""}
                  {it.duration_min ? `, ${it.duration_min} min` : ", persistent"}</span>
                {it.error && <span className="alm-unack"> {it.error}</span>}
              </div>
            );
          })}
          {campaign.report_path && <div className="dim small">Report: <code>{campaign.report_path.split(/[\/]/).pop()}</code> in campaign_results/</div>}
        </div>
      )}
    </section>
  );
}

export function MasterPanel() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const [dist, setDist] = useState({ target: "cooling_water_temperature_K", direction: "1", duration_min: 15 });
  const [actu, setActu] = useState({ target: "cstr_vent_valve", mode: "freeze", severity_pct: 50, duration_min: 0 });
  const [pump, setPump] = useState({ pump_id: "P-201A", duration_min: 0 });
  const [sens, setSens] = useState({ target: "cstr_pressure_bar", mode: "bias", bias_pct: -20, duration_min: 0 });
  const [proc, setProc] = useState({ fault: "catalyst_activity_loss", severity: 50, duration_min: 0 });
  if (!meta || !snap) return null;
  const inj = meta.injections;
  const inject = (kind: string, body: Record<string, unknown>) => send({ type: "inject", kind, ...body });
  const t = snap.truth;

  return (
    <div className="panel-body">
      <p className="hint warn">Ground-truth test controls. Never visible to the agents.</p>
      <CampaignSection />
      <section>
        <h4>Disturbance</h4>
        <Field label="Variable"><Select value={dist.target} options={Object.entries(inj.disturbances)} onChange={(v) => setDist({ ...dist, target: v })} /></Field>
        <div className="row">
          <Field label="Step"><Select value={dist.direction} onChange={(v) => setDist({ ...dist, direction: v })}
            options={[["1", `+${inj.disturbance_steps?.[dist.target] ?? "step"}`], ["-1", `−${inj.disturbance_steps?.[dist.target] ?? "step"}`]]} /></Field>
          <Field label="Duration [min]"><Num value={dist.duration_min} onChange={(v) => setDist({ ...dist, duration_min: v })} /></Field>
        </div>
        <button className="btn" onClick={() => inject("disturbance", { ...dist, direction: +dist.direction })}>Inject disturbance</button>
      </section>
      <section>
        <h4>Actuator fault</h4>
        <div className="row">
          <Field label="Target"><Select value={actu.target} options={inj.actuator_targets} onChange={(v) => setActu({ ...actu, target: v })} /></Field>
          <Field label="Mode"><Select value={actu.mode} options={inj.actuator_modes} onChange={(v) => setActu({ ...actu, mode: v })} /></Field>
        </div>
        <div className="row">
          <Field label="Severity [% nominal]"><Num value={actu.severity_pct} onChange={(v) => setActu({ ...actu, severity_pct: v })} /></Field>
          <Field label="Duration [min], 0 = persistent"><Num value={actu.duration_min} onChange={(v) => setActu({ ...actu, duration_min: v })} /></Field>
        </div>
        <button className="btn danger" onClick={() => inject("actuator", actu)}>Inject actuator fault</button>
      </section>
      <section>
        <h4>Pump trip</h4>
        <div className="row">
          <Field label="Pump"><Select value={pump.pump_id} options={meta.pump_ids} onChange={(v) => setPump({ ...pump, pump_id: v })} /></Field>
          <Field label="Duration [min], 0 = persistent"><Num value={pump.duration_min} onChange={(v) => setPump({ ...pump, duration_min: v })} /></Field>
        </div>
        <button className="btn danger" onClick={() => inject("pump", pump)}>Trip pump</button>
      </section>
      <section>
        <h4>Sensor fault</h4>
        <div className="row">
          <Field label="Sensor"><Select value={sens.target} options={inj.sensor_targets} onChange={(v) => setSens({ ...sens, target: v })} /></Field>
          <Field label="Mode"><Select value={sens.mode} options={["bias", "freeze"]} onChange={(v) => setSens({ ...sens, mode: v })} /></Field>
        </div>
        <div className="row">
          <Field label="Bias [% of value]"><Num value={sens.bias_pct} onChange={(v) => setSens({ ...sens, bias_pct: v })} /></Field>
          <Field label="Duration [min], 0 = persistent"><Num value={sens.duration_min} onChange={(v) => setSens({ ...sens, duration_min: v })} /></Field>
        </div>
        <button className="btn danger" onClick={() => inject("sensor", sens)}>Inject sensor fault</button>
      </section>
      <section>
        <h4>Process / utility fault</h4>
        <Field label="Fault"><Select value={proc.fault} options={inj.process_faults} onChange={(v) => setProc({ ...proc, fault: v })} /></Field>
        <div className="row">
          <Field label="Severity (loss %, leaks kmol/h)"><Num value={proc.severity} onChange={(v) => setProc({ ...proc, severity: v })} /></Field>
          <Field label="Duration [min], 0 = persistent"><Num value={proc.duration_min} onChange={(v) => setProc({ ...proc, duration_min: v })} /></Field>
        </div>
        <button className="btn danger" onClick={() => inject("process", proc)}>Inject process fault</button>
      </section>
      <button className="btn" onClick={() => send({ type: "clear_injections" })}>End all manual injections</button>

      <section className="truth">
        <h4>Ground truth</h4>
        {t.schedule.map((e) => (
          <div key={e.name} className="kv-line"><span className={`pill ${e.phase === "active" ? "pill-bad" : ""}`}>{e.phase}</span> {e.name}
            <span className="dim"> @ {e.start_min.toFixed(0)}{e.end_min != null ? `–${e.end_min.toFixed(0)}` : ""} min</span></div>
        ))}
        {(["runtime", "actuator", "sensor", "pump"] as const).map((k) => (
          <div key={k} className="kv-line">{k}: <b>{t[k].join(", ") || "none"}</b></div>
        ))}
      </section>
    </div>
  );
}
