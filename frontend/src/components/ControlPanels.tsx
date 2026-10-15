import { useEffect, useState, type ReactNode } from "react";
import { send, usePlant } from "../store";
import { LOOP_NAME } from "./PlantTab";

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

const PUMP_TRAIN_NAME: Record<string, string> = {
  FEED: "Feed", CSTR_OUTLET: "Reactor outlet", REFLUX: "Reflux", DISTILLATE: "Distillate", BOTTOMS: "Bottoms",
};
const AUX_NAME: Record<string, [string, string]> = {
  H2_BDV: ["H₂ blowdown valve", "vents reactor hydrogen to the flare to bring the pressure down"],
  EMERGENCY_COOLING: ["Reactor emergency cooling", "cools the reactor with cooling water"],
};

/** Everything the operator (or the action agent) may change, one section per kind of lever. */
export function OperatorPanel() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const nav = usePlant((s) => s.nav);
  const [loop, setLoop] = useState("R_TIC");
  const [value, setValue] = useState(0);     // setpoint (AUTO) or output (MANUAL) of the selected loop
  const [feed, setFeed] = useState(100);
  const [aux, setAux] = useState<Record<string, number>>({});

  const c = snap?.controllers[loop];
  // Seed the inputs from the live plant when the selection or the episode changes.
  useEffect(() => { if (nav?.loop) setLoop(nav.loop); }, [nav]);
  useEffect(() => { if (c) setValue(+(c.mode === "MANUAL" ? c.last_output : c.setpoint).toPrecision(5)); }, [loop, c?.mode, snap?.episode]); // eslint-disable-line
  useEffect(() => { if (snap) setFeed(+snap.throughput_target.toFixed(1)); }, [snap?.episode]); // eslint-disable-line
  useEffect(() => { if (snap) setAux(Object.fromEntries(Object.entries(snap.auxiliaries).map(([k, v]) => [k, Math.round(100 * v)]))); }, [snap?.episode]); // eslint-disable-line

  if (!meta || !snap || !c) return null;
  const L = meta.loops[loop];
  const manual = c.mode === "MANUAL";
  const unack = snap.alarms.some((a) => a.state.endsWith("UNACK"));
  const [flo, fhi] = meta.throughput_bounds;

  return (
    <div className="panel-body operate">
      <section>
        <h4>Alarms and safety</h4>
        <div className="row">
          <button className="btn" disabled={!unack} onClick={() => send({ type: "ack_all" })}>Acknowledge all alarms</button>
          <button className="btn" disabled={!snap.sif_latched.length} onClick={() => send({ type: "reset_sif" })}
            title="Reset tripped safety functions whose variable is back in its normal band">Reset safety functions</button>
        </div>
      </section>

      <section>
        <h4>Pumps</h4>
        <div className="pump-list">
          {Object.entries(snap.pumps).map(([train, p]) => {
            const onSpare = p.active_pump === p.spare;
            const runningFailed = onSpare ? p.spare_failed : p.primary_failed;
            const target = onSpare ? p.primary : p.spare;
            const targetFailed = onSpare ? p.primary_failed : p.spare_failed;
            return (
              <div key={train} className={`pump-row ${runningFailed ? "failed" : ""}`}>
                <span className="pump-name">{PUMP_TRAIN_NAME[train] ?? train}</span>
                <span className={`pill ${runningFailed ? "pill-bad" : "pill-ok"}`}>{p.active_pump} {runningFailed ? "failed" : "running"}</span>
                <button className={`btn sm ${runningFailed && !targetFailed ? "primary" : ""}`} disabled={targetFailed}
                  title={targetFailed ? `${target} is failed too` : undefined}
                  onClick={() => act(onSpare ? { select_primaries: [train] } : { start_spares: [train] })}>
                  Switch to {target}</button>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <h4>Control loop</h4>
        <Field label="Loop">
          <Select value={loop} onChange={setLoop}
            options={Object.keys(meta.loops).map((k) => [k, `${k} · ${LOOP_NAME[k] ?? ""}`] as [string, string])} />
        </Field>
        <div className="loop-readout">
          <span>Measured <b className="num">{c.pv?.toPrecision(5) ?? "—"}</b> {L.sp_unit}</span>
          <span>Setpoint <b className="num">{c.setpoint.toPrecision(5)}</b> {L.sp_unit}</span>
          <span>Output <b className="num">{c.last_output.toPrecision(4)}</b> {L.mv_unit}</span>
        </div>
        <div className="seg small" role="radiogroup" aria-label="Loop mode">
          {(["AUTO", "MANUAL"] as const).map((m) => (
            <button key={m} role="radio" aria-checked={c.mode === m} className={c.mode === m ? "on" : ""}
              title={m === "AUTO" ? "The controller moves the output to hold the setpoint" : "You set the output directly"}
              onClick={() => c.mode !== m && act({ modes: { [loop]: m } })}>{m === "AUTO" ? "Automatic" : "Manual"}</button>
          ))}
        </div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); act(manual ? { manual_outputs: { [loop]: value } } : { setpoints: { [loop]: value } }); }}>
          <Field label={manual ? `Output [${L.mv_unit}]` : `Setpoint [${L.sp_unit}] (${L.sp_min}–${L.sp_max})`}>
            <Num value={value} min={manual ? undefined : L.sp_min} max={manual ? undefined : L.sp_max} onChange={setValue} /></Field>
          <button className="btn primary" type="submit">Apply</button>
        </form>
      </section>

      <section>
        <h4>Feed rate</h4>
        <form className="row" onSubmit={(e) => { e.preventDefault(); act({ throughput_target: feed }); }}>
          <Field label={`Fresh IPA feed [kmol/h] (${flo}–${fhi}, now ${snap.throughput_target.toFixed(1)})`}>
            <Num value={feed} min={flo} max={fhi} onChange={setFeed} /></Field>
          <button className="btn primary" type="submit">Apply</button>
        </form>
      </section>

      <section>
        <h4>Emergency devices</h4>
        {Object.keys(meta.auxiliaries).map((k) => {
          const [name, what] = AUX_NAME[k] ?? [k, ""];
          return (
            <form key={k} className="row" onSubmit={(e) => { e.preventDefault(); act({ auxiliary_commands: { [k]: (aux[k] ?? 0) / 100 } }); }}>
              <Field label={`${name} [% open] (now ${Math.round(100 * (snap.auxiliaries[k] ?? 0))} %)`}>
                <Num value={aux[k] ?? 0} min={0} max={100} step={5} onChange={(v) => setAux({ ...aux, [k]: v })} /></Field>
              <button className="btn" type="submit" title={what}>Apply</button>
            </form>
          );
        })}
      </section>
      <p className="hint">You have the same levers as the action agent. Safety functions cannot be bypassed.</p>
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
// Default sensor bias in engineering units: [bias, unit]. Large enough to matter, small enough to be plausible.
const SENSOR_BIAS: Record<string, [number, string]> = {
  cstr_pressure_bar: [-1.0, "bar"], cstr_temperature_K: [-5, "K"], cstr_holdup_kmol: [-8, "kmol"],
  column_pressure_bar: [-0.1, "bar"], top_temperature_K: [-5, "K"], bottom_temperature_K: [-5, "K"],
  reboiler_holdup_kmol: [-6, "kmol"], drum_holdup_kmol: [-6, "kmol"],
};
const KINDS: [Kind, string][] = [["pump", "Pump trip"], ["actuator", "Valve / actuator"], ["sensor", "Sensor"],
                                 ["process", "Process / utility"], ["disturbance", "Disturbance"]];

export function FaultsPanel() {
  const meta = usePlant((s) => s.meta);
  const snap = usePlant((s) => s.snap);
  const [kind, setKind] = useState<Kind>("pump");
  const [duration, setDuration] = useState(0);
  const [pump, setPump] = useState("P-201A");
  const [actu, setActu] = useState({ target: "cstr_vent_valve", mode: "freeze", severity_pct: 50 });
  const [sens, setSens] = useState({ target: "cstr_pressure_bar", mode: "bias", bias: SENSOR_BIAS.cstr_pressure_bar[0] });
  const [proc, setProc] = useState({ fault: "catalyst_activity_loss", severity: 50 });
  const [dist, setDist] = useState({ target: "cooling_water_temperature_K", direction: "1" });
  if (!meta || !snap) return null;
  const inj = meta.injections;
  const t = snap.truth;
  if (t.hidden) return (
    <div className="panel-body">
      <p className="hint warn">A blind human test is running: what was injected stays hidden, and manual injections are off,
        until the test ends.</p>
    </div>
  );
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
      <p className="hint">Break the plant on purpose to test the operator or the agents. Neither is told what you injected.</p>
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
            <Field label="Sensor"><Select value={sens.target} options={inj.sensor_targets}
              onChange={(v) => setSens({ ...sens, target: v, bias: SENSOR_BIAS[v]?.[0] ?? 0 })} /></Field>
            <Field label="Failure"><Select value={sens.mode} options={["bias", "freeze"]} onChange={(v) => setSens({ ...sens, mode: v })} /></Field>
          </div>
          {sens.mode === "bias" && <Field label={`Bias [${SENSOR_BIAS[sens.target]?.[1] ?? ""}] (added to the reading)`}>
            <Num value={sens.bias} onChange={(v) => setSens({ ...sens, bias: v })} /></Field>}
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
