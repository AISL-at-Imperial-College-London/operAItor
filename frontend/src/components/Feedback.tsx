import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { API_VERSION, store, usePlant, type PlantState } from "../store";

// Reports go to the project's GitHub issues, pre-filled from here: the same for every way OperAItor runs (desktop
// app, Docker, pip, the website). Nothing passes through the OperAItor server and no token is needed; the reporter
// reviews the issue on GitHub and submits it with their own account (or copies / downloads the report instead).
export const REPO = "https://github.com/AISL-at-Imperial-College-London/operAItor";
const MAX_URL = 7500;  // browsers and GitHub accept about 8 kB of URL

type Kind = "bug" | "physics" | "feedback";
const KINDS: Record<Kind, { label: string; template: string; prefix: string; hint: string }> = {
  bug: { label: "Something doesn't work", template: "bug.yml", prefix: "[bug] ",
    hint: "The app, the agents, pipelines, the benchmark, installing…" },
  physics: { label: "The plant behaves oddly", template: "physics.yml", prefix: "[physics] ",
    hint: "A balance, a response, a trip or a limit a real process would not show" },
  feedback: { label: "Feedback or an idea", template: "feedback.yml", prefix: "[feedback] ",
    hint: "What worked, what you missed, what you would change" },
};

/** What a maintainer needs to reproduce a report: version, install, pipeline, model (never a key), plant state. */
export function diagnostics(s: PlantState): string {
  const srv = s.session?.server;
  const snap = s.snap;
  const p = s.session?.pipeline;
  const conn = s.session?.llm.connection;
  const lines = [
    `OperAItor ${srv?.version ?? "?"} · ${srv?.install ?? "?"} · ${srv?.system ?? "?"} (${srv?.machine ?? "?"}) · Python ${srv?.python ?? "?"} · console API ${API_VERSION}`,
    `Browser: ${navigator.userAgent}`,
    `Pipeline: ${p ? `${p.name} (${p.reference ? "baseline" : `custom ${p.fingerprint}`}${p.python ? ", Python agents" : ""}${p.custom_kg ? ", own knowledge graph" : ""})` : "?"}`
      + ` · validator ${s.agent?.validator ?? "?"} · agents ${s.agent?.mode ?? "?"}`,
    `Model: ${conn ? `${conn.model} via ${conn.label}${conn.in_browser ? " (browser)" : ""}` : `none${s.session?.llm.reason ? ` (${s.session.llm.reason})` : ""}`}`,
  ];
  if (snap) {
    lines.push(`Plant: ${snap.config.scenario}, episode ${snap.episode}, t = ${snap.time_min.toFixed(1)} min, `
      + `${snap.done ? `ended (${snap.episode_status})` : snap.running ? "running" : "paused"}, operator ${snap.operator_mode ?? "agents"}`);
    const active = snap.alarms.filter((a) => a.state.startsWith("ACTIVE")).map((a) => a.id);
    lines.push(`Active alarms: ${active.join(", ") || "none"} · safety functions latched: ${snap.sif_latched.join(", ") || "none"}`);
  }
  const events = s.agentLog.filter((e) => e.stage !== "node").slice(-12);
  if (events.length) {
    lines.push("Last agent events:");
    for (const e of events) lines.push(`  ${e.time_min?.toFixed(1)} ${e.stage}${e.reason ? `: ${String(e.reason).slice(0, 120)}` : ""}`);
  }
  const msgs = s.messages.slice(-6);
  if (msgs.length) {
    lines.push("Last messages:");
    for (const m of msgs) lines.push(`  ${m.time_min?.toFixed(1)} [${m.level}] ${m.text.slice(0, 160)}`);
  }
  return lines.join("\n");
}

