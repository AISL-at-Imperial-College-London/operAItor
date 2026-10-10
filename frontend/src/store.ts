import { useSyncExternalStore } from "react";
import type { AgentEvent, AgentInfo, Brief, Campaign, ChatMessage, Hello, LlmUsage, Message, Meta, MonitorEvent, Num, SessionInfo, Snapshot, TrendPoint } from "./types";

const MAX_POINTS = 1440;

export type Columns = Record<string, Num[]>;

// Must match API_VERSION in server/plant_service.py.
export const API_VERSION = 7;

const SESSION_KEY = "plant-session";
function storedSession(): string {
  try { return sessionStorage.getItem(SESSION_KEY) ?? ""; } catch { return ""; }
}
function storeSession(id: string) {
  try { if (id) sessionStorage.setItem(SESSION_KEY, id); else sessionStorage.removeItem(SESSION_KEY); } catch { /* unavailable */ }
}

export interface PlantState {
  connected: boolean;
  backendVersion: number | null;
  meta: Meta | null;
  snap: Snapshot | null;
  trend: Columns;          // mutated in place; `trendVersion` signals changes
  trendVersion: number;
  monitor: Columns;        // t, t2, t2_limit, spe, spe_limit, fault
  lastMonitor: MonitorEvent | null;
  agentLog: AgentEvent[];
  agent: AgentInfo | null;
  pending: Record<number, AgentEvent>;
  messages: Message[];
  toast: { ok: boolean; text: string; at: number } | null;
  brief: Brief | null;          // planning agent's latest brief
  campaign: Campaign | null;
  chat: ChatMessage[];
  chatMode: string;
  chatModel: string | null;
  llmUsage: LlmUsage | null;
  pipeline: Pipeline;
  session: SessionInfo | null;
  notice: { kind: "busy" | "expired"; text: string } | null;
  keyBusy: boolean;
}

export type NodeStatus = "idle" | "running" | "ok" | "warn" | "fail" | "skip";
export const PIPELINE_NODES = ["monitoring", "kg_retrieval", "planning", "action_propose",
  "twin_evaluate", "validate", "reprompt", "dispatch"] as const;
export interface Pipeline {
  cycle: number;
  t: number | null;
  status: Record<string, NodeStatus>;
  attempts: number;
  outcome: string | null;
}

const idlePipeline = (cycle = 0, t: number | null = null): Pipeline => ({
  cycle, t, attempts: 0, outcome: null,
  status: Object.fromEntries(PIPELINE_NODES.map((n) => [n, "idle"])) as Record<string, NodeStatus>,
});

// How each agent event moves the pipeline view of its decision cycle.
function advance(p: Pipeline, ev: AgentEvent): Pipeline {
  if (ev.cycle == null || ev.cycle < p.cycle) return p;
  const next = ev.cycle > p.cycle ? { ...idlePipeline(ev.cycle, ev.time_min) } : { ...p, status: { ...p.status } };
  next.status.monitoring = "ok";
  const set = (n: string, s: NodeStatus) => { next.status[n] = s; };
  switch (ev.stage) {
    case "node": if (ev.node) set(ev.node, "running"); if (ev.node === "action_propose" && (ev.attempt ?? 0) > 0) set("reprompt", "ok"); break;
    case "kg": set("kg_retrieval", "ok"); break;
    case "plan": set("planning", "ok"); break;
    case "proposal": set("action_propose", "ok"); next.attempts = (ev.attempt ?? 0) + 1; break;
    case "invalid": set("action_propose", "warn"); break;
    case "twin": set("twin_evaluate", ev.restored ? "ok" : ev.safe_reached ? "warn" : "fail"); break;
    case "twin_skipped": set("twin_evaluate", "skip"); break;
    case "admit": set("twin_evaluate", ev.admissible ? "ok" : "fail"); break;
    case "validation": set("validate", ev.accepted ? "ok" : "fail"); break;
    case "reprompt": set("reprompt", "running"); break;
    case "fallback": set("validate", "warn"); next.outcome = "mitigation"; break;
    case "applied": case "pending": case "advisory":
      set("dispatch", "ok"); next.outcome = ev.kind === "mitigation" ? `${ev.stage} (mitigation)` : ev.stage; break;
    case "rejected": set("dispatch", "fail"); next.outcome = "rejected"; break;
  }
  return next;
}

