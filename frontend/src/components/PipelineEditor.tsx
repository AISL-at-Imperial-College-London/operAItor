import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { checkCode, checkKg, loadPipelineDefaults, runPipeline, runReference, startBench, stopBench, store, usePlant } from "../store";
import type { BenchStatus, CustomPipeline, KgReport, PipelineDefaults, SettingSpec } from "../types";

// The visitor's own pipelines live in this browser (like the LLM key, nothing is stored on the server).
const STORE_KEY = "operaitor.pipelines";
function loadMine(): CustomPipeline[] {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) ?? "[]"); } catch { return []; }
}
function saveMine(list: CustomPipeline[]): boolean {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(list)); return true; } catch { return false; }
}
const newId = () => Math.random().toString(36).slice(2, 10);
const toast = (ok: boolean, text: string) => store.set({ toast: { ok, text, at: Date.now() } });

function show(spec: SettingSpec, v: unknown): string {
  if (spec.choices) return spec.choices[String(v)] ?? String(v);
  if (spec.type === "bool") return v ? "yes" : "no";
  return String(v);
}

function changes(p: CustomPipeline): string[] {
  const out = [];
  const n = Object.keys(p.settings).length;
  if (n) out.push(`${n} setting${n > 1 ? "s" : ""}`);
  const prompts = Object.keys(p.prompts);
  if (prompts.length) out.push(`prompts: ${prompts.join(", ")}`);
  if (p.kg) out.push(`own knowledge graph${p.kgName ? ` (${p.kgName})` : ""}`);
  if (p.code) out.push("Python agents");
  return out.length ? out : ["no changes yet"];
}