/** The GitHub "new issue" address for a report, its fields filled in (kept short enough for a URL). */
export function issueUrl(kind: Kind, title: string, what: string, diag: string | null): string {
  const k = KINDS[kind];
  const version = store.state.session?.server?.version ?? "";
  const build = (d: string | null) => {
    const q = new URLSearchParams({ template: k.template, title: k.prefix + title.trim() });
    q.set("what", what.trim());
    if (kind !== "feedback") q.set("version", `${version} (${store.state.session?.server?.install ?? "?"})`);
    if (d) q.set("diagnostics", d);
    return `${REPO}/issues/new?${q.toString()}`;
  };
  let url = build(diag);
  if (url.length > MAX_URL && diag) {  // shorten the diagnostics, never the reporter's own words
    const room = Math.max(0, diag.length - (url.length - MAX_URL) - 40);
    url = build(diag.slice(0, room) + "\n… (shortened: use Download report for all of it)");
  }
  return url;
}

export function FeedbackDialog({ onClose }: { onClose: () => void }) {
  const state = usePlant((s) => s);
  const [kind, setKind] = useState<Kind>("bug");
  const [title, setTitle] = useState("");
  const [what, setWhat] = useState("");
  const [attach, setAttach] = useState(true);
  const [copied, setCopied] = useState(false);
  const diag = useMemo(() => diagnostics(state), [state]);
  const ready = title.trim().length > 2 && what.trim().length > 2;
  const report = () => `${KINDS[kind].prefix}${title.trim()}\n\n${what.trim()}${attach ? `\n\n--- Diagnostics ---\n${diag}` : ""}\n`;
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([report()], { type: "text/plain" }));
    a.download = `operaitor-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  // On document.body: inside the header (its backdrop filter makes it the containing block) the dialog is clipped.
  return createPortal(
    <div className="modal-wrap" role="dialog" aria-modal="true" aria-label="Report a bug or give feedback">
      <div className="modal-scrim" onClick={onClose} />
      <div className="modal card">
        <div className="modal-head"><h3>Report a bug or give feedback</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="seg wrap" role="radiogroup" aria-label="Kind of report">
          {(Object.keys(KINDS) as Kind[]).map((k) => (
            <button key={k} role="radio" aria-checked={kind === k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{KINDS[k].label}</button>
          ))}
        </div>
        <p className="dim small">{KINDS[kind].hint}</p>
        <label className="field"><span>Title</span>
          <input type="text" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} autoFocus /></label>
        <label className="field"><span>{kind === "feedback" ? "Your feedback" : "What happened, and what you expected"}</span>
          <textarea rows={5} value={what} onChange={(e) => setWhat(e.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
          Attach diagnostics (version, how it runs, pipeline, model, plant state; never your API key)</label>
        {attach && <details className="sub"><summary className="small">What will be attached</summary><pre className="diag">{diag}</pre></details>}
        <div className="row modal-actions">
          {ready
            ? <a className="btn primary" target="_blank" rel="noreferrer" href={issueUrl(kind, title, what, attach ? diag : null)}>Continue on GitHub ↗</a>
            : <button className="btn primary" disabled title="Add a title and a description first">Continue on GitHub ↗</button>}
          <button className="btn" disabled={!ready} onClick={() => {
            void navigator.clipboard?.writeText(report()).then(() => setCopied(true), () => setCopied(false));
          }}>{copied ? "Copied" : "Copy report"}</button>
          <button className="btn" disabled={!ready} onClick={download}>Download report</button>
        </div>
        <p className="dim small">GitHub opens with the report filled in: check it and press <b>Create</b> (a free GitHub account is
          needed). No account? Copy or download the report and share it with the OperAItor team.</p>
        <p className="small">Want to help? <a href={`${REPO}/issues`} target="_blank" rel="noreferrer">See the open issues</a> ·{" "}
          <a href={`${REPO}/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22`} target="_blank" rel="noreferrer">ones we'd love help with</a> ·{" "}
          <a href={`${REPO}/blob/main/CONTRIBUTING.md`} target="_blank" rel="noreferrer">how to contribute</a></p>
      </div>
    </div>,
    document.body,
  );
}
