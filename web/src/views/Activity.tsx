import { useEffect, useState } from "react";
import { api, type Op } from "../api";

const WHO: Record<string, string> = { user: "You", scheduler: "Scheduled", model: "Assistant" };

export default function ActivityView() {
  const [ops, setOps] = useState<Op[] | null>(null);
  useEffect(() => { api.ops().then(setOps); }, []);
  if (!ops) return <section className="sheet"><p className="muted">Loading…</p></section>;
  return (
    <section className="sheet">
      <p className="muted" style={{ marginTop: 0 }}>
        Every change is recorded here with the snapshot before and after it. To undo one, run
        <code> glassfolio restore &lt;id&gt;</code> in the terminal.
      </p>
      <table>
        <thead><tr><th>When</th><th>Who</th><th>What</th><th className="num">Rows</th><th className="num">Snapshot</th><th>ID</th></tr></thead>
        <tbody>
          {ops.map((o) => (
            <tr key={o.op_id}>
              <td className="num" style={{ textAlign: "left" }}>{o.ts.slice(0, 16).replace("T", " ")}</td>
              <td>{WHO[o.actor] ?? o.actor}</td>
              <td>{o.description}</td>
              <td className="num">+{o.rows_inserted}{o.rows_deleted ? ` −${o.rows_deleted}` : ""}</td>
              <td className="num">{o.snapshot_before} → {o.snapshot_after ?? "?"}</td>
              <td className="faint"><code>{o.op_id}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
