import { useEffect, useRef, useState, type ReactNode } from "react";
import { ask, usePlant } from "../store";

const SUGGESTIONS = [
  "status",
  "why is there a fault?",
  "how is the reactor level moving?",
  "what is R_PIC?",
  "what did the agents do?",
  "tell me about the reflux pump",
];

// Minimal markdown: **bold**, `code`, line breaks.
function render(text: string): ReactNode[] {
  return text.split("\n").map((line, i) => (
    <div key={i}>
      {line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, j) =>
        part.startsWith("**") ? <b key={j}>{part.slice(2, -2)}</b>
          : part.startsWith("`") ? <code key={j}>{part.slice(1, -1)}</code>
          : <span key={j}>{part}</span>)}
    </div>
  ));
}

export function ChatPanel() {
  const chat = usePlant((s) => s.chat);
  const mode = usePlant((s) => s.chatMode);
  const model = usePlant((s) => s.chatModel);
  const connected = usePlant((s) => s.connected);
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [chat]);

  const submit = (q: string) => {
    const t = q.trim();
    if (!t) return;
    ask(t);
    setText("");
  };

  return (
    <div className="panel-body chat">
      <p className="hint">
        Ask what things are (knowledge graph) and how the plant is moving (live data).{" "}
        <span className={`pill ${mode === "llm" ? "pill-ok" : ""}`} title={mode === "llm" ? "LLM with tools" : "deterministic intent router; set OPENAI_API_KEY (and install langchain-openai) for LLM chat"}>
          {mode === "llm" ? (model ?? "LLM") : "offline"}
        </span>
      </p>
      <div className="chat-log" aria-live="polite">
        {chat.length === 0 && <div className="empty">Try one of the questions below.</div>}
        {chat.map((m, i) => (
          <div key={i} className={`bubble ${m.role} ${m.pending ? "pending" : ""}`}>
            {render(m.text)}
            {m.tools && m.tools.length > 0 && <div className="tools">tools: {m.tools.join(", ")}</div>}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="chips">
        {SUGGESTIONS.map((s) => <button key={s} className="btn xs" onClick={() => submit(s)}>{s}</button>)}
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        <input type="text" value={text} placeholder={connected ? "Ask about the plant…" : "Not connected"}
          onChange={(e) => setText(e.target.value)} aria-label="Chat message" />
        <button className="btn primary" type="submit" disabled={!connected || !text.trim()}>Send</button>
      </form>
    </div>
  );
}
