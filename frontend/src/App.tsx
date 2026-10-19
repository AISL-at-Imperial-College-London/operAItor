import { useEffect, useState } from "react";
import { AgentsTab } from "./components/AgentsTab";
import { AlarmsTab } from "./components/Alarms";
import { ChatPanel } from "./components/Chat";
import { FaultsPanel, OperatorPanel } from "./components/ControlPanels";
import { HumanTestPanel } from "./components/HumanTest";
import { SessionNotice } from "./components/LlmKey";
import { PlantTab } from "./components/PlantTab";
import { DRAWERS, Header, type Drawer, type Tab, TABS } from "./components/TopBar";
import { Tour, tourSeen } from "./components/Tour";
import { Trends } from "./components/Trends";
import { API_VERSION, usePlant } from "./store";

const DRAWER_TITLE: Record<Drawer, string> = { operate: "Operate the plant", assistant: "Assistant", faults: "Inject faults" };

function load<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v !== null && allowed.includes(v) ? v : fallback;
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

export default function App() {
  const [tab, setTab] = useState<Tab>(() => load("tab", "plant", TABS.map((t) => t.id)));
  // One drawer at a time; "" = closed. Wide screens start with the operator panel open.
  const [drawer, setDrawer] = useState<Drawer | "">(() =>
    load("drawer", window.innerWidth >= 1500 ? "operate" : "", ["", ...DRAWERS.map((d) => d.id)] as const));
  const snap = usePlant((s) => s.snap);
  const connected = usePlant((s) => s.connected);
  const backendVersion = usePlant((s) => s.backendVersion);
  const notice = usePlant((s) => s.notice);
  const humanTest = usePlant((s) => s.humanTest?.state === "running");
  const nav = usePlant((s) => s.nav);
  useEffect(() => {  // another panel asked to show a tab or a drawer
    if (!nav) return;
    if (nav.tab) setTab(nav.tab);
    if (nav.drawer !== undefined) setDrawer(nav.drawer);
  }, [nav]);
  // The tour starts by itself on the first visit, once the plant is there (not for automated browsers: the tests).
  const [touring, setTouring] = useState(false);
  const ready = !!snap;
  useEffect(() => { if (ready && !tourSeen() && !navigator.webdriver) setTouring(true); }, [ready]);

  useEffect(() => save("tab", tab), [tab]);
  useEffect(() => save("drawer", drawer), [drawer]);

  return (
    <div className={`app ${drawer ? "with-drawer" : ""}`}>
      <Header tab={tab} onTab={setTab} drawer={drawer} onDrawer={(d) => setDrawer((cur) => (cur === d ? "" : d))}
        onTour={() => setTouring(true)} />
      {connected && backendVersion !== null && backendVersion < API_VERSION && (
        <div className="banner warn">
          The plant server runs older code than this console (API v{backendVersion}, console needs v{API_VERSION}).
          Restart it (<code>operaitor serve</code>) and reload.
        </div>
      )}
      <SessionNotice />
      {snap?.operator_mode === "human" && !humanTest && (
        <div className="banner warn">
          You are the operator: the agents are off.
          {drawer !== "operate" && <button className="btn sm" onClick={() => setDrawer("operate")}>Start a scored human test</button>}
        </div>
      )}
      {!connected && !notice && <div className="banner warn">Connecting to the plant server…</div>}
      <div className="layout">
        <main>
          {snap ? (
            <>
              {tab === "plant" && <PlantTab onAlarms={() => setTab("alarms")} />}
              {tab === "trends" && <Trends />}
              {tab === "agents" && <AgentsTab />}
              {tab === "alarms" && <AlarmsTab />}
            </>
          ) : <div className="empty pad">Waiting for the plant…</div>}
        </main>
        {drawer && (
          <aside className="drawer" aria-label={DRAWER_TITLE[drawer]}>
            <div className="drawer-head">
              <h2 className="drawer-title">{DRAWER_TITLE[drawer]}</h2>
              <button className="icon-btn" onClick={() => setDrawer("")} aria-label="Close panel">✕</button>
            </div>
            <div className="drawer-body">
              {drawer === "operate" ? <>{snap?.operator_mode === "human" && <HumanTestPanel />}<OperatorPanel /></>
                : drawer === "assistant" ? (humanTest ? <p className="hint">The assistant is off during a human test: you are on your own.</p> : <ChatPanel />)
                : <FaultsPanel />}
            </div>
          </aside>
        )}
        {drawer && <div className="scrim" onClick={() => setDrawer("")} />}
      </div>
      <Toast />
      {touring && snap && <Tour onClose={() => setTouring(false)} />}
    </div>
  );
}