function emptyMonitor(): Columns {
  return { t: [], t2: [], t2_limit: [], spe: [], spe_limit: [] };
}

function pushTrend(cols: Columns, p: TrendPoint) {
  const keys = Object.keys(p);
  if (!cols.t) keys.forEach((k) => (cols[k] = []));
  const n = cols.t.length;
  if (n && (cols.t[n - 1] as number) >= p.t) return; // duplicate tick
  for (const k of Object.keys(cols)) {
    cols[k].push(p[k] ?? null);
    if (cols[k].length > MAX_POINTS) cols[k].shift();
  }
}

function pushMonitor(cols: Columns, m: MonitorEvent) {
  if (!m.pca) return;
  const n = cols.t.length;
  if (n && (cols.t[n - 1] as number) >= m.time_min) return;
  cols.t.push(m.time_min);
  cols.t2.push(m.pca.t2);
  cols.t2_limit.push(m.pca.t2_limit);
  cols.spe.push(m.pca.spe);
  cols.spe_limit.push(m.pca.spe_limit);
  for (const k of Object.keys(cols)) if (cols[k].length > MAX_POINTS) cols[k].shift();
}

class PlantStore {
  state: PlantState = {
    connected: false, backendVersion: null, meta: null, snap: null, trend: {}, trendVersion: 0,
    monitor: emptyMonitor(), lastMonitor: null, agentLog: [], agent: null, pending: {},
    messages: [], toast: null, brief: null, campaign: null, chat: [], chatMode: "offline", chatModel: null, llmUsage: null,
    pipeline: idlePipeline(), session: null, notice: null, keyBusy: false,
  };
  // The visitor's LLM connection (incl. key) lives only in this page's memory (never in browser storage), so a
  // new session (after a server restart or session expiry) can be re-keyed without retyping.
  private llmConn: { provider: string; model: string; key: string; url: string } | null = null;
  private chatId = 0;
  private listeners = new Set<() => void>();
  private ws: WebSocket | null = null;
  private retry = 500;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private set(patch: Partial<PlantState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sid = storedSession();
    const ws = new WebSocket(`${proto}://${location.host}/ws${sid ? `?session=${encodeURIComponent(sid)}` : ""}`);
    this.ws = ws;
    ws.onopen = () => { this.retry = 500; this.set({ connected: true }); };
    ws.onclose = () => {
      this.set({ connected: false });
      const wait = this.state.notice?.kind === "busy" ? 30000 : this.retry;
      setTimeout(() => this.connect(), wait);
      this.retry = Math.min(this.retry * 2, 8000);
    };
    ws.onmessage = (e) => this.handle(JSON.parse(e.data));
  }

  send(msg: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
    else this.set({ toast: { ok: false, text: "Not connected to plant server", at: Date.now() } });
  }

  private applyHello(h: Hello) {
    const trend: Columns = {};
    h.trend.forEach((p) => pushTrend(trend, p));
    const monitor = emptyMonitor();
    h.monitor.forEach((m) => pushMonitor(monitor, m));
    const pending: Record<number, AgentEvent> = {};
    (h.agent?.pending ?? []).forEach((p) => (pending[p.proposal_id] = p));
    const brief = [...h.agent_log].reverse().find((e) => e.stage === "plan")?.brief ?? null;
    const pipeline = h.agent_log.reduce(advance, idlePipeline());
    this.set({
      backendVersion: h.api_version ?? 0,
      meta: h.meta, snap: h.snapshot, trend, trendVersion: this.state.trendVersion + 1,
      monitor, lastMonitor: h.monitor.at(-1) ?? null, agentLog: h.agent_log.slice(-300),
      agent: h.agent ?? this.state.agent, pending, messages: h.messages, brief, pipeline,
      campaign: h.campaign ?? null, chatMode: h.chat?.mode ?? this.state.chatMode,
      chatModel: h.chat?.model ?? null, llmUsage: h.agent?.llm_usage ?? this.state.llmUsage,
      session: h.session ?? null, notice: null,
    });
    if (h.session) {
      const fresh = h.session.id !== storedSession();
      storeSession(h.session.id);
      // A new session on a server that needs visitor keys: re-send the key this page already has.
      if (fresh && this.llmConn && h.session.llm.key_source !== "visitor") this.connectLlm(this.llmConn);
    }
  }

