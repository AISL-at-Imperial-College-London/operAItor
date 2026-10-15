import { useState } from "react";
import { connectLlm, forgetKey, usePlant } from "../store";

/** Where the visitor connects the agents to an LLM: free tiers and Ollama first, OpenAI optional. */
export function LlmKeyCard() {
  const session = usePlant((s) => s.session);
  const busy = usePlant((s) => s.keyBusy);
  const usage = usePlant((s) => s.llmUsage);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  if (!session) return null;
  const llm = session.llm;
  const providers = llm.providers ?? {};
  const conn = llm.connection;

  if (conn && llm.active) {
    return (
      <div className="key-card on">
        <div className="key-head"><span className="pill pill-ok">LLM agents on</span> <b>{conn.model}</b>
          <span className="dim"> · {conn.label}{conn.free ? " · free" : ""}</span></div>
        {usage && <div className="dim small">{usage.calls} calls · {(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens
          {conn.free ? "" : ` · ≈ $${usage.cost_usd.toFixed(4)}`} this session</div>}
        {llm.key_source === "visitor" && <button className="btn sm" onClick={forgetKey}>Disconnect</button>}
      </div>
    );
  }

  const pid = provider || Object.keys(providers)[0] || "";
  const p = providers[pid];
  const chosenModel = model || p?.models[0] || "";
  const ready = !!p && !!chosenModel && (!p.key || !!key.trim()) && (!p.url || !session.hosted || !!url.trim());
  return (
    <form className="key-card" onSubmit={(e) => {
      e.preventDefault();
      if (!ready) return;
      connectLlm({ provider: pid, model: chosenModel.trim(), key: key.trim(), url: url.trim() });
      setKey("");
    }}>
      <p className="small dim">The free options need only a free account with the provider, or your own Ollama.</p>
      <label className="field"><span>Provider</span>
        <select value={pid} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
          {Object.entries(providers).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select></label>
      {p && <>
        <label className="field"><span>Model</span>
          <input type="text" list={`models-${pid}`} value={chosenModel} onChange={(e) => setModel(e.target.value)} spellCheck={false} />
          <datalist id={`models-${pid}`}>{p.models.map((m) => <option key={m} value={m} />)}</datalist></label>
        {p.url && (
          <label className="field"><span>Ollama address{session.hosted ? " (https, reachable from the internet)" : " (blank = this machine)"}</span>
            <input type="text" placeholder={session.hosted ? "https://my-ollama.example.org" : "http://127.0.0.1:11434"}
              value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} /></label>
        )}
        <label className="field"><span>API key{p.key ? "" : " (optional)"}</span>
          <input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} /></label>
        <div className="key-row">
          <button className="btn primary sm" disabled={busy || !ready}>{busy ? "Connecting…" : "Connect"}</button>
          <a className="small" href={p.signup} target="_blank" rel="noreferrer">
            {p.key ? `Get a ${p.free ? "free " : ""}key` : "Get Ollama"} ↗</a>
        </div>
      </>}
      <p className="small dim">Your key stays in this server's memory for your session only; it is never stored or shown again.</p>
    </form>
  );
}

export function SessionNotice() {
  const notice = usePlant((s) => s.notice);
  if (!notice) return null;
  return <div className="banner warn">{notice.text}</div>;
}
