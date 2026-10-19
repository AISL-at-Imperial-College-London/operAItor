import { useEffect, useState } from "react";
import { listModels } from "../browserLlm";
import { availableModels, changeModel, connectLlm, forgetKey, usePlant } from "../store";

const OTHER = "other:";   // the dropdown's "type a name" entry (no model id starts like this)

/** Pick a model: the ones this connection can use (listed live by the provider or Ollama), the popular ones first,
 *  or any other name typed in. The browser checks a typed name with the provider before the agents use it. */
function ModelSelect({ value, onChange, listed, popular }:
  { value: string; onChange: (m: string) => void; listed: string[]; popular: string[] }) {
  const [typing, setTyping] = useState(false);
  const live = listed.length > 0;
  const top = live ? popular.filter((m) => listed.includes(m)) : popular;   // a live list drops retired names
  const rest = listed.filter((m) => !top.includes(m));
  const free = rest.filter((m) => m.endsWith(":free"));
  const paid = rest.filter((m) => !m.endsWith(":free"));
  const custom = typing || (!!value && !top.includes(value) && !rest.includes(value));
  const opts = (ms: string[]) => ms.map((m) => <option key={m} value={m}>{m}</option>);
  return (
    <>
      <select aria-label="Model" value={custom ? OTHER : value} onChange={(e) => {
        const typed = e.target.value === OTHER;
        setTyping(typed);
        if (!typed) onChange(e.target.value);
      }}>
        {top.length > 0 && <optgroup label="Popular">{opts(top)}</optgroup>}
        {free.length > 0 && <optgroup label="Free">{opts(free)}</optgroup>}
        {paid.length > 0 && <optgroup label={free.length ? "Paid" : "All models"}>{opts(paid)}</optgroup>}
        <option value={OTHER}>Other model (type its name)…</option>
      </select>
      {custom && <input type="text" aria-label="Model name" value={value} onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. provider/model-name" spellCheck={false} autoFocus />}
    </>
  );
}

/** Switch the agents' model at any time; the key (or the server's Ollama) stays connected. */
function ModelPicker({ current, provider }: { current: string; provider: string }) {
  const busy = usePlant((s) => s.keyBusy);
  const serverModels = usePlant((s) => s.serverModels);
  const popular = usePlant((s) => s.session?.llm.providers?.[provider]?.models ?? []);
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
      <ModelSelect value={value} onChange={setValue} listed={options} popular={popular} />
      <button className="btn primary sm" disabled={busy || !value.trim() || value.trim() === current}>Use this model</button>
      <button type="button" className="btn sm" onClick={() => setOpen(false)}>Cancel</button>
      <span className="dim small">{listed === null && !serverModels.length ? "finding models…" : `${options.length} available`}</span>
    </form>
  );
}

/** Where the visitor connects the agents to an LLM: free tiers and Ollama first, OpenAI optional. Always reachable:
 *  also when the agents already run on a model (e.g. the Ollama this computer has, which the server picks itself). */
export function LlmKeyCard() {
  const session = usePlant((s) => s.session);
  const usage = usePlant((s) => s.llmUsage);
  const [switching, setSwitching] = useState(false);
  const conn = session?.llm.connection;
  useEffect(() => setSwitching(false), [conn?.provider, conn?.model]);
  if (!session) return null;
  const llm = session.llm;

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
          {(conn.in_browser || (llm.key_source === "server" && conn.provider === "ollama")) && <ModelPicker current={conn.model} provider={conn.provider} />}
          <button className="btn sm" aria-expanded={switching} onClick={() => setSwitching(!switching)}>
            {switching ? "Cancel" : "Use another provider"}</button>
          {llm.key_source === "visitor" && <button className="btn sm" onClick={forgetKey}>Disconnect</button>}
        </div>
        {switching && <ProviderForm />}
      </div>
    );
  }
  return <ProviderForm />;
}

/** Choose a provider (Groq, Gemini, OpenRouter, Ollama, OpenAI), a model and a key; the browser makes the calls. */
function ProviderForm() {
  const session = usePlant((s) => s.session);
  const busy = usePlant((s) => s.keyBusy);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const [found, setFound] = useState<string[]>([]);
  const providers = session?.llm.providers ?? {};
  const pid = provider || Object.keys(providers)[0] || "";
  const p = providers[pid];
  // Ollama and OpenRouter list their models without a key: straight away. The others once the key is typed.
  useEffect(() => {
    setFound([]);
    if (!p || (p.key && pid !== "openrouter")) return;
    let current = true;
    listModels({ provider: pid, model: "", key: "", url: url.trim() }).then((ms) => current && setFound(ms));
    return () => { current = false; };
  }, [pid]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!session) return null;
  const chosenModel = model || (found.length ? p?.models.find((m) => found.includes(m)) ?? found[0] : p?.models[0]) || "";
  const findModels = () => {
    if (p && (!p.key || key.trim())) listModels({ provider: pid, model: "", key: key.trim(), url: url.trim() }).then(setFound);
  };
  const ready = !!p && !!chosenModel && (!p.key || !!key.trim());
  return (
    <form className="key-card" onSubmit={(e) => {
      e.preventDefault();
      if (!ready) return;
      // the key stays typed if the check fails (e.g. a misspelt model); the form closes once connected
      connectLlm({ provider: pid, model: chosenModel.trim(), key: key.trim(), url: url.trim() });
    }}>
      <p className="small dim">The free options need only a free account with the provider, or your own Ollama.</p>
      <label className="field"><span>Provider</span>
        <select value={pid} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
          {Object.entries(providers).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select></label>
      {p && <>
        <div className="field"><span>Model</span>
          <ModelSelect key={pid} value={chosenModel} onChange={setModel} listed={found} popular={p.models} />
          <span className="dim small">{found.length > 0
            ? `${found.length} models available ${pid === "openrouter" ? "on OpenRouter" : `with this ${p.key ? "key" : "Ollama"}`}`
            : p.key ? "Type your key to list every model it can use, or pick Other to type a name." : ""}</span></div>
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
