import React, { useEffect, useState } from "react";
import AgentDesk from "./AgentDesk";

export default function AgentDialog({ dialog, agent, name, index, asOf }) {
  const [tab, setTab] = useState("report");
  useEffect(() => setTab("report"), [agent?.role]);
  const review = agent?.review;
  const sources = Array.isArray(review?.sources)
    ? review.sources.filter((source) => {
        try {
          return new URL(source.url).protocol === "https:";
        } catch {
          return false;
        }
      })
    : [];
  return (
    <dialog
      ref={dialog}
      className="agent-dialog"
      aria-labelledby="workspace-title"
    >
      <div className="modal-head">
        <div>
          <span className="eyebrow">SPECIALIST WORKSPACE</span>
          <h2 id="workspace-title">{name || "Agent workspace"}</h2>
        </div>
        <button
          className="icon-button"
          onClick={() => dialog.current.close()}
          aria-label="Close agent workspace"
        >
          ×
        </button>
      </div>
      {agent && (
        <div className="workspace-detail">
          <aside className="workspace-person">
            <AgentDesk
              phase={agent.phase}
              index={index}
              verdict={review?.verdict}
            />
            <strong>
              {agent.phase === "completed"
                ? "Report delivered"
                : agent.phase === "running"
                  ? "At work"
                  : ["failed", "invalid"].includes(agent.phase)
                    ? "Needs attention"
                    : "Resting · waiting for work"}
            </strong>
            <p>
              {agent.phase === "completed"
                ? "The review is finished. Its verdict can still block the trade."
                : agent.phase === "running"
                  ? "A real start event was recorded. This is not a live heartbeat."
                  : "Activity follows recorded session events, not a simulated schedule."}
            </p>
            <span className="eyebrow">NATIVE AGENT</span>
            <code>
              {typeof agent.agent_id === "string"
                ? agent.agent_id
                : "Not delegated"}
            </code>
            <span className="eyebrow">SNAPSHOT CUTOFF</span>
            <small>{asOf || "—"}</small>
          </aside>
          <div className="workspace-report">
            <div
              className="tabs"
              role="tablist"
              aria-label="Agent workspace details"
            >
              {[
                ["report", "Report"],
                ["evidence", "Engine evidence"],
                ["json", "Raw JSON"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  className={tab === id ? "active" : ""}
                  onClick={() => setTab(id)}
                >
                  {label}
                </button>
              ))}
            </div>
            {tab === "json" ? (
              <pre className="json-view">
                {JSON.stringify(
                  review || { phase: agent.phase, review: null },
                  null,
                  2,
                )}
              </pre>
            ) : tab === "evidence" ? (
              <>
                <p className="notice">
                  Prepared numerical evidence. This is separate from the
                  independent agent’s judgement.
                </p>
                <pre className="json-view">
                  {JSON.stringify(agent.artifact?.data || {}, null, 2)}
                </pre>
              </>
            ) : (
              <>
                <div className="workspace-verdict">
                  <span className="eyebrow">INDEPENDENT VERDICT</span>
                  <b
                    className={
                      review?.verdict === "BLOCK"
                        ? "verdict-block"
                        : review?.verdict === "PASS"
                          ? "verdict-pass"
                          : "verdict-other"
                    }
                  >
                    {typeof review?.verdict === "string"
                      ? review.verdict
                      : "PENDING"}
                  </b>
                </div>
                {agent.errors?.map((error) => (
                  <p className="notice error" key={error}>
                    {error}
                  </p>
                ))}
                <p className="review-summary">
                  {typeof review?.summary === "string"
                    ? review.summary
                    : "No independent report has been saved yet. You can leave this workspace open; it will update when the agent saves its review."}
                </p>
                {Array.isArray(review?.evidence) && (
                  <>
                    <h3>What this agent found</h3>
                    <ul className="evidence-list">
                      {review.evidence.map((item, i) => (
                        <li key={i}>
                          {typeof item === "string"
                            ? item
                            : JSON.stringify(item)}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {!!sources.length && (
                  <>
                    <h3>Original sources</h3>
                    <div className="source-list">
                      {sources.map((source, i) => (
                        <a
                          key={i}
                          href={source.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {new URL(source.url).hostname} ↗
                          <small>
                            Published:{" "}
                            {source.published_at ||
                              (source.published_date
                                ? `${source.published_date} (${source.publication_timezone}, date only)`
                                : "Unverified")}
                          </small>
                        </a>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </dialog>
  );
}
