import { useState } from "react";
import { forgetKey, send, usePlant, useKey } from "../store";

function PresetSelect({ value, presets }: { value: string; presets: Record<string, string> }) {
  return (
    <label className="key-row small">Validation:
      <select aria-label="Agent configuration" value={value}
        onChange={(e) => send({ type: "set_agent_preset", preset: e.target.value })}>
        {Object.entries(presets).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
    </label>
  );
}

// Where the visitor turns the LLM agents on for their own session.
export function LlmKeyCard() {
  const session = usePlant((s) => s.session);
  const busy = usePlant((s) => s.keyBusy);
  const usage = usePlant((s) => s.llmUsage);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  if (!session) return null;
  const llm = session.llm;
  const models = session.models.length ? session.models : [llm.model];
  const chosen = model || llm.model;

  if (llm.key_source === "visitor" || (llm.active && !session.hosted)) {
    return (
      <div className="key-card on">
        <div className="key-head"><span className="pill pill-ok">LLM agents on</span> <b>{llm.model}</b>
          <span className="dim"> · {llm.key_source === "visitor" ? "your key" : "server key"}</span></div>
        {usage && <div className="dim small">{usage.calls} calls · {(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens · ≈ ${usage.cost_usd.toFixed(4)} this session</div>}
        {llm.presets && <PresetSelect value={llm.preset} presets={llm.presets} />}
        {llm.key_source === "visitor" && <button className="btn sm" onClick={forgetKey}>Remove my key</button>}
      </div>
    );
  }
  return (
    <form className="key-card" onSubmit={(e) => { e.preventDefault(); if (key.trim()) { useKey(key.trim(), chosen); setKey(""); } }}>
      <div className="key-head"><b>Turn on the LLM agents</b></div>
      <p className="small dim">
        Without an LLM the agents only monitor and diagnose. Paste an OpenAI API key to let them act
        (diagnosis, actions, validation, chat). A run costs a few cents with gpt-5-nano.
      </p>
      <div className="key-row">
        <input type="password" autoComplete="off" spellCheck={false} placeholder="sk-…" aria-label="OpenAI API key"
          value={key} onChange={(e) => setKey(e.target.value)} />
        <select aria-label="Model" value={chosen} onChange={(e) => setModel(e.target.value)}>
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <button className="btn primary sm" disabled={busy || !key.trim()}>{busy ? "Checking…" : "Use key"}</button>
      </div>
      <p className="small dim">
        The key goes only to this server, is held in memory for your session and is never stored or
        shown again. Calls are billed to your OpenAI account; closing the tab ends the session.
      </p>
      {llm.reason && !/API key/.test(llm.reason) && <p className="small dim">Status: {llm.reason}</p>}
    </form>
  );
}

export function SessionNotice() {
  const notice = usePlant((s) => s.notice);
  if (!notice) return null;
  return <div className="offline">{notice.text}</div>;
}