  connectLlm(c: { provider: string; model: string; key: string; url: string }) {
    this.llmConn = c;
    this.set({ keyBusy: true });
    this.send({ type: "set_llm", ...c });
  }

  forgetKey() {
    this.llmConn = null;
    this.send({ type: "clear_api_key" });
  }

  private handle(msg: any) {
    switch (msg.type) {
      case "hello":
        this.applyHello(msg);
        break;
      case "busy":
        this.set({ notice: { kind: "busy", text: msg.text } });
        break;
      case "expired":
        storeSession("");
        this.set({ notice: { kind: "expired", text: msg.text } });
        break;
      case "reset":
        this.applyHello(msg.hello);
        break;
      case "snapshot": {
        pushTrend(this.state.trend, msg.trend);
        this.set({ snap: msg, trendVersion: this.state.trendVersion + 1 });
        break;
      }
      case "status":
        if (this.state.snap) this.set({ snap: { ...this.state.snap, ...msg } });
        break;
      case "monitor":
        pushMonitor(this.state.monitor, msg);
        this.set({ lastMonitor: msg, trendVersion: this.state.trendVersion + 1 });
        break;
      case "agent": {
        const ev = msg as AgentEvent;
        const pending = { ...this.state.pending };
        if (ev.stage === "pending" && ev.proposal_id) pending[ev.proposal_id] = ev;
        if ((ev.stage === "approved" || ev.stage === "dismissed") && ev.proposal_id) delete pending[ev.proposal_id];
        this.set({ agentLog: [...this.state.agentLog, ev].slice(-300), pending,
                   brief: ev.stage === "plan" && ev.brief ? ev.brief : this.state.brief,
                   pipeline: advance(this.state.pipeline, ev) });
        break;
      }
      case "llm_usage":
        this.set({ llmUsage: msg.total });
        break;
      case "campaign":
        this.set({ campaign: msg.campaign });
        break;
      case "chat": {
        const chat = this.state.chat.map((m) =>
          m.pending && m.id === msg.id ? { role: "assistant" as const, text: msg.reply, tools: msg.tools, id: msg.id } : m);
        this.set({ chat, chatMode: msg.mode ?? this.state.chatMode });
        break;
      }
      case "agent_mode":
        if (this.state.agent) this.set({ agent: { ...this.state.agent, mode: msg.mode } });
        break;
      case "message":
        this.set({ messages: [...this.state.messages, msg].slice(-50) });
        break;
      case "ack":
        if (msg.request === "set_llm" || msg.request === "set_api_key") {
          if (!msg.ok) this.llmConn = null;
          this.set({ keyBusy: false });
        }
        if (!msg.ok || ["set_llm", "set_api_key", "clear_api_key", "set_agent_preset"].includes(msg.request) || ["action", "inject", "agent_approve", "campaign_start", "campaign_stop"].includes(msg.request))
          this.set({ toast: { ok: msg.ok, text: msg.summary, at: Date.now() } });
        break;
    }
  }

  ask(text: string) {
    const id = ++this.chatId;
    this.set({ chat: [...this.state.chat, { role: "user", text }, { role: "assistant", text: "…", pending: true, id }] });
    this.send({ type: "chat", text, id, session: "console" });
  }
}

export const store = new PlantStore();

export function usePlant<T>(select: (s: PlantState) => T): T {
  return useSyncExternalStore(store.subscribe, () => select(store.state));
}

export const send = (msg: Record<string, unknown>) => store.send(msg);
export const ask = (text: string) => store.ask(text);
export const connectLlm = (c: { provider: string; model: string; key: string; url: string }) => store.connectLlm(c);
export const forgetKey = () => store.forgetKey();
