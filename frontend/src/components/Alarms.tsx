import { useState } from "react";
import { send, usePlant } from "../store";

const STATE_LABEL: Record<string, string> = {
  ACTIVE_UNACK: "active · unacknowledged", ACTIVE_ACK: "active", RTN_UNACK: "returned · unacknowledged",
};

type Row = { t: number; kind: "alarm" | "system"; level: string; text: string; detail?: string; prio?: number };

export function AlarmsTab() {
  const snap = usePlant((s) => s.snap);
  const messages = usePlant((s) => s.messages);
  const [filter, setFilter] = useState<"all" | "alarm" | "system">("all");
  if (!snap) return null;
  const active = snap.alarms.filter((a) => a.state !== "NORMAL")
    .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id));
  const rows: Row[] = [
    ...snap.alarm_history.map((e) => ({ t: e.time_min, kind: "alarm" as const, prio: e.priority,
      level: e.transition === "ACTIVATED" ? (e.priority === 1 ? "bad" : "warn") : "",
      text: `${e.id} ${e.transition.toLowerCase().replace(/_/g, " ")}${e.by ? ` by ${e.by}` : ""}`,
      detail: `${e.description}${e.value != null ? ` · ${e.value.toFixed(3)}` : ""}` })),
    ...messages.map((m) => ({ t: m.time_min, kind: "system" as const, level: m.level === "error" ? "bad" : m.level === "warn" ? "warn" : "", text: m.text })),
  ].filter((r) => filter === "all" || r.kind === filter).sort((a, b) => b.t - a.t);

  return (
    <div className="stack">
      <section className="card">
        <h3>Active alarms <span className="dim">({active.length})</span>
          <span className="spacer" />
          <button className="btn sm" onClick={() => send({ type: "ack_all" })} disabled={!active.some((a) => a.state.endsWith("UNACK"))}>Acknowledge all</button>
          <button className="btn sm" onClick={() => send({ type: "reset_sif" })} disabled={!snap.sif_latched.length}
            title="Reset latched safety functions whose variable is back inside its reset band">Reset SIFs</button></h3>
        {active.length === 0 ? <p className="dim">No active alarms.</p> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>P</th><th>Alarm</th><th>State</th><th className="num">Value</th><th className="num">Limit</th><th>Description</th><th /></tr></thead>
              <tbody>
                {active.map((a) => (
                  <tr key={a.id} className={`alarm-row ${a.state}`}>
                    <td><span className={`prio p${a.priority}`}>{a.priority}</span></td>
                    <td><b>{a.id}</b></td>
                    <td>{STATE_LABEL[a.state] ?? a.state}</td>
                    <td className="num">{a.value?.toFixed(3) ?? "—"}</td>
                    <td className="num">{a.limit} {a.unit}</td>
                    <td className="dim">{a.description}</td>
                    <td>{a.state.endsWith("UNACK") && (
                      <button className="btn xs" onClick={() => send({ type: "action", action: { acknowledge_alarm_ids: [a.id] } })}>Ack</button>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <h3>Event log <span className="spacer" />
          <div className="seg small" role="radiogroup" aria-label="Filter events">
            {(["all", "alarm", "system"] as const).map((f) => (
              <button key={f} role="radio" aria-checked={filter === f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
                {f === "all" ? "All" : f === "alarm" ? "Alarms" : "System"}</button>
            ))}
          </div></h3>
        {rows.length === 0 ? <p className="dim">Nothing yet.</p> : (
          <ul className="event-log">
            {rows.slice(0, 200).map((r, i) => (
              <li key={i} className={r.level}>
                <span className="num dim">{r.t.toFixed(1)}</span>
                <span className={`tag ${r.kind}`}>{r.kind === "alarm" ? `alarm P${r.prio}` : "system"}</span>
                <span>{r.text}{r.detail && <span className="dim"> — {r.detail}</span>}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
