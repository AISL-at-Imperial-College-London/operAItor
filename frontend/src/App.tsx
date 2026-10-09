import { useEffect, useState } from "react";
import { Alarms } from "./components/Alarms";
import { ChatPanel } from "./components/Chat";
import { MasterPanel, OperatorPanel } from "./components/ControlPanels";
import { Faceplates } from "./components/Faceplates";
import { FddPanel } from "./components/FddPanel";
import { Overview } from "./components/Overview";
import { TopBar } from "./components/TopBar";
import { Trends } from "./components/Trends";
import { API_VERSION, usePlant } from "./store";

const TABS = ["Overview", "Trends", "FDD & Agents", "Alarms & events"] as const;
type Tab = (typeof TABS)[number];
const SIDES = ["Operator", "Assistant", "Master"] as const;
type Side = (typeof SIDES)[number];
const SIDE_LABEL: Record<Side, string> = { Operator: "Operator", Assistant: "Assistant", Master: "Master / faults" };

function load<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch { /* storage unavailable */ }
}

function Toast() {
  const toast = usePlant((s) => s.toast);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!toast) return;
    setVisible(true);
    const id = setTimeout(() => setVisible(false), 3500);
    return () => clearTimeout(id);
  }, [toast]);
  if (!toast || !visible) return null;
  return <div className={`toast ${toast.ok ? "ok" : "bad"}`} role="status">{toast.text}</div>;
}

function Messages() {
  const messages = usePlant((s) => s.messages);
  return (
    <div className="card">
      <div className="card-title">Event log</div>
      <ul className="msgs">
        {[...messages].reverse().slice(0, 30).map((m, i) => (
          <li key={i} className={m.level}><span className="num t">{m.time_min.toFixed(1)}</span> {m.text}</li>
        ))}
      </ul>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>(() => load("tab", "Overview", TABS));
  const [side, setSide] = useState<Side>(() => load("side", "Operator", SIDES));
  const [drawer, setDrawer] = useState(false);
  const snap = usePlant((s) => s.snap);
  const connected = usePlant((s) => s.connected);
  const backendVersion = usePlant((s) => s.backendVersion);

  useEffect(() => save("tab", tab), [tab]);
  useEffect(() => save("side", side), [side]);

  return (
    <div className="app">
      <TopBar onToggleDrawer={() => setDrawer((d) => !d)} />
      {connected && backendVersion !== null && backendVersion < API_VERSION && (
        <div className="offline outdated">
          The plant server is running older code than this console (API v{backendVersion}, console needs v{API_VERSION}).
          Stop it with Ctrl+C and start it again: <code>py -3 run_server.py</code>. Until then the agent pipeline will not update.
        </div>
      )}
      {!connected && (
        <div className="offline">Connecting to the plant server… start it with <code>python run_server.py</code></div>
      )}
      <div className="layout">
        <main>
          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>
            ))}
          </nav>
          {snap && (
            <div className="tab-body">
              {tab === "Overview" && (<><Overview /><Faceplates /><Alarms compact /></>)}
              {tab === "Trends" && <Trends />}
              {tab === "FDD & Agents" && <FddPanel />}
              {tab === "Alarms & events" && (<><Alarms /><Messages /></>)}
            </div>
          )}
        </main>
        <aside className={`side ${drawer ? "open" : ""}`}>
          <div className="side-head">
            <nav className="tabs small" role="tablist">
              {SIDES.map((t) => (
                <button key={t} role="tab" aria-selected={side === t} className={side === t ? "on" : ""} onClick={() => setSide(t)}>
                  {SIDE_LABEL[t]}
                </button>
              ))}
            </nav>
            <button className="btn sm drawer-close" onClick={() => setDrawer(false)} aria-label="Close controls">✕</button>
          </div>
          {side === "Operator" ? <OperatorPanel /> : side === "Assistant" ? <ChatPanel /> : <MasterPanel />}
        </aside>
        {drawer && <div className="scrim" onClick={() => setDrawer(false)} />}
      </div>
      <Toast />
    </div>
  );
}
