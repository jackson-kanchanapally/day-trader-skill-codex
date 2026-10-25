import React, { useCallback, useEffect, useRef, useState } from "react";

const active = (run) => run && !["completed", "failed", "interrupted"].includes(run.status);
async function read(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const doc = await response.json();
  if (!response.ok) throw new Error(doc.error || "Run request failed");
  return doc;
}

export function useCodexRunner() {
  const [connection, setConnection] = useState(null), [run, setRun] = useState(null);
  const [open, setOpen] = useState(false), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const selected = useRef(""), locked = useRef(false);
  const refresh = useCallback(async () => {
    const state = await read("/api/runner");
    setConnection(state);
    if (!selected.current) selected.current = state.active_id || state.runs?.[0]?.id || "";
    if (selected.current) {
      const id = selected.current;
      const detail = await read("/api/runs/" + encodeURIComponent(id));
      if (selected.current === id) setRun(detail);
    }
  }, []);
  useEffect(() => {
    let disposed = false, timer;
    const poll = async () => {
      try { await refresh(); } catch (e) { if (!disposed) setError(e.message); }
      if (!disposed) timer = setTimeout(poll, 1500);
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [refresh]);
  const post = async (path, body) => {
    if (locked.current) return false;
    locked.current = true; setBusy(true); setError("");
    try {
      const state = await read("/api/runner"); setConnection(state);
      const next = await read(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Trader-Token": state.token || "" }, body: JSON.stringify(body) });
      selected.current = next.id; setRun(next); setOpen(true); await refresh(); return true;
    } catch (e) { setError(e.message); setOpen(true); return false; }
    finally { setBusy(false); locked.current = false; }
  };
  return {
    connection, run, open, setOpen, error, busy,
    start: (prompt) => post("/api/runs", { prompt }),
    reply: (text) => post(`/api/runs/${run.id}/reply`, { text }),
    answer: (request_id, answer) => post(`/api/runs/${run.id}/answer`, { request_id, answer }),
    stop: () => post(`/api/runs/${run.id}/stop`, {}),
    select: async (id) => { selected.current = id; setRun(null); try { await refresh(); } catch (e) { setError(e.message); } },
  };
}

function PendingRequest({ request, runner }) {
  const [answers, setAnswers] = useState({});
  const questions = request.params.questions, approval = request.method.endsWith("requestApproval");
  const choices = request.params.availableDecisions || ["accept", "decline", "cancel"];
  return <section className="run-question">
    <b>{approval ? "Tool permission requested" : "Codex needs your input"}</b>
    {approval ? <>
      {request.params.reason && <p>{request.params.reason}</p>}
      <details open><summary>Requested action</summary><pre>{JSON.stringify({ ...request.params, item: request.item }, null, 2)}</pre></details>
      <div className="run-actions">{[["accept", "Allow once"], ["decline", "Decline"], ["cancel", "Cancel"]].filter(([v]) => choices.includes(v)).map(([value, label]) => <button key={value} disabled={runner.busy} onClick={() => runner.answer(request.id, value)}>{label}</button>)}</div>
    </> : <form onSubmit={async (e) => { e.preventDefault(); await runner.answer(request.id, answers); }}>
      {questions?.map((q) => <label key={q.id}>{q.question}
        {q.options?.length > 0 && <select aria-label={`Options for ${q.header || q.question}`} value={q.options.some((o) => o.label === answers[q.id]) ? answers[q.id] : ""} onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })}><option value="">Choose an option or type below</option>{q.options.map((o) => <option key={o.label} value={o.label}>{o.label} — {o.description}</option>)}</select>}
        <textarea required value={answers[q.id] || ""} onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })} placeholder="Your answer" />
      </label>)}
      <button type="submit" disabled={runner.busy || !questions?.every((q) => answers[q.id]?.trim())}>Send answers</button>
    </form>}
  </section>;
}

export default function RunPanel({ runner }) {
  const [reply, setReply] = useState("");
  const { run, connection, open } = runner;
  if (!open) return null;
  const live = active(run);
  return <aside className="codex-run-panel" aria-label="Codex request" role="region">
    <div className="run-head"><div><span className="eyebrow">CODEX</span><h2>{run ? "Request progress" : "Run from Trade Desk"}</h2></div><button className="icon-button" aria-label="Hide request progress" onClick={() => runner.setOpen(false)}>×</button></div>
    {connection?.runs?.length > 0 && <label className="run-select">Request<select value={run?.id || ""} onChange={(e) => runner.select(e.target.value)}><option value="" disabled>Select a request</option>{connection.runs.map((r) => <option key={r.id} value={r.id}>{r.status} · {r.title}</option>)}</select></label>}
    <div className="run-scroll">
      {runner.error && <p className="notice error" role="alert">{runner.error}</p>}
      {connection?.error && <p className="notice">{connection.error}</p>}
      {!run && <p>Use New request to research a stock or run a scan. Click Run in Codex to start.</p>}
      {run && <>
        <div className="run-state" role="status"><b>{run.status.replaceAll("_", " ")}</b>{live && <button disabled={runner.busy || run.status === "stopping"} onClick={runner.stop}>Stop request</button>}</div>
        {run.error && <p className="notice error">{run.error}</p>}
        {run.messages.map((m, i) => <article className={`run-message ${m.role}`} key={m.id}>{m.role === "user" && i === 0 ? <details><summary>Original request</summary><pre>{m.text}</pre></details> : <><b>{m.role === "user" ? "You" : "Codex"}</b><div>{m.text || "Working…"}</div></>}</article>)}
        {run.activity.length > 0 && <details className="run-activity"><summary>Recent activity · {run.activity.at(-1).label}</summary><ul>{run.activity.slice(-20).map((event, i) => <li key={i}>{event.label}</li>)}</ul></details>}
        {run.pending.map((request) => <PendingRequest key={`${run.id}-${request.id}`} request={request} runner={runner} />)}
        {!live && run.thread_id && <form className="run-reply" onSubmit={async (e) => { e.preventDefault(); if (await runner.reply(reply)) setReply(""); }}><label>Continue this request<textarea value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Answer account questions or add instructions…" required /></label><button disabled={runner.busy || Boolean(connection?.active_id) || !reply.trim()} type="submit">Send to Codex</button></form>}
        <p className="micro">Research and paper proposals only. Saved research appears in Individual research. Observatory shows specialists when actual specialists run.</p>
      </>}
    </div>
  </aside>;
}