function download(p: CustomPipeline) {
  const { id: _id, ...rest } = p;
  const blob = new Blob([JSON.stringify({ operaitor_pipeline: 1, ...rest }, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${p.name.replace(/[^\w.-]+/g, "_") || "pipeline"}.operaitor-pipeline.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function KgReportView({ report }: { report: KgReport }) {
  const f = report.families;
  return (
    <div className="kg-report">
      <div className="small">{report.nodes} nodes · {report.relations} relations · {f["fault modes"] ?? 0} fault modes ·
        {" "}{f["operator actions"] ?? 0} operator actions · {report.mapped_to_plant} nodes linked to plant variables ·
        {" "}{report.faults_covered} of {report.reference_faults} reference faults covered</div>
      {report.warnings.length ? <ul className="warn-text small">{report.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        : <div className="ok-text small">No problems found.</div>}
    </div>
  );
}

function SettingInput({ spec, value, onChange }: { spec: SettingSpec; value: unknown; onChange: (v: unknown) => void }) {
  if (spec.choices) return (
    <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
      {Object.entries(spec.choices).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
    </select>);
  if (spec.type === "bool") return <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  return <input type="number" min={spec.min} max={spec.max} step={spec.type === "int" ? 1 : 0.5} value={Number(value)}
    onChange={(e) => onChange(spec.type === "int" ? parseInt(e.target.value || "0", 10) : parseFloat(e.target.value || "0"))} />;
}

/** The decision steps of a pipeline, as a chain: click a step to switch it or change its variant. */
function StepsView({ value, set, defaults, code }: {
  value: (k: string) => unknown; set: (k: string, v: unknown) => void; defaults: PipelineDefaults; code: string | null | undefined;
}) {
  const detectors = !!code && /^\s*DETECTORS\s*=/m.test(code);
  const wiring = !!code && /^\s*def\s+BUILD_GRAPH\b/m.test(code);
  const validators = defaults.settings.validator.choices ?? {};
  const retries = Number(value("reprompt_max"));
  const Step = ({ title, sub, off, onClick, hint }: { title: string; sub: string; off?: boolean; onClick?: () => void; hint?: string }) =>
    onClick ? <button type="button" role="listitem" className={`pl-step${off ? " off" : ""}`} title={hint} onClick={onClick}><b>{title}</b><small>{sub}</small></button>
      : <div role="listitem" className="pl-step fixed"><b>{title}</b><small>{sub}</small></div>;
  return (
    <>
      <div className="pl-steps" role="list" aria-label="Decision steps">
        <Step title="Monitoring" sub={`PCA + rule checks${detectors ? " + your detectors" : ""}`} />
        <span className="arrow" aria-hidden>→</span>
        <Step title="Diagnosis" sub={value("diagnoser") === "llm" ? "LLM diagnosis agent" : "rules only"} off={value("diagnoser") !== "llm"}
          hint="Click: LLM diagnosis agent or rules only" onClick={() => set("diagnoser", value("diagnoser") === "llm" ? "heuristic" : "llm")} />
        <span className="arrow" aria-hidden>→</span>
        <Step title="Knowledge graph" sub={value("use_kg") ? "evidence lookup" : "skipped"} off={!value("use_kg")}
          hint="Click: use the knowledge graph or skip it" onClick={() => set("use_kg", !value("use_kg"))} />
        <span className="arrow" aria-hidden>→</span>
        <Step title="Planning" sub={value("planner") === "llm" ? "LLM planning agent" : "knowledge-graph rules"}
          hint="Click: LLM planning agent or knowledge-graph rules" onClick={() => set("planner", value("planner") === "llm" ? "kg" : "llm")} />
        <span className="arrow" aria-hidden>→</span>
        <Step title="Action agent" sub="proposes commands" />
        <span className="arrow" aria-hidden>→</span>
        <label role="listitem" className={`pl-step select${value("validator") === "none" ? " off" : ""}`}>
          <b>Check</b>
          <select aria-label="Check each action with" value={String(value("validator"))} onChange={(e) => set("validator", e.target.value)}>
            {Object.entries(validators).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <span className="arrow" aria-hidden>→</span>
        <Step title="Dispatch" sub="applied to the plant" />
      </div>
      <div className="pl-steps-loop small">
        <span>↺ rejected → back to the action agent,</span>
        <button type="button" className="btn xs" aria-label="Fewer retries" disabled={retries <= 0} onClick={() => set("reprompt_max", retries - 1)}>−</button>
        <b>{retries}</b>
        <button type="button" className="btn xs" aria-label="More retries" disabled={retries >= 5} onClick={() => set("reprompt_max", retries + 1)}>+</button>
        <span>{retries === 1 ? "retry" : "retries"} · then</span>
        <button type="button" className="btn xs" onClick={() => set("fallback_to_safe", !value("fallback_to_safe"))}>
          {value("fallback_to_safe") ? "apply the best safe candidate" : "give up"}</button>
      </div>
      {wiring && <p className="small warn-text">Your Python code wires the steps itself (BUILD_GRAPH): it decides their order and adds
        its own; the switches above still choose each standard step's variant.</p>}
    </>
  );
}

function Editor({ draft, defaults, onSave, onCancel }: {
  draft: CustomPipeline; defaults: PipelineDefaults; onSave: (p: CustomPipeline, run: boolean) => void; onCancel: () => void;
}) {
  const [p, setP] = useState<CustomPipeline>(draft);
  const [agent, setAgent] = useState("action");
  const kgCheck = usePlant((s) => s.kgCheck);
  const codeCheck = usePlant((s) => s.codeCheck);
  const plugins = usePlant((s) => s.session?.plugins);
  const fileRef = useRef<HTMLInputElement>(null);
  const ref = defaults.references[p.base];
  const base = ref?.settings ?? {};
  const setSetting = (k: string, v: unknown) => {
    const settings = { ...p.settings };
    if (JSON.stringify(v) === JSON.stringify(base[k])) delete settings[k]; else settings[k] = v;
    setP({ ...p, settings });
  };
  const prompt = p.prompts[agent];
  const setPrompt = (mode: "append" | "replace", text: string) => {
    const prompts = { ...p.prompts };
    if (text.trim()) prompts[agent] = { mode, text }; else delete prompts[agent];
    setP({ ...p, prompts });
  };
  const mode = prompt?.mode ?? "append";
  const text = prompt?.text ?? "";
  const tooLong = Object.values(p.prompts).some((x) => x.text.length > defaults.limits.prompt_chars)
    || (p.code?.length ?? 0) > defaults.limits.code_chars;
  const onKgFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > defaults.limits.kg_bytes) { toast(false, `${f.name} is larger than ${defaults.limits.kg_bytes / 1e6} MB`); return; }
    const t = await f.text();
    setP({ ...p, kg: t, kgName: f.name });
    checkKg(t);
  };
  return (
    <div className="pl-editor">
      <div className="row">
        <label className="field"><span>Name</span>
          <input type="text" value={p.name} maxLength={60} onChange={(e) => setP({ ...p, name: e.target.value })} /></label>
        <div className="field"><span>Starts from (baseline)</span><b>{ref?.label ?? p.base}</b></div>
      </div>

      <h4>Steps <span className="dim small">· click a step to switch it</span></h4>
      <StepsView value={(k) => (k in p.settings ? p.settings[k] : base[k])} set={setSetting} defaults={defaults} code={p.code} />

      <h4>Settings <span className="dim small">· changed values are highlighted; the rest follow the baseline</span></h4>
      <div className="pl-settings">
        {Object.entries(defaults.settings).map(([k, spec]) => {
          const changed = k in p.settings;
          const value = changed ? p.settings[k] : base[k];
          return (
            <label key={k} className={`pl-setting${changed ? " changed" : ""}`}>
              <span>{spec.label}</span>
              <SettingInput spec={spec} value={value} onChange={(v) => setSetting(k, v)} />
              <small className="dim">{changed ? <>baseline: {show(spec, base[k])} · <a href="#" onClick={(e) => { e.preventDefault(); setSetting(k, base[k]); }}>reset</a></> : "baseline"}</small>
              {k === "validator" && value === "custom" && !p.code && <small className="warn-text">needs a VALIDATOR in your Python agents (below)</small>}
            </label>
          );
        })}
      </div>

      <h4>Prompts</h4>
      <div className="seg small wrap" role="tablist">
        {Object.entries(defaults.prompt_agents).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={agent === k} className={agent === k ? "on" : ""} onClick={() => setAgent(k)}>
            {label}{p.prompts[k] ? " ●" : ""}</button>))}
      </div>
      <div className="row pl-prompt-mode">
        <div className="seg small" role="radiogroup" aria-label="How your text is used">
          <button role="radio" aria-checked={mode === "append"} className={mode === "append" ? "on" : ""} onClick={() => setPrompt("append", text)}>Add to the baseline prompt</button>
          <button role="radio" aria-checked={mode === "replace"} className={mode === "replace" ? "on" : ""} onClick={() => setPrompt("replace", text || defaults.prompts[agent])}>Replace it</button>
        </div>
        {prompt && <button className="btn ghost sm" onClick={() => setPrompt(mode, "")}>Back to the baseline prompt</button>}
      </div>
      <textarea className="pl-prompt" rows={mode === "replace" ? 14 : 6} spellCheck={false} value={text}
        placeholder={mode === "append" ? "Extra instructions, added after the baseline prompt. Leave empty to use the baseline as it is." : ""}
        onChange={(e) => setPrompt(mode, e.target.value)} />
      <div className="dim small">{text.length.toLocaleString()} / {defaults.limits.prompt_chars.toLocaleString()} characters
        {agent === "action" && " · with ADMIT validation, the action-record instructions are always added"}
        {agent === "planning" && " · used only with the LLM planner (setting above)"}
        {agent === "judge" && " · used only when actions are checked by the LLM judge"}</div>
      <details className="sub"><summary className="small">Baseline {defaults.prompt_agents[agent]?.toLowerCase()} prompt</summary>
        <pre className="pl-ref-prompt">{defaults.prompts[agent]}</pre></details>

      <h4>Knowledge graph</h4>
      <p className="small dim">JSON-LD in the same format as the baseline graph (the node ids link it to the plant, e.g. controller_R_PIC,
        pump_train_REFLUX, fault_reflux_pump_trip).</p>
      <div className="row">
        <span>{p.kg ? <b>{p.kgName ?? "your graph"}</b> : "Baseline knowledge graph"}</span>
        <input ref={fileRef} type="file" accept=".jsonld,.json,application/ld+json,application/json" hidden
          onChange={(e) => { void onKgFile(e.target.files?.[0]); e.target.value = ""; }} />
        <button className="btn sm" onClick={() => fileRef.current?.click()}>{p.kg ? "Replace…" : "Upload your own…"}</button>
        {p.kg && <button className="btn ghost sm" onClick={() => setP({ ...p, kg: null, kgName: null })}>Use the baseline graph</button>}
        {p.kg && <button className="btn ghost sm" onClick={() => checkKg(p.kg!)}>Check</button>}
      </div>
      {p.kg && kgCheck && (kgCheck.report ? <KgReportView report={kgCheck.report} /> : <p className="bad-text small">{kgCheck.summary}</p>)}

      <h4>Python agents <span className="dim small">· your own fault detectors and validator</span></h4>
      {!plugins?.allowed && <p className="small warn-text">Not on this server: {plugins?.reason || "Python agents need OperAItor on your own computer"}.
        You can still write and save them here, export the pipeline and run it in OperAItor on your computer.</p>}
      <p className="small dim">A Python module that defines <code>DETECTORS</code> (run next to PCA on every observation; an alarm triggers
        the agents) and/or <code>VALIDATOR</code> (checks each proposed action when <i>Check each action with</i> is
        <i> Your Python validator</i>; it can simulate the action on a copy of the plant). See docs/pipelines.md.</p>
      <div className="row">
        {p.code == null
          ? <button className="btn sm" onClick={() => setP({ ...p, code: defaults.plugin_template })}>Start from the example</button>
          : <>
            <button className="btn sm" disabled={!plugins?.allowed} onClick={() => checkCode(p.code ?? "")}>Check the code</button>
            {!/^\s*def\s+BUILD_GRAPH\b/m.test(p.code) && <button className="btn sm"
              onClick={() => setP({ ...p, code: (p.code ?? "").trimEnd() + "\n" + defaults.graph_template })}>Add the wiring example</button>}
            <button className="btn ghost sm" onClick={() => setP({ ...p, code: null })}>Remove the Python agents</button>
          </>}
      </div>
      {p.code != null && <textarea className="pl-prompt pl-code" rows={18} spellCheck={false} value={p.code} aria-label="Python agents"
        onChange={(e) => setP({ ...p, code: e.target.value })} />}
      {p.code != null && codeCheck && <p className={`small ${codeCheck.ok ? "ok-text" : "bad-text"}`}>{codeCheck.summary}</p>}

      <div className="row pl-actions">
        <button className="btn primary sm" disabled={!p.name.trim() || tooLong} onClick={() => onSave(p, true)}>Save and run</button>
        <button className="btn sm" disabled={!p.name.trim() || tooLong} onClick={() => onSave(p, false)}>Save</button>
        <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

const money = (v: number) => `${v < 0 ? "−" : ""}$${Math.abs(Math.round(v)).toLocaleString()}`;

/** Benchmark the running pipeline on the benchmark scenarios and compare with the published runs. */
function BenchPanel() {
  const bench = usePlant((s) => s.bench);
  const session = usePlant((s) => s.session);
  const human = usePlant((s) => s.snap?.operator_mode === "human");
  const [choice, setChoice] = useState("quick");
  const running = bench?.state === "running";
  const active = session?.pipeline;
  return (
    <div className="pl-bench" id="pipeline-bench">
      <h4>Benchmark the running pipeline</h4>
      <p className="small dim">Replays benchmark scenarios on this plant with the benchmark's faults, timing and score, the
        agents acting on their own, and compares each scenario with the reference runs of the original study: no agents, and
        {" "}{active?.reference ? "this baseline" : "the baseline it starts from"} with every model run there (one run each, on
        the plant before version 1.1).</p>
      <div className="row">
        <select aria-label="Scenarios" value={choice} disabled={running} onChange={(e) => setChoice(e.target.value)}>
          <option value="quick">Quick: 3 scenarios (single, coupled pair, coupled triple)</option>
          <option value="all">All 20 scenarios</option>
          {["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10", "D01", "D02", "D03", "D04", "C01", "C02", "C03", "T01", "T02", "T03"]
            .map((id) => <option key={id} value={id}>{id} only</option>)}
        </select>
        {running ? <button className="btn sm" onClick={stopBench}>Stop</button>
          : <button className="btn primary sm" disabled={human} title={human ? "Switch the operator to the agents first" : undefined}
            onClick={() => startBench(choice)}>Start the benchmark</button>}
        <span className="dim small">running: <b>{active?.name ?? "—"}</b>{active && !active.reference ? ` · ${active.fingerprint}` : ""}</span>
      </div>
      {!session?.llm.active && <p className="small warn-text">No LLM connected: the agents only monitor, so the result is the
        plant without agents. Connect an LLM in Agent settings to benchmark the agents.</p>}
      {bench && <BenchResults bench={bench} />}
    </div>
  );
}

function BenchResults({ bench }: { bench: BenchStatus }) {
  const models = Object.keys(bench.summary.reference).filter((k) => k !== "No agents");
  const cell = (v: { profit: number; stopped: boolean } | null | undefined) =>
    v ? <td className="num">{money(v.profit)}{v.stopped && <span className="bad-text"> · stop</span>}</td> : <td className="num dim">—</td>;
  const cur = bench.current;
  return (
    <>
      <p className="small">
        <b>{bench.state === "running" ? "Running" : bench.state}</b> · {bench.done} of {bench.total} scenarios
        {bench.model ? ` · ${bench.model}` : " · no LLM"} · pipeline {bench.pipeline.name}
        {cur && <> · now <b>{cur.id}</b> at {cur.time_min.toFixed(0)} / {cur.window_min.toFixed(0)} min, {money(cur.profit)}
          {cur.stop_min != null && <span className="bad-text"> · plant stopped at {cur.stop_min.toFixed(1)} min</span>}</>}
      </p>
      {bench.state === "running" && <div className="bench-bar" role="progressbar" aria-valuemin={0} aria-valuemax={bench.total}
        aria-valuenow={bench.done}><i style={{ width: `${(100 * (bench.done + (cur ? cur.time_min / cur.window_min : 0))) / bench.total}%` }} /></div>}
      {bench.results.length > 0 && (
        <div className="tw"><table className="bench-table">
          <thead><tr><th>Scenario</th><th>Faults</th><th className="num">This pipeline</th><th className="num">No agents</th>
            {models.map((m) => <th key={m} className="num" title="Reference run of the original study">{m} · reference</th>)}</tr></thead>
          <tbody>
            {bench.results.map((r) => (
              <tr key={r.id} className={r.complete ? "" : "dim"}>
                <td>{r.id}</td><td className="small">{r.faults.join(" + ")}</td>
                <td className="num"><b>{money(r.profit)}</b>{r.stopped && <span className="bad-text"> · stop</span>}{!r.complete && " (stopped early)"}</td>
                {cell(r.no_agents)}
                {models.map((m) => <Fragment key={m}>{cell(r.baseline[m])}</Fragment>)}
              </tr>
            ))}
          </tbody>
          {bench.summary.scenarios > 0 && (
            <tfoot><tr><td colSpan={2}><b>{bench.summary.scenarios} scored</b> (plant stops · total)</td>
              <td className="num"><b>{bench.summary.stops} · {money(bench.summary.profit)}</b></td>
              {["No agents", ...models].map((m) => { const s = bench.summary.reference[m];
                return <td key={m} className="num">{s ? `${s.stops} · ${money(s.profit)}` : "—"}</td>; })}
            </tr></tfoot>
          )}
        </table></div>
      )}
      {bench.report_path && <p className="dim small">Report: <code>{bench.report_path}</code></p>}
    </>
  );
}

/** Baselines and the visitor's own pipelines: run one, or customise settings, prompts and the knowledge graph. */
export function PipelineManager() {
  const session = usePlant((s) => s.session);
  const defaults = usePlant((s) => s.pipelineDefaults);
  const busy = usePlant((s) => s.pipelineBusy);
  const [mine, setMine] = useState<CustomPipeline[]>(loadMine);
  const [editing, setEditing] = useState<CustomPipeline | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (session) loadPipelineDefaults(); }, [session?.id]);
  const active = session?.pipeline;
  const llmOn = !!session?.llm.active;
  const persist = (list: CustomPipeline[]) => {
    setMine(list);
    if (!saveMine(list)) toast(false, "Saved for this visit only: the browser's storage is full (a large knowledge graph?). Export it to keep it.");
  };
  const labels = useMemo(() => defaults?.references ?? {}, [defaults]);

  if (!defaults) return <p className="dim">Loading the baseline pipelines…</p>;
  if (editing) return (
    <Editor key={editing.id} draft={editing} defaults={defaults} onCancel={() => setEditing(null)}
      onSave={(p, run) => {
        const list = mine.some((x) => x.id === p.id) ? mine.map((x) => (x.id === p.id ? p : x)) : [...mine, p];
        persist(list);
        setEditing(null);
        if (run) runPipeline(p);
      }} />
  );

  const onImport = async (f: File | undefined) => {
    if (!f) return;
    try {
      const raw = JSON.parse(await f.text());
      if (!raw || typeof raw !== "object" || !(raw.base in labels)) throw new Error("not an OperAItor pipeline file");
      const p: CustomPipeline = { id: newId(), name: String(raw.name || f.name).slice(0, 60), base: raw.base,
        settings: raw.settings ?? {}, prompts: raw.prompts ?? {}, kg: raw.kg ?? null, kgName: raw.kgName ?? null };
      persist([...mine, p]);
      toast(true, `Imported "${p.name}"`);
    } catch (e) { toast(false, `Could not import ${f.name}: ${(e as Error).message}`); }
  };
  const isActive = (p: CustomPipeline) => !!active && !active.reference && active.client_id === p.id;
  const codeBlocked = (p: CustomPipeline) => !!p.code && !session?.plugins?.allowed;

  return (
    <div className="pl-manager">
      <p className="small dim">The baselines are the published reference pipelines and never change. Customise one to try your own
        settings, prompts or knowledge graph; your pipelines are kept in this browser, and you can export them to share.
        {!llmOn && " Connect an LLM (Agent settings) for the agents to act."}</p>
      <h4>Baselines</h4>
      <ul className="pl-list">
        {Object.entries(labels).map(([k, r]) => {
          const on = !!active?.reference && active.base === k;
          return (
            <li key={k} className={on ? "on" : ""}>
              <span className="pl-name"><span>{r.label} <span className="pill">baseline</span>{on && <span className="pill pill-ok">running</span>}</span></span>
              <span className="pl-buttons">
                <button className="btn sm" disabled={on || busy} onClick={() => runReference(k)}>Run</button>
                <button className="btn sm" onClick={() => setEditing({ id: newId(), name: `${r.label} (my version)`, base: k, settings: {}, prompts: {} })}>Customise</button>
              </span>
            </li>
          );
        })}
      </ul>
      <h4>Your pipelines</h4>
      {mine.length ? (
        <ul className="pl-list">
          {mine.map((p) => (
            <li key={p.id} className={isActive(p) ? "on" : ""}>
              <span className="pl-name"><span>{p.name}{isActive(p) && <span className="pill pill-ok" title="fingerprint of what runs">running · {active?.fingerprint}</span>}</span>
                <small className="dim">from {labels[p.base]?.label ?? p.base} · {changes(p).join(" · ")}</small></span>
              <span className="pl-buttons">
                <button className="btn sm primary" disabled={busy || !!isActive(p) || codeBlocked(p)}
                  title={codeBlocked(p) ? "Has Python agents: run it in OperAItor on your own computer" : undefined}
                  onClick={() => runPipeline(p)}>Run</button>
                <button className="btn sm" onClick={() => setEditing(p)}>Edit</button>
                <button className="btn sm" onClick={() => setEditing({ ...p, id: newId(), name: `${p.name} (copy)` })}>Duplicate</button>
                <button className="btn sm" onClick={() => download(p)}>Export</button>
                <button className="btn ghost sm" onClick={() => { if (confirm(`Delete "${p.name}"?`)) persist(mine.filter((x) => x.id !== p.id)); }}>Delete</button>
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="dim small">None yet: customise a baseline, or import a pipeline file.</p>}
      <input ref={importRef} type="file" accept=".json,application/json" hidden
        onChange={(e) => { void onImport(e.target.files?.[0]); e.target.value = ""; }} />
      <button className="btn sm" onClick={() => importRef.current?.click()}>Import a pipeline file…</button>
      <BenchPanel />
      {active && !active.reference && active.kg_report && (
        <details className="sub"><summary className="small">Knowledge graph of the running pipeline</summary><KgReportView report={active.kg_report} /></details>)}
    </div>
  );
}
