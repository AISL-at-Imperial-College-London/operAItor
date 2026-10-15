// LLM calls made by this browser, with a key that never leaves it (server: operaitor/agents/browser_llm.py).
//
// The agents run on the server; each model call arrives here as an `llm_request`, this tab calls the
// provider directly, and the answer goes back as an `llm_response`. The key is kept in memory only (not in
// localStorage) and is sent ONLY to the provider address below (or the Ollama address the visitor typed),
// never to an address the server names.

export interface LlmConn { provider: string; model: string; key: string; url: string }

export interface LlmRequest {
  id: number; provider: string; model: string; messages: Record<string, unknown>[];
  temperature?: number; reasoning_effort?: string; json?: boolean; tools?: Record<string, unknown>[];
}

export interface LlmReply {
  message: { content: string; tool_calls?: { id?: string; function: { name: string; arguments: unknown } }[] };
  usage: { input_tokens: number; output_tokens: number };
}

// Fixed here, in the page's own code: a key goes to its provider and nowhere else.
const API: Record<string, { label: string; base: string; check: string; json: boolean }> = {
  groq: { label: "Groq", base: "https://api.groq.com/openai/v1", check: "/models", json: true },
  gemini: { label: "Google Gemini", base: "https://generativelanguage.googleapis.com/v1beta/openai", check: "/models", json: false },
  openrouter: { label: "OpenRouter", base: "https://openrouter.ai/api/v1", check: "/key", json: false },
  openai: { label: "OpenAI", base: "https://api.openai.com/v1", check: "/models", json: true },
};
const OLLAMA_DEFAULT = "http://localhost:11434";
const CALL_TIMEOUT_MS = 200_000;

const ollamaRoot = (url: string) => (url.trim() || OLLAMA_DEFAULT).replace(/\/+$/, "").replace(/\/v1$/, "");
const label = (c: LlmConn) => (c.provider === "ollama" ? "your Ollama" : API[c.provider]?.label ?? c.provider);

async function fetchWithTimeout(url: string, init: RequestInit, ms: number): Promise<Response> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: ctl.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function errorText(r: Response): Promise<string> {
  try {
    const body = await r.json();
    const e = body?.error;
    return String(e?.message ?? e ?? body?.message ?? "").slice(0, 200);
  } catch {
    return "";
  }
}

/** Check the key / address from this browser before connecting. Returns an error message, or null when it works. */
export async function checkConnection(c: LlmConn): Promise<string | null> {
  try {
    if (c.provider === "ollama") {
      const r = await fetchWithTimeout(`${ollamaRoot(c.url)}/api/tags`, {}, 10_000);
      if (!r.ok) return `your Ollama answered ${r.status}`;
      const names = new Set(((await r.json()).models ?? []).map((m: { name: string }) => m.name));
      if (names.size && !names.has(c.model) && !names.has(`${c.model}:latest`))
        return `your Ollama has no model "${c.model}"; it has ${[...names].slice(0, 8).join(", ")}`;
      return null;
    }
    const api = API[c.provider];
    if (!api) return `unknown provider ${c.provider}`;
    if (!c.key) return `${api.label} needs an API key`;
    const r = await fetchWithTimeout(api.base + api.check, { headers: { Authorization: `Bearer ${c.key}` } }, 10_000);
    if (r.ok) return null;
    if (r.status === 401 || r.status === 403) return `${api.label} rejected this key (${r.status})`;
    const why = await errorText(r);
    return `${api.label} answered ${r.status}${why ? `: ${why}` : ""}`;
  } catch (e) {
    return c.provider === "ollama"
      ? `could not reach your Ollama at ${ollamaRoot(c.url)} from this browser (is it running, and does OLLAMA_ORIGINS allow this site?)`
      : `could not reach ${label(c)} from this browser (${(e as Error).name})`;
  }
}

/** One model call from this browser. Retries rate limits and server errors twice. */
export async function callLlm(c: LlmConn, req: LlmRequest): Promise<LlmReply> {
  if (req.provider !== c.provider) throw new Error("the server asked for a different provider than the one connected here");
  for (let attempt = 0; ; attempt++) {
    const r = c.provider === "ollama" ? await ollamaCall(c, req) : await openAiCall(c, req);
    if (r.ok) return c.provider === "ollama" ? ollamaReply(await r.json()) : openAiReply(await r.json());
    const retriable = r.status === 429 || r.status >= 500;
    if (!retriable || attempt >= 2) {
      const why = await errorText(r);
      throw new Error(`${label(c)} answered ${r.status}${why ? `: ${why}` : ""}`);
    }
    const after = Number(r.headers.get("retry-after"));
    await new Promise((res) => setTimeout(res, Math.min(30, Number.isFinite(after) && after > 0 ? after : 2 ** (attempt + 1)) * 1000));
  }
}

