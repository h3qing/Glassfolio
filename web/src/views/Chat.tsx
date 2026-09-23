import { useEffect, useRef, useState } from "react";
import { api, type ChatTurn } from "../api";
import { Icon } from "../icons";

type Message =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; turn?: ChatTurn; confirmed?: Record<string, "done" | "dismissed"> };

const STARTERS = [
  "How much NVDA do I really own?",
  "Why did my portfolio change this month?",
  "What is my portfolio worth after taxes?",
  "Do I have any open questions?",
];

/** Wrap numbers so privacy mode can frost them. */
function WithAmounts({ text }: { text: string }) {
  const parts = text.split(/(\$?-?\d[\d,]*(?:\.\d+)?%?)/g);
  return <>{parts.map((p, i) => (i % 2 ? <span key={i} className="amount">{p}</span> : p))}</>;
}

function AssistantMessage({ m, onConfirm, onDismiss, onImport }: {
  m: Extract<Message, { role: "assistant" }>; onConfirm: (id: string) => void; onDismiss: (id: string) => void;
  onImport: () => void;
}) {
  const t = m.turn;
  return (
    <div className="msg assistant">
      <p className="msg-text"><WithAmounts text={m.content} /></p>
      {t && t.ungrounded.length > 0 && (
        <p className="status warn msg-note">! Contains numbers not found in your data: <span className="amount">{t.ungrounded.join(", ")}</span>. Don't rely on them.</p>
      )}
      {t?.false_claim && (
        <p className="status warn msg-note">! Nothing was actually changed. Answer the question under Questions, or ask again.</p>
      )}
      {t?.proposals.map((p) => (
        <div key={p.action_id} className="proposal">
          <p><WithAmounts text={p.summary} /></p>
          {m.confirmed?.[p.action_id] ? <p className="muted">{m.confirmed[p.action_id] === "done" ? "✓ Done" : "Dismissed"}</p> : (
            <div className="row"><button className="btn primary" onClick={() => onConfirm(p.action_id)}>Confirm</button>
              <button className="btn" onClick={() => onDismiss(p.action_id)}>Dismiss</button></div>
          )}
        </div>
      ))}
      {t?.cards.filter((c) => c.card === "file_request").map((c, i) => (
        <div key={i} className="proposal"><p>Export {c.what}{c.where ? ` (${c.where})` : ""}, then drop it on the Import page.</p>
          <button className="btn" onClick={onImport}>Open Import</button></div>
      ))}
      {t && t.steps.length > 0 && (
        <p className="faint msg-note">Looked up: {t.steps.map((s) => s.ok ? s.tool.replace(/_/g, " ") : `${s.tool} (failed)`).join(", ")}</p>
      )}
    </div>
  );
}

export default function ChatPanel({ onClose, onChanged, onImport }: { onClose: () => void; onChanged: () => void; onImport: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { api.models().then((m) => setModel(m.name)).catch(() => undefined); }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, busy]);

  const send = (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((ms) => [...ms, { role: "user", content: message }]);
    setDraft(""); setBusy(true);
    api.chat(message, history)
      .then((turn) => setMessages((ms) => [...ms, { role: "assistant", content: turn.reply, turn }]))
      .catch((e) => setMessages((ms) => [...ms, { role: "assistant", content: `Something went wrong: ${e.message}` }]))
      .finally(() => setBusy(false));
  };
  const mark = (id: string, state: "done" | "dismissed") => setMessages((ms) => ms.map((m) =>
    m.role === "assistant" && m.turn?.proposals.some((p) => p.action_id === id)
      ? { ...m, confirmed: { ...(m.confirmed ?? {}), [id]: state } } : m));
  const confirm = (id: string) => api.confirmAction(id).then(() => { mark(id, "done"); onChanged(); })
    .catch((e) => setMessages((ms) => [...ms, { role: "assistant", content: e.message }]));

  return (
    <aside className="chat glass" aria-label="Assistant">
      <header className="chat-head">
        <Icon name="sparkle" />
        <div><strong>Assistant</strong><div className="faint" style={{ fontSize: 12 }}>{model ? `${model}, on this Mac` : "No local model chosen"}</div></div>
        <button className="icon-btn" style={{ marginLeft: "auto" }} onClick={onClose} aria-label="Close assistant">✕</button>
      </header>
      <div className="chat-body" aria-live="polite">
        {messages.length === 0 && (
          <div className="starters">
            <p className="muted">Ask about your portfolio. Answers come from your data; the assistant can't change anything without your confirmation.</p>
            {STARTERS.map((s) => <button key={s} className="btn" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {messages.map((m, i) => m.role === "user"
          ? <div key={i} className="msg user"><p className="msg-text"><WithAmounts text={m.content} /></p></div>
          : <AssistantMessage key={i} m={m} onConfirm={confirm} onDismiss={(id) => mark(id, "dismissed")} onImport={onImport} />)}
        {busy && <div className="msg assistant"><p className="msg-text faint">Thinking…</p></div>}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
        <textarea rows={2} value={draft} placeholder="Ask anything about your portfolio" aria-label="Message"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(draft); } }} />
        <button className="btn primary" disabled={busy || !draft.trim()} aria-label="Send">Send</button>
      </form>
    </aside>
  );
}
