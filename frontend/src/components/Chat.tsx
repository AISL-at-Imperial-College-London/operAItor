import { useEffect, useRef, useState, type ReactNode } from "react";
import { ask, navigate, usePlant } from "../store";

const SUGGESTIONS = [
  "status",
  "why is there a fault?",
  "what did the agents do?",
  "what is R_PIC?",
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
        Ask what a tag or unit is, how a variable is moving, or what the agents did.{" "}
        {mode === "llm" ? <span className="pill pill-ok" title="LLM with plant tools">{model ?? "LLM"}</span> : <>
          <span className="pill" title="Rule-based answers without an LLM">basic answers</span>{" "}
          <button className="link-btn" onClick={() => navigate({ tab: "agents" })}>connect an LLM</button></>}
      </p>
      <div className="chat-log" aria-live="polite">
        {chat.length === 0 && <div className="empty">Ask anything, or try a suggestion below.</div>}
        {chat.map((m, i) => (
          <div key={i} className={`bubble ${m.role} ${m.pending ? "pending" : ""}`}>
            {render(m.text)}
            {m.tools && m.tools.length > 0 && <div className="tools">tools: {m.tools.join(", ")}</div>}
          </div>
        ))}
        <div ref={end} />
      </div>
      {chat.length === 0 && <div className="chips">
        {SUGGESTIONS.map((s) => <button key={s} className="btn xs" onClick={() => submit(s)}>{s}</button>)}
      </div>}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        <input type="text" value={text} placeholder={connected ? "Ask about the plant…" : "Not connected"}
          onChange={(e) => setText(e.target.value)} aria-label="Chat message" />
        <button className="btn primary" type="submit" disabled={!connected || !text.trim()}>Send</button>
      </form>
    </div>
  );
}
