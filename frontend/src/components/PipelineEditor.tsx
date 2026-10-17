import { useEffect, useMemo, useRef, useState } from "react";
import { checkKg, loadPipelineDefaults, runPipeline, runReference, store, usePlant } from "../store";
import type { CustomPipeline, KgReport, PipelineDefaults, SettingSpec } from "../types";

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

function Editor({ draft, defaults, onSave, onCancel }: {
  draft: CustomPipeline; defaults: PipelineDefaults; onSave: (p: CustomPipeline, run: boolean) => void; onCancel: () => void;
}) {
  const [p, setP] = useState<CustomPipeline>(draft);
  const [agent, setAgent] = useState("action");
  const kgCheck = usePlant((s) => s.kgCheck);
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
  const tooLong = Object.values(p.prompts).some((x) => x.text.length > defaults.limits.prompt_chars);
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

      <div className="row pl-actions">
        <button className="btn primary sm" disabled={!p.name.trim() || tooLong} onClick={() => onSave(p, true)}>Save and run</button>
        <button className="btn sm" disabled={!p.name.trim() || tooLong} onClick={() => onSave(p, false)}>Save</button>
        <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
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
                <button className="btn sm primary" disabled={busy || !!isActive(p)} onClick={() => runPipeline(p)}>Run</button>
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
      {active && !active.reference && active.kg_report && (
        <details className="sub"><summary className="small">Knowledge graph of the running pipeline</summary><KgReportView report={active.kg_report} /></details>)}
    </div>
  );
}
