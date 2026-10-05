import { send, usePlant } from "../store";

const STATE_LABEL: Record<string, string> = {
  ACTIVE_UNACK: "Active · unack", ACTIVE_ACK: "Active", RTN_UNACK: "Returned · unack",
};

export function Alarms({ compact = false }: { compact?: boolean }) {
  const snap = usePlant((s) => s.snap);
  if (!snap) return null;
  const active = snap.alarms
    .filter((a) => a.state !== "NORMAL")
    .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id));
  return (
    <div className="card">
      <div className="card-title row">
        <span>Alarms {active.length ? `(${active.length})` : ""}</span>
        <span className="spacer" />
        <button className="btn sm" onClick={() => send({ type: "ack_all" })}>Acknowledge all</button>
        <button className="btn sm" onClick={() => send({ type: "reset_sif" })}>Reset SIF</button>
      </div>
      {active.length === 0 ? (
        <div className="empty">No active alarms</div>
      ) : (
        <div className="table-wrap">
          <table className="alarms">
            <thead><tr><th>P</th><th>Alarm</th><th>State</th><th>PV</th><th>Limit</th>{!compact && <th>Description</th>}<th></th></tr></thead>
            <tbody>
              {active.map((a) => (
                <tr key={a.id} className={`alarm-row ${a.state} p${a.priority}`}>
                  <td><span className={`prio p${a.priority}`}>{a.priority}</span></td>
                  <td>{a.id}</td>
                  <td>{STATE_LABEL[a.state] ?? a.state}</td>
                  <td className="num">{a.value?.toFixed(3) ?? "—"}</td>
                  <td className="num">{a.limit} {a.unit}</td>
                  {!compact && <td className="dim">{a.description}</td>}
                  <td>
                    {a.state.endsWith("UNACK") && (
                      <button className="btn xs" onClick={() => send({ type: "action", action: { acknowledge_alarm_ids: [a.id] } })}>Ack</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!compact && (
        <>
          <div className="card-title" style={{ marginTop: 14 }}>Event history</div>
          <div className="table-wrap history">
            <table className="alarms">
              <thead><tr><th>Time</th><th>P</th><th>Alarm</th><th>Transition</th><th>PV</th><th>Description</th></tr></thead>
              <tbody>
                {snap.alarm_history.map((e, i) => (
                  <tr key={i}>
                    <td className="num">{e.time_min.toFixed(1)}</td>
                    <td>{e.priority}</td><td>{e.id}</td><td>{e.transition}</td>
                    <td className="num">{e.value?.toFixed(3) ?? "—"}</td>
                    <td className="dim">{e.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
