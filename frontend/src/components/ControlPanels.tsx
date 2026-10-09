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
      <p className="hint">You have the same authority as the action agent: setpoints, throughput, standby pumps and the
        emergency auxiliaries. Safety functions cannot be bypassed.</p>
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
    <details className="sub">
      <summary>Fault campaign <span className="dim small">scripted sequence of faults</span></summary>
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
          {campaign.report_path && <div className="dim small">Report: <code>{campaign.report_path.split(/[\/]/).pop()}</code></div>}
        </div>
      )}
    </details>
  );
}

type Kind = "pump" | "actuator" | "sensor" | "process" | "disturbance";
const KINDS: [Kind, string][] = [["pump", "Pump trip"], ["actuator", "Valve / actuator"], ["sensor", "Sensor"],
                                 ["process", "Process / utility"], ["disturbance", "Disturbance"]];

export function FaultsPanel() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const [kind, setKind] = useState<Kind>("pump");
  const [duration, setDuration] = useState(0);
  const [pump, setPump] = useState("P-201A");
  const [actu, setActu] = useState({ target: "cstr_vent_valve", mode: "freeze", severity_pct: 50 });
  const [sens, setSens] = useState({ target: "cstr_pressure_bar", mode: "bias", bias_pct: -20 });
  const [proc, setProc] = useState({ fault: "catalyst_activity_loss", severity: 50 });
  const [dist, setDist] = useState({ target: "cooling_water_temperature_K", direction: "1" });
  if (!meta || !snap) return null;
  const inj = meta.injections;
  const t = snap.truth;
  const inject = () => {
    const body: Record<string, unknown> =
      kind === "pump" ? { pump_id: pump } : kind === "actuator" ? actu : kind === "sensor" ? sens
      : kind === "process" ? proc : { target: dist.target, direction: +dist.direction };
    send({ type: "inject", kind, ...body, duration_min: kind === "disturbance" && !duration ? 15 : duration });
  };
  const active = [...t.schedule.filter((e) => e.phase === "active").map((e) => e.name),
                  ...(["runtime", "actuator", "sensor", "pump"] as const).flatMap((k) => t[k])];

  return (
    <div className="panel-body">
      <p className="hint warn">Test controls: what you inject here is ground truth and is never shown to the agents.</p>
      <section>
        <h4>Inject a fault</h4>
        <div className="seg wrap" role="radiogroup" aria-label="Fault type">
          {KINDS.map(([k, l]) => <button key={k} role="radio" aria-checked={kind === k} className={kind === k ? "on" : ""}
            onClick={() => setKind(k)}>{l}</button>)}
        </div>
        {kind === "pump" && (
          <Field label="Pump"><Select value={pump} options={meta.pump_ids} onChange={setPump} /></Field>
        )}
        {kind === "actuator" && (<>
          <div className="row">
            <Field label="Valve / actuator"><Select value={actu.target} options={inj.actuator_targets} onChange={(v) => setActu({ ...actu, target: v })} /></Field>
            <Field label="Failure"><Select value={actu.mode} options={inj.actuator_modes} onChange={(v) => setActu({ ...actu, mode: v })} /></Field>
          </div>
          <Field label="Severity [% of nominal]"><Num value={actu.severity_pct} onChange={(v) => setActu({ ...actu, severity_pct: v })} /></Field>
        </>)}
        {kind === "sensor" && (<>
          <div className="row">
            <Field label="Sensor"><Select value={sens.target} options={inj.sensor_targets} onChange={(v) => setSens({ ...sens, target: v })} /></Field>
            <Field label="Failure"><Select value={sens.mode} options={["bias", "freeze"]} onChange={(v) => setSens({ ...sens, mode: v })} /></Field>
          </div>
          {sens.mode === "bias" && <Field label="Bias [% of value]"><Num value={sens.bias_pct} onChange={(v) => setSens({ ...sens, bias_pct: v })} /></Field>}
        </>)}
        {kind === "process" && (<>
          <Field label="Fault"><Select value={proc.fault} options={inj.process_faults} onChange={(v) => setProc({ ...proc, fault: v })} /></Field>
          <Field label="Severity (loss %, or leak kmol/h)"><Num value={proc.severity} onChange={(v) => setProc({ ...proc, severity: v })} /></Field>
        </>)}
        {kind === "disturbance" && (
          <div className="row">
            <Field label="Variable"><Select value={dist.target} options={Object.entries(inj.disturbances)} onChange={(v) => setDist({ ...dist, target: v })} /></Field>
            <Field label="Step"><Select value={dist.direction} onChange={(v) => setDist({ ...dist, direction: v })}
              options={[["1", `+${inj.disturbance_steps?.[dist.target] ?? "step"}`], ["-1", `−${inj.disturbance_steps?.[dist.target] ?? "step"}`]]} /></Field>
          </div>
        )}
        <Field label={kind === "disturbance" ? "Duration [min]" : "Duration [min] (0 = until cleared)"}>
          <Num value={duration} min={0} onChange={setDuration} /></Field>
        <div className="row">
          <button className="btn danger" onClick={inject}>Inject</button>
          <button className="btn" onClick={() => send({ type: "clear_injections" })} disabled={!active.length}>Clear all injections</button>
        </div>
      </section>
      <section>
        <h4>Active injections</h4>
        {active.length ? active.map((a) => <div key={a} className="kv-line"><span className="pill pill-bad">active</span> {a}</div>)
          : <p className="dim small">None.</p>}
        {t.schedule.some((e) => e.phase !== "active") && (
          <details className="sub"><summary>Scheduled / finished</summary>
            {t.schedule.filter((e) => e.phase !== "active").map((e) => (
              <div key={e.name} className="kv-line small"><span className="pill">{e.phase}</span> {e.name}
                <span className="dim"> @ {e.start_min.toFixed(0)}{e.end_min != null ? `–${e.end_min.toFixed(0)}` : ""} min</span></div>))}
          </details>
        )}
      </section>
      <CampaignSection />
    </div>
  );
}
