import { useEffect, useState } from "react";
import { listModels } from "../browserLlm";
import { availableModels, changeModel, connectLlm, forgetKey, usePlant } from "../store";

/** Switch the agents' model at any time; the key (or the server's Ollama) stays connected. */
function ModelPicker({ current }: { current: string }) {
  const busy = usePlant((s) => s.keyBusy);
  const serverModels = usePlant((s) => s.serverModels);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(current);
  const [listed, setListed] = useState<string[] | null>(null);
  useEffect(() => setValue(current), [current]);
  useEffect(() => {
    if (!open) return;
    setListed(null);
    availableModels().then(setListed);
  }, [open]);
  const options = [...new Set([...(listed ?? []), ...serverModels])];
  if (!open) return <button className="btn sm" onClick={() => setOpen(true)}>Change model</button>;
  return (
    <form className="key-row model-picker" onSubmit={(e) => {
      e.preventDefault();
      const m = value.trim();
      if (m && m !== current) changeModel(m);
      setOpen(false);
    }}>
      <input type="text" list="model-options" value={value} onChange={(e) => setValue(e.target.value)} spellCheck={false}
        aria-label="Model" placeholder="model name" autoFocus />
      <datalist id="model-options">{options.map((m) => <option key={m} value={m} />)}</datalist>
      <button className="btn primary sm" disabled={busy || !value.trim() || value.trim() === current}>Use this model</button>
      <button type="button" className="btn sm" onClick={() => setOpen(false)}>Cancel</button>
      <span className="dim small">{listed === null && !serverModels.length ? "finding models…" : `${options.length} available`}</span>
    </form>
  );
}

/** Where the visitor connects the agents to an LLM: free tiers and Ollama first, OpenAI optional. */
export function LlmKeyCard() {
  const session = usePlant((s) => s.session);
  const busy = usePlant((s) => s.keyBusy);
  const usage = usePlant((s) => s.llmUsage);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const [found, setFound] = useState<string[]>([]);
  useEffect(() => setFound([]), [provider]);
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
        {conn.in_browser && <div className="dim small">
          {conn.provider === "ollama" ? "Your browser makes these calls to your Ollama; this server never connects to it."
            : "Your browser makes these calls with your key; this server never sees it. Reloading forgets the key."}
          {" "}Keep this tab open while the agents work.</div>}
        <div className="key-row">
          {(conn.in_browser || (llm.key_source === "server" && conn.provider === "ollama")) && <ModelPicker current={conn.model} />}
          {llm.key_source === "visitor" && <button className="btn sm" onClick={forgetKey}>Disconnect</button>}
        </div>
      </div>
    );
  }

  const pid = provider || Object.keys(providers)[0] || "";
  const p = providers[pid];
  const chosenModel = model || p?.models[0] || "";
  const findModels = () => {
    if (p && (!p.key || key.trim())) listModels({ provider: pid, model: "", key: key.trim(), url: url.trim() }).then(setFound);
  };
  const ready = !!p && !!chosenModel && (!p.key || !!key.trim());
  return (
    <form className="key-card" onSubmit={(e) => {
      e.preventDefault();
      if (!ready) return;
      connectLlm({ provider: pid, model: chosenModel.trim(), key: key.trim(), url: url.trim() });
      setKey("");
    }}>
      <p className="small dim">The free options need only a free account with the provider, or your own Ollama.</p>
      <label className="field"><span>Provider</span>
        <select value={pid} onChange={(e) => { setProvider(e.target.value); setModel("");
          if (providers[e.target.value] && !providers[e.target.value].key)   // Ollama: list its models straight away
            listModels({ provider: e.target.value, model: "", key: "", url: url.trim() }).then(setFound); }}>
          {Object.entries(providers).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select></label>
      {p && <>
        <label className="field"><span>Model</span>
          <input type="text" list={`models-${pid}`} value={chosenModel} onChange={(e) => setModel(e.target.value)} spellCheck={false} />
          <datalist id={`models-${pid}`}>{[...new Set([...found, ...p.models])].map((m) => <option key={m} value={m} />)}</datalist>
          {found.length > 0 && <span className="dim small">{found.length} models available with this {p.key ? "key" : "Ollama"}</span>}</label>
        {p.url && (
          <label className="field"><span>Ollama address (blank = Ollama on your own computer)</span>
            <input type="text" placeholder="http://localhost:11434"
              value={url} onChange={(e) => setUrl(e.target.value)} onBlur={findModels} spellCheck={false} /></label>
        )}
        <label className="field"><span>API key{p.key ? "" : " (optional)"}</span>
          <input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)}
            onBlur={findModels} /></label>
        <div className="key-row">
          <button className="btn primary sm" disabled={busy || !ready}>{busy ? "Connecting…" : "Connect"}</button>
          <a className="small" href={p.signup} target="_blank" rel="noreferrer">
            {p.key ? `Get a ${p.free ? "free " : ""}key` : "Get Ollama"} ↗</a>
        </div>
      </>}
      <p className="small dim">
        Your key stays in this browser tab, in memory only. Your browser calls the provider directly, so this server
        never receives the key. {p?.url ? "Ollama must allow this site: set OLLAMA_ORIGINS to this page's address." : ""}
      </p>
    </form>
  );
}

export function SessionNotice() {
  const notice = usePlant((s) => s.notice);
  if (!notice) return null;
  return <div className="banner warn">{notice.text}</div>;
}
