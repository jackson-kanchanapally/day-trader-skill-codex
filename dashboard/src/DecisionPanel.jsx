import React from "react";

const num = (value) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)
    : "—";
const money = (value) =>
  Number.isFinite(value) ? "₹" + num(value) : "Unavailable";
const percent = (value) =>
  Number.isFinite(value) ? num(value) + "%" : "Unavailable";
const when = (value) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value)) + " IST"
    : "—";
const age = (seconds) =>
  seconds < 3600
    ? Math.floor(seconds / 60) + "m"
    : seconds < 86400
      ? (seconds / 3600).toFixed(1) + "h"
      : (seconds / 86400).toFixed(1) + "d";
export function Cautions({ flags, compact = false }) {
  if (!flags?.length)
    return compact ? (
      <span className="micro">Execution checks pending</span>
    ) : (
      <p className="micro">
        No threshold cautions from the available daily indicators. Execution and
        risk checks still apply.
      </p>
    );
  return (
    <div className={compact ? "caution-tags" : "caution-list"}>
      {flags.map((flag) =>
        compact ? (
          <span key={flag.code} title={flag.evidence}>
            {flag.label}
          </span>
        ) : (
          <article key={flag.code}>
            <span>!</span>
            <div>
              <b>{flag.label}</b>
              <p>{flag.evidence}</p>
            </div>
          </article>
        ),
      )}
    </div>
  );
}
export default function DecisionPanel({ detail, onResearch, onPrepare }) {
  const d = detail.decision,
    q = d.quote,
    m = detail.metrics || {};
  return (
    <section className="decision-workspace">
      <div
        className="decision-stages"
        aria-label="Separate setup, tradeability and risk states"
      >
        <article>
          <span>01 · DAILY SETUP</span>
          <strong>
            {detail.score.toFixed(1)}
            <small>/100 evidence score</small>
          </strong>
          <p>Matched daily rules</p>
        </article>
        <article>
          <span>02 · TRADEABILITY NOW</span>
          <strong className="warning">
            {d.tradeability.status === "BLOCKED" ? "Blocked" : "Incomplete"}
          </strong>
          <p>{d.market.label}</p>
        </article>
        <article>
          <span>03 · INDEPENDENT RISK</span>
          <strong>Not cleared</strong>
          <p>Current exact-order check required</p>
        </article>
      </div>
      <div className="decision-data-line">
        <b className={d.data.status === "CURRENT_DAILY" ? "" : "warning"}>
          {d.data.status === "CURRENT_DAILY"
            ? "Latest completed daily session"
            : d.data.status === "STALE"
              ? "Stale daily evidence"
              : d.data.status.replaceAll("_", " ")}
        </b>
        <span>
          Expected session {d.data.expected_session || "Calendar unavailable"}
        </span>
        <span>
          {d.data.status === "FUTURE"
            ? "Future-dated cutoff"
            : `${age(d.data.scan_age_seconds)} since scan cutoff`}
        </span>
        <span>Checked {when(d.data.observed_at)}</span>
        <span>Saved data · intraday signal required</span>
      </div>
      <div className="decision-columns">
        <div>
          <h3>Before this can become a paper trade</h3>
          <ul className="decision-blockers">
            {d.tradeability.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <Cautions flags={detail.flags} />
          <p className="micro decision-threshold-note">
            Cautions use disclosed research thresholds. A price jump alone does
            not verify a circuit lock or trading halt.
          </p>
        </div>
        <aside className="decision-review-links">
          <span className="eyebrow">INDIVIDUAL RESEARCH</span>
          {d.research.length ? (
            d.research.map((record) => (
              <button key={record.id} onClick={() => onResearch(record.id)}>
                <b>{record.label} ↗</b>
                <span>{when(record.as_of)}</span>
                <small>
                  {record.same_scan_cutoff
                    ? "Same saved daily source"
                    : "Separate saved review"}
                </small>
              </button>
            ))
          ) : (
            <p>
              No saved review for this symbol yet. Preparing a request keeps the
              symbol and source cutoff attached.
            </p>
          )}
          <button
            className="primary"
            onClick={() =>
              onPrepare({
                symbol: detail.symbol,
                as_of: detail.scan_cutoff,
                origin_cutoff: detail.scan_cutoff,
              })
            }
          >
            Prepare review request ↗
          </button>
          <small>
            The request asks for your limits and dated evidence before sizing.
          </small>
        </aside>
      </div>
      <details className="execution-evidence">
        <summary>
          Execution evidence and volatility{" "}
          <span>
            {q.status === "CAPTURED"
              ? "Captured quote available"
              : q.status === "STALE"
                ? "Stale quote · execution evidence not current"
                : "Quote evidence unavailable"}{" "}
            ↓
          </span>
        </summary>
        <div className="execution-metrics">
          {[
            ["Daily close change", percent(m.change_pct)],
            ["Opening gap", percent(m.gap_pct)],
            ["Session range", percent(m.day_range_pct)],
            ["Wilder ATR14", money(m.atr14)],
            ["Bid", money(q.bid)],
            ["Ask", money(q.ask)],
            [
              "Quoted spread",
              Number.isFinite(q.spread_bps)
                ? num(q.spread_bps) + " bps"
                : "Unavailable",
            ],
            ["Upper price band", money(q.upper_price_band)],
            ["Lower price band", money(q.lower_price_band)],
            [
              "Halt status",
              typeof q.halted === "boolean"
                ? q.halted
                  ? "Reported halt"
                  : "No halt reported"
                : "Unverified",
            ],
            ["Instrument type", q.instrument_type || "Unverified"],
          ].map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <b>{value}</b>
            </div>
          ))}
        </div>
        <p>
          {q.reason}{" "}
          {q.observed_at
            ? `Quote capture: ${when(q.observed_at)}. Status: ${q.status}.`
            : ""}
        </p>
        {q.source && (
          <a href={q.source} target="_blank" rel="noreferrer">
            Open supplied execution snapshot source ↗
          </a>
        )}
        <p>
          Daily turnover estimates do not verify a current spread, book depth or
          fill. Corporate actions and adjustment basis require source
          reconciliation.
        </p>
      </details>
    </section>
  );
}