function openAiCall(c: LlmConn, req: LlmRequest): Promise<Response> {
  const api = API[c.provider];
  const body: Record<string, unknown> = { model: c.model, messages: req.messages };
  if (req.temperature !== undefined) body.temperature = req.temperature;
  if (req.reasoning_effort) body.reasoning_effort = req.reasoning_effort;
  if (req.json && api.json) body.response_format = { type: "json_object" };
  if (req.tools?.length) body.tools = req.tools;
  const headers: Record<string, string> = { Authorization: `Bearer ${c.key}`, "Content-Type": "application/json" };
  if (c.provider === "openrouter") headers["X-Title"] = "OperAItor";
  return fetchWithTimeout(`${api.base}/chat/completions`, { method: "POST", headers, body: JSON.stringify(body) }, CALL_TIMEOUT_MS);
}

function openAiReply(data: any): LlmReply {
  const m = data?.choices?.[0]?.message ?? {};
  return { message: { content: m.content ?? "", tool_calls: m.tool_calls ?? [] },
           usage: { input_tokens: data?.usage?.prompt_tokens ?? 0, output_tokens: data?.usage?.completion_tokens ?? 0 } };
}

// Ollama's native API: its OpenAI-compatible endpoint would cut the agents' 7-12k-token prompts to the
// default context, and the native one takes tool-call arguments as objects.
function ollamaCall(c: LlmConn, req: LlmRequest): Promise<Response> {
  const messages = req.messages.map((m: any) => m.role === "assistant" && m.tool_calls
    ? { ...m, tool_calls: m.tool_calls.map((t: any) => ({ function: { name: t.function.name,
        arguments: typeof t.function.arguments === "string" ? safeJson(t.function.arguments) : t.function.arguments } })) }
    : m);
  const body: Record<string, unknown> = { model: c.model, messages, stream: false,
    options: { temperature: req.temperature ?? 0, num_ctx: 16384, num_predict: 2048 } };
  if (req.json) body.format = "json";
  if (req.tools?.length) body.tools = req.tools;
  return fetchWithTimeout(`${ollamaRoot(c.url)}/api/chat`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }, CALL_TIMEOUT_MS);
}

function ollamaReply(data: any): LlmReply {
  const m = data?.message ?? {};
  return { message: { content: m.content ?? "", tool_calls: (m.tool_calls ?? []).map((t: any, i: number) => ({ id: `call_${i}`, function: t.function })) },
           usage: { input_tokens: data?.prompt_eval_count ?? 0, output_tokens: data?.eval_count ?? 0 } };
}

function safeJson(s: string): unknown {
  try { return JSON.parse(s || "{}"); } catch { return {}; }
}

// Not chat models: speech, images, embeddings, moderation and the like.
const NOT_CHAT = /(embed|tts|whisper|audio|realtime|transcri|image|dall-e|moderation|guard|search|aqa|babbage|davinci)/i;

/** The chat models this connection can use, listed by the provider from this browser ([] if unavailable). */
export async function listModels(c: LlmConn): Promise<string[]> {
  try {
    if (c.provider === "ollama") {
      const r = await fetchWithTimeout(`${ollamaRoot(c.url)}/api/tags`, {}, 10_000);
      if (!r.ok) return [];
      return ((await r.json()).models ?? []).map((m: { name: string }) => m.name).sort();
    }
    const api = API[c.provider];
    if (!api || !c.key) return [];
    const r = await fetchWithTimeout(`${api.base}/models`, { headers: { Authorization: `Bearer ${c.key}` } }, 10_000);
    if (!r.ok) return [];
    let ids: string[] = ((await r.json()).data ?? []).map((m: { id: string }) => String(m.id).replace(/^models\//, ""));
    ids = ids.filter((id) => !NOT_CHAT.test(id));
    if (c.provider === "openai") ids = ids.filter((id) => /^(gpt|o\d|chatgpt)/.test(id));
    if (c.provider === "gemini") ids = ids.filter((id) => id.startsWith("gemini"));
    const free = (id: string) => (id.endsWith(":free") ? 0 : 1);   // OpenRouter: free models first
    return [...new Set(ids)].sort((a, b) => free(a) - free(b) || a.localeCompare(b));
  } catch {
    return [];
  }
}
