import React, { useEffect, useMemo, useRef, useState } from "react";
import "./opportunities.css";
import DecisionPanel, { Cautions } from "./DecisionPanel";

const labels = {
  breakout_20d: "20-day breakout",
  bull_flag: "Bull flag",
  reclaim_ma20: "MA20 reclaim",
  oversold_bounce: "Oversold bounce",
  oversold: "Oversold",
};
const numeric = (n) =>
  Number.isFinite(n)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(n)
    : "—";
const money = (n) => (Number.isFinite(n) ? `₹${numeric(n)}` : "—");
const multiple = (n) => (Number.isFinite(n) ? `${n.toFixed(2)}×` : "—");
const shortMoney = (n) =>
  !Number.isFinite(n)
    ? "—"
    : n >= 1e7
      ? `₹${numeric(n / 1e7)} Cr`
      : n >= 1e5
        ? `₹${numeric(n / 1e5)} L`
        : money(n);
const day = (value) =>
  value && Number.isFinite(new Date(value).getTime())
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        year: "numeric",
      }).format(new Date(value))
    : "—";
const clock = (value) =>
  value && Number.isFinite(new Date(value).getTime())
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(new Date(value)) + " IST"
    : "—";
const signed = (n) =>
  Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${n.toFixed(2)}%` : "—";
async function get(path, signal) {
  const response = await fetch(path, { cache: "no-store", signal });
  const doc = await response.json();
  if (!response.ok)
    throw new Error(doc.error || "Unable to read the saved scan.");
  return doc;
}
function Tags({ tags }) {
  return (
    <div className="op-tags">
      {tags.map((tag) => (
        <span className={`op-tag op-tag-${tag}`} key={tag}>
          {labels[tag] || tag}
        </span>
      ))}
    </div>
  );
}

function DailyChart({ candles, high20 }) {
  const [hover, setHover] = useState(null);
  if (!candles.length)
    return (
      <div className="op-chart-empty">
        <span>⌁</span>
        <p>No matching saved candle history is available for this scan.</p>
        <small>
          The stored indicators and setup evidence are available below.
        </small>
      </div>
    );
  const width = 860,
    height = 245,
    left = 12,
    right = 74,
    top = 20,
    bottom = 28,
    plotWidth = width - left - right;
  const average = candles.map((_, i) =>
    i < 19
      ? null
      : candles.slice(i - 19, i + 1).reduce((total, c) => total + c.close, 0) /
        20,
  );
  const prices = candles.map((c) => c.close),
    lo = Math.min(...prices),
    hi = Math.max(...prices),
    padding = (hi - lo) * 0.12 || 1,
    min = lo - padding,
    max = hi + padding;
  const x = (i) => left + (i * plotWidth) / Math.max(1, candles.length - 1),
    y = (v) => top + ((max - v) * (height - top - bottom)) / (max - min);
  const line = prices.map((value, i) => `${x(i)},${y(value)}`).join(" ");
  const ma = average
    .map((value, i) => (value === null ? null : `${x(i)},${y(value)}`))
    .filter(Boolean)
    .join(" ");
  const index =
      hover === null ? candles.length - 1 : Math.min(hover, candles.length - 1),
    selected = candles[index];
  const previous = candles[index - 1],
    change = previous ? (selected.close / previous.close - 1) * 100 : null;
  const maxVolume = Math.max(1, ...candles.map((c) => c.volume));
  return (
    <>
      <div className="op-chart-readout">
        <div>
          <span className="eyebrow">{day(selected.time)} · DAILY CLOSE</span>
          <strong>
            {money(selected.close)}{" "}
            <small className={change >= 0 ? "positive" : "op-negative"}>
              {signed(change)}
            </small>
          </strong>
        </div>
        <span className="micro">Volume {numeric(selected.volume)}</span>
      </div>
      <svg
        className="op-chart"
        viewBox={`0 0 ${width} ${height + 65}`}
        role="img"
        aria-label={`${candles.length} saved daily closing prices, 20-session average and volume`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const point = ((event.clientX - rect.left) / rect.width) * width;
          setHover(
            Math.max(
              0,
              Math.min(
                candles.length - 1,
                Math.round(((point - left) / plotWidth) * (candles.length - 1)),
              ),
            ),
          );
        }}
      >
        <defs>
          <linearGradient id="op-chart-fill" x1="0" y1="0" x2="0" y2="1">
            <stop stopColor="#aee9c3" stopOpacity=".17" />
            <stop offset="1" stopColor="#aee9c3" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line
              x1={left}
              y1={top + f * (height - top - bottom)}
              x2={width - right + 6}
              y2={top + f * (height - top - bottom)}
              className="op-gridline"
            />
            <text
              x={width - right + 17}
              y={top + f * (height - top - bottom) + 4}
              className="op-axis"
            >
              {numeric(max - f * (max - min))}
            </text>
          </g>
        ))}
        <polygon
          points={`${left},${height - bottom} ${line} ${x(candles.length - 1)},${height - bottom}`}
          fill="url(#op-chart-fill)"
        />
        <polyline points={ma} className="op-ma-line" />
        <polyline points={line} className="op-price-line" />
        {Number.isFinite(high20) && high20 >= min && high20 <= max && (
          <g>
            <line
              x1={left}
              y1={y(high20)}
              x2={width - right}
              y2={y(high20)}
              className="op-high-line"
            />
            <text x={left + 8} y={y(high20) - 7} className="op-axis">
              Prior 20-session high
            </text>
          </g>
        )}
        {candles.map((c, i) => (
          <rect
            key={c.time}
            x={x(i) - 2}
            y={height + 32 - (c.volume / maxVolume) * 35}
            width={Math.max(2, (plotWidth / candles.length) * 0.55)}
            height={(c.volume / maxVolume) * 35}
            fill={i === index ? "#34d399" : "#64748b"}
          />
        ))}
        <line
          x1={x(index)}
          y1={top}
          x2={x(index)}
          y2={height + 32}
          className="op-crosshair"
        />
        <circle
          cx={x(index)}
          cy={y(selected.close)}
          r="4"
          fill="#34d399"
          stroke="#11151c"
          strokeWidth="2"
        />
        {[0, Math.floor(candles.length / 2), candles.length - 1].map((i) => (
          <text
            key={i}
            x={x(i)}
            y={height + 58}
            textAnchor={
              i === 0 ? "start" : i === candles.length - 1 ? "end" : "middle"
            }
            className="op-axis"
          >
            {day(candles[i].time)}
          </text>
        ))}
      </svg>
      <div className="op-chart-legend">
        <span>
          <i />
          Daily close
        </span>
        <span>
          <i className="op-ma-dot" />
          20-session average
        </span>
        <span>Saved snapshot · {candles.length} sessions</span>
      </div>
    </>
  );
}

function OpportunityDetail({ selected, scan, onClose, onPrepare, onResearch }) {
  const dialog = useRef(null),
    [detail, setDetail] = useState(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    setError("");
    const controller = new AbortController();
    if (!dialog.current.open) dialog.current.showModal();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        setDetail(
          await get(
            `/api/opportunities/${encodeURIComponent(selected.symbol)}?scan=${encodeURIComponent(scan.scan_id)}`,
            controller.signal,
          ),
        );
        setError("");
      } catch (e) {
        if (e.name !== "AbortError") setError(e.message);
      } finally {
        busy = false;
      }
    }
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [selected?.symbol, scan?.scan_id]);
  const item = detail || selected;
  function prepare(seed) {
    dialog.current.close();
    onPrepare(seed);
  }
  function research(id) {
    dialog.current.close();
    onResearch(id);
  }
  return (
    <dialog
      ref={dialog}
      className="op-detail-dialog"
      aria-labelledby="op-detail-title"
      onClose={onClose}
    >
      <div className="op-detail-head">
        <div>
          <span className="eyebrow">
            DAILY SETUP ANALYSIS · #{item?.rank || "—"}
          </span>
          <h2 id="op-detail-title">{item?.name || "Company analysis"}</h2>
          <p>
            {item?.symbol} <span>·</span> {day(item?.as_of)} candle
          </p>
        </div>
        <button
          className="icon-button"
          aria-label="Close company analysis"
          onClick={() => dialog.current.close()}
          autoFocus
        >
          ×
        </button>
      </div>
      {error ? (
        <p className="notice error" role="alert">
          {error}
        </p>
      ) : !detail ? (
        <div className="op-detail-loading" role="status">
          Reading saved setup evidence…
        </div>
      ) : (
        <>
          <DecisionPanel
            detail={detail}
            onResearch={research}
            onPrepare={prepare}
          />
          <div className="op-detail-grid">
            <div className="op-detail-main">
              <section className="op-detail-chart">
                <DailyChart
                  key={detail.symbol}
                  candles={detail.candles}
                  high20={detail.high20}
                />
              </section>
              <div className="op-metric-grid">
                {[
                  [
                    "RSI 14",
                    numeric(detail.rsi),
                    `Previous ${numeric(detail.rsi_prev)}`,
                  ],
                  [
                    "Relative volume",
                    multiple(detail.volume_x),
                    "Latest / 20-session average",
                  ],
                  [
                    "MA20",
                    money(detail.ma20),
                    `${signed(Number.isFinite(detail.ma20) && detail.ma20 > 0 ? (detail.price / detail.ma20 - 1) * 100 : null)} from average`,
                  ],
                  [
                    "20-session closing high",
                    money(detail.high20),
                    "Before the latest candle",
                  ],
                  [
                    "Pre-flag rise",
                    Number.isFinite(detail.run_pct)
                      ? detail.run_pct.toFixed(2) + "%"
                      : "—",
                    "10 sessions before the latest five",
                  ],
                  [
                    "Five-close range",
                    Number.isFinite(detail.range5_pct)
                      ? detail.range5_pct.toFixed(2) + "%"
                      : "—",
                    "Range as % of latest close",
                  ],
                ].map(([label, value, note]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    <small>{note}</small>
                  </div>
                ))}
              </div>
              <section className="op-detail-section">
                <div className="op-section-heading">
                  <span className="eyebrow">WHY IT MATCHED</span>
                  <h3>Evidence behind the setup.</h3>
                </div>
                <div className="op-evidence-cards">
                  {detail.evidence.map((e, i) => (
                    <article key={e.key}>
                      <span className="op-evidence-number">0{i + 1}</span>
                      <div>
                        <Tags tags={[e.key]} />
                        <p>{e.explanation}</p>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
              <details className="op-recent">
                <summary>
                  Recent daily candles <span>OHLCV ↗</span>
                </summary>
                <div className="op-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Session</th>
                        <th>Open</th>
                        <th>High</th>
                        <th>Low</th>
                        <th>Close</th>
                        <th>Volume</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.candles
                        .slice(-10)
                        .reverse()
                        .map((c) => (
                          <tr key={c.time}>
                            <td>{day(c.time)}</td>
                            {["open", "high", "low", "close", "volume"].map(
                              (k) => (
                                <td key={k}>{numeric(c[k])}</td>
                              ),
                            )}
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                <p className="micro">{detail.history_note}</p>
              </details>
            </div>
            <aside className="op-analysis-sidebar">
              <section className="op-score-card">
                <span className="eyebrow">SETUP STRENGTH</span>
                <div className="op-big-score">
                  {detail.score.toFixed(1)}
                  <span>/ 100</span>
                </div>
                <p>
                  A rule-based evidence score.
                  <br />
                  Not a probability of profit.
                </p>
                <div className="op-score-parts">
                  {detail.ranking.components.map((part) => (
                    <div key={part.label}>
                      <div>
                        <span>{part.label}</span>
                        <b>
                          {part.points}
                          <small> / {part.maximum}</small>
                        </b>
                      </div>
                      <div className="op-score-track">
                        <i
                          style={{
                            width: `${(part.points / part.maximum) * 100}%`,
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
                {detail.ranking.freshness_multiplier < 1 && (
                  <p className="op-stale">
                    Older candles: total score reduced by 50%.
                  </p>
                )}
                <small>
                  Approx. daily turnover {shortMoney(detail.turnover)}
                </small>
              </section>
              <section className="op-next-checks">
                <span className="eyebrow">BEFORE A PAPER TRADE</span>
                <h3>Check the prerequisites.</h3>
                <p>
                  These checks apply to a current entry. Saved findings remain
                  visible in Individual research.
                </p>
                <ul>
                  {detail.checks.map((check) => (
                    <li key={check.label} title={check.reason}>
                      <span>◌ {check.label}</span>
                      <small>{check.status.replaceAll("_", " ")}</small>
                    </li>
                  ))}
                </ul>
                <button
                  className="primary"
                  onClick={() =>
                    prepare({
                      symbol: detail.symbol,
                      as_of: detail.scan_cutoff,
                      origin_cutoff: detail.scan_cutoff,
                    })
                  }
                >
                  Prepare review request ↗
                </button>
              </section>
              <section className="op-source-card">
                <span className="eyebrow">SOURCE & CUTOFF</span>
                <p>{detail.source}</p>
                <dl>
                  <div>
                    <dt>Candle session</dt>
                    <dd>{day(detail.as_of)}</dd>
                  </div>
                  <div>
                    <dt>Scan cutoff</dt>
                    <dd>
                      {day(detail.scan_cutoff)}
                      <br />
                      {clock(detail.scan_cutoff)}
                    </dd>
                  </div>
                </dl>
                <details>
                  <summary>Provider attempts</summary>
                  {detail.provider_attempts.map((attempt, i) => (
                    <div className="op-provider" key={i}>
                      <b>{attempt.provider}</b>
                      <span>
                        {attempt.usable
                          ? "Snapshot used / usable"
                          : "Unavailable / incomplete"}
                      </span>
                      {attempt.error && <p>{attempt.error}</p>}
                    </div>
                  ))}
                </details>
              </section>
            </aside>
          </div>
        </>
      )}
    </dialog>
  );
}

export default function Opportunities({
  refreshToken,
  onCreateRequest,
  onCount,
  onPrepare,
  onResearch,
}) {
  const [scan, setScan] = useState(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [sort, setSort] = useState("score"),
    [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(50),
    [selected, setSelected] = useState(null),
    [received, setReceived] = useState(null);
  const [reviewFilter, setReviewFilter] = useState("all");
  useEffect(() => {
    let active = true,
      busy = false;
    const controller = new AbortController();
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const doc = await get("/api/opportunities", controller.signal);
        if (active) {
          setScan(doc);
          setError("");
          setReceived(new Date().toISOString());
          onCount?.(doc.matched);
        }
      } catch (e) {
        if (active && e.name !== "AbortError") setError(e.message);
      } finally {
        busy = false;
        if (active) setLoading(false);
      }
    }
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [refreshToken, onCount]);
  useEffect(
    () => setPage(1),
    [query, filter, sort, pageSize, scan?.scan_id, reviewFilter],
  );
  const rows = useMemo(() => {
    const value = query.trim().toLowerCase();
    const matches = (scan?.opportunities || []).filter(
      (row) =>
        (filter === "all" || row.setups.includes(filter)) &&
        (!value || `${row.name} ${row.symbol}`.toLowerCase().includes(value)) &&
        (reviewFilter === "all" ||
          (reviewFilter === "cautions" && row.flags?.length) ||
          (reviewFilter === "research" && row.research_count > 0) ||
          (reviewFilter === "stale" && row.data_status !== "CURRENT_DAILY")),
    );
    return matches.sort((a, b) =>
      sort === "volume"
        ? (b.volume_x ?? -1) - (a.volume_x ?? -1) || a.rank - b.rank
        : sort === "turnover"
          ? b.turnover - a.turnover || a.rank - b.rank
          : sort === "rsi"
            ? (a.rsi ?? Infinity) - (b.rsi ?? Infinity) || a.rank - b.rank
            : a.rank - b.rank,
    );
  }, [scan, query, filter, sort, reviewFilter]);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize)),
    current = Math.min(page, pages),
    visible = rows.slice((current - 1) * pageSize, current * pageSize);
  function download() {
    const cell = (value) => {
      let text = String(value ?? "");
      if (/^[=+@\-\t\r]/.test(text)) text = "'" + text;
      return '"' + text.replaceAll('"', '""') + '"';
    };
    const csv = [
      [
        "Rank",
        "Symbol",
        "Company / listing",
        "Setup score",
        "Setups",
        "Last close INR",
        "Relative volume",
        "RSI14",
        "Approx turnover INR",
        "Candle date",
        "Data status",
        "Tradeability",
        "Research cautions",
        "Saved research records",
      ],
      ...rows.map((r) => [
        r.rank,
        r.symbol,
        r.name,
        r.score,
        r.setups.map((tag) => labels[tag]).join("; "),
        r.price,
        r.volume_x,
        r.rsi,
        r.turnover,
        r.as_of.slice(0, 10),
        r.data_status,
        r.tradeability,
        (r.flags || []).map((f) => f.label).join("; "),
        r.research_count,
      ]),
    ]
      .map((row) => row.map(cell).join(","))
      .join("\r\n");
    const url = URL.createObjectURL(
        new Blob([csv], { type: "text/csv;charset=utf-8;" }),
      ),
      link = document.createElement("a");
    link.href = url;
    link.download = `nse-opportunities-${scan?.as_of?.slice(0, 10) || "scan"}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="opportunities" id="opportunities">
      <div className="op-page-title">
        <div>
          <span className="eyebrow">
            <i /> NSE / BSE · SAVED DAILY SCAN
          </span>
          <h1>
            Market opportunities<span>.</span>
          </h1>
          <p>
            Every match. The evidence to decide what deserves a closer look.
          </p>
        </div>
        <button className="op-secondary" onClick={onCreateRequest}>
          ＋ New scan request
        </button>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}{" "}
          {scan && `Showing the last received scan from ${clock(received)}.`}
        </p>
      )}
      {!scan?.scan_id ? (
        <section className="panel empty">
          <span className="empty-symbol">⌕</span>
          <h2>
            {loading
              ? "Reading your latest scan…"
              : "Start with a market scan."}
          </h2>
          <p>
            {loading
              ? "Loading saved scanner results."
              : "Run a daily scan request in Codex. Every matching symbol will appear here, ranked with its source and setup evidence."}
          </p>
          {!loading && (
            <button className="primary" onClick={onCreateRequest}>
              Create a scan request ↗
            </button>
          )}
        </section>
      ) : (
        <>
          <div className="op-summary-grid">
            {[
              [
                "Universe",
                numeric(scan.universe),
                "Symbols in the saved scan",
                "◇",
              ],
              [
                "Successfully scanned",
                numeric(scan.scanned),
                `${((scan.scanned / scan.universe) * 100).toFixed(1)}% data coverage`,
                "↗",
              ],
              [
                "Setup matches",
                numeric(scan.matched),
                "All matches included below",
                "⌁",
              ],
              [
                "Failed symbols",
                numeric(scan.failed),
                "Insufficient or unavailable data",
                "◌",
              ],
            ].map(([label, value, note, icon], i) => (
              <div className={`op-summary-card op-summary-${i}`} key={label}>
                <span>
                  {label}
                  <i>{icon}</i>
                </span>
                <strong>{value}</strong>
                <p>{note}</p>
              </div>
            ))}
          </div>
          <div className="op-scan-context">
            <span>
              <i /> Daily candles:{" "}
              <b>{(scan.data_dates || []).map(day).join(" · ")}</b>
            </span>
            <span>
              Cutoff {day(scan.as_of)} · {clock(scan.as_of)}
            </span>
            <span>Saved results · refresh every 10s</span>
          </div>
          <div className="market-readiness-banner">
            <span className="decision-pill caution">
              {scan.market?.label || "Session state unavailable"}
            </span>
            <div>
              <b>
                Daily research candidates · current entry and risk checks
                pending
              </b>
              <p>
                {scan.stale_count
                  ? `${scan.stale_count} matches need refreshed daily evidence. `
                  : "Daily evidence is checked against the latest completed session. "}
                Expected session{" "}
                {scan.market?.expected_session || "unavailable"}. Updating this
                page reads saved files; it does not fetch live quotes.
              </p>
            </div>
          </div>
          <details className="op-ranking">
            <summary>
              <span>How are opportunities ranked?</span>
              <span>
                Setup strength · highest first <b>＋</b>
              </span>
            </summary>
            <p>{scan.ranking.description}</p>
            <ol>
              {scan.ranking.rules.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ol>
            <small>
              The directory can include ETFs. Ranking organizes daily rule
              matches; it does not clear an entry or risk review.
            </small>
            <small>{scan.caution_note}</small>
          </details>
          <div className="op-list-heading">
            <div>
              <span className="eyebrow">THE OPPORTUNITY BOARD</span>
              <h2>
                {filter === "all"
                  ? "All matching companies & funds"
                  : labels[filter]}{" "}
                <span>{numeric(rows.length)}</span>
              </h2>
            </div>
            <button
              className="op-secondary"
              onClick={download}
              disabled={!rows.length}
            >
              ↓ Export{" "}
              {query || filter !== "all" || reviewFilter !== "all"
                ? "filtered "
                : "all "}
              matches
            </button>
          </div>
          <div className="op-filterbar">
            <label className="op-search">
              <span>⌕</span>
              <input
                aria-label="Search companies or symbols"
                placeholder="Search company or symbol…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {query && (
                <button
                  aria-label="Clear company search"
                  onClick={() => setQuery("")}
                >
                  ×
                </button>
              )}
            </label>
            <label className="op-sort">
              Review status
              <select
                aria-label="Filter review readiness"
                value={reviewFilter}
                onChange={(e) => setReviewFilter(e.target.value)}
              >
                <option value="all">All matches</option>
                <option value="cautions">With research cautions</option>
                <option value="research">With saved research</option>
                <option value="stale">Daily refresh required</option>
              </select>
            </label>
            <label className="op-sort">
              Sort by
              <select
                aria-label="Sort opportunities"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="score">Setup strength ↓</option>
                <option value="volume">Relative volume ↓</option>
                <option value="turnover">Approx. turnover ↓</option>
                <option value="rsi">RSI ↑ (lowest first)</option>
              </select>
            </label>
          </div>
          <div
            className="op-setup-filters"
            role="group"
            aria-label="Filter by setup"
          >
            <button
              className={filter === "all" ? "active" : ""}
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              All setups <span>{scan.matched}</span>
            </button>
            {Object.entries(labels).map(([key, label]) => (
              <button
                key={key}
                className={filter === key ? "active" : ""}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label} <span>{scan.setups[key] || 0}</span>
              </button>
            ))}
          </div>
          <section
            className="op-results panel"
            aria-label="Ranked scan opportunities"
          >
            <div className="op-table-scroll">
              <table className="op-table">
                <caption className="op-sr-only">
                  All saved matching symbols ranked by setup strength. Select a
                  company to inspect its saved technical analysis.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Score rank</th>
                    <th scope="col">Company / listing</th>
                    <th scope="col">Strength</th>
                    <th scope="col">Matched setups</th>
                    <th scope="col">Last close</th>
                    <th scope="col">Rel. volume</th>
                    <th scope="col" className="op-hide-medium">
                      RSI 14
                    </th>
                    <th scope="col" className="op-hide-medium">
                      Approx. turnover
                    </th>
                    <th scope="col">
                      <span className="op-sr-only">Analysis</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <tr key={row.symbol} onClick={() => setSelected(row)}>
                      <td className="op-rank-cell">
                        {String(row.rank).padStart(2, "0")}
                      </td>
                      <td className="op-company-cell">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelected(row);
                          }}
                          className="op-company-button"
                          aria-label={`Analyze ${row.name} (${row.symbol})`}
                        >
                          <span className="op-monogram">
                            {row.symbol.replace(/\.(NS|BO)$/, "").slice(0, 2)}
                          </span>
                          <span>
                            <b>{row.name}</b>
                            <small>
                              {row.symbol}{" "}
                              {row.freshness === "older" && (
                                <em>· Older data</em>
                              )}
                            </small>
                          </span>
                        </button>
                        <div className="op-row-status">
                          <span>
                            {row.tradeability === "BLOCKED"
                              ? "Entry blocked"
                              : "Review pending"}
                          </span>
                          {row.research_count > 0 && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onResearch(null, row.symbol);
                              }}
                            >
                              Research {row.research_count} ↗
                            </button>
                          )}
                        </div>
                        <Cautions
                          compact
                          flags={(row.flags || []).slice(0, 2)}
                        />
                      </td>
                      <td className="op-score-cell">
                        <b>{row.score.toFixed(1)}</b>
                        <span className="op-small-track">
                          <i style={{ width: `${row.score}%` }} />
                        </span>
                      </td>
                      <td className="op-setups-cell">
                        <Tags tags={row.setups} />
                      </td>
                      <td className="op-price-cell">{money(row.price)}</td>
                      <td
                        className={`op-volume-cell ${row.volume_x >= 1.5 ? "positive" : ""}`}
                      >
                        {multiple(row.volume_x)}
                      </td>
                      <td className="op-hide-medium">{numeric(row.rsi)}</td>
                      <td className="op-hide-medium">
                        {shortMoney(row.turnover)}
                      </td>
                      <td className="op-open-cell">
                        <button
                          aria-label={`View detailed analysis for ${row.symbol}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelected(row);
                          }}
                        >
                          <span>View analysis </span>↗
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!rows.length && (
              <div className="empty small">
                <h3>No matches for this search.</h3>
                <p>Try another company or setup filter.</p>
                <button
                  className="op-secondary"
                  onClick={() => {
                    setQuery("");
                    setFilter("all");
                    setReviewFilter("all");
                  }}
                >
                  Clear filters
                </button>
              </div>
            )}
            <div className="op-pagination">
              <p>
                Showing{" "}
                <b>
                  {rows.length ? (current - 1) * pageSize + 1 : 0}–
                  {Math.min(current * pageSize, rows.length)}
                </b>{" "}
                of <b>{numeric(rows.length)}</b> matches
              </p>
              <div>
                <label>
                  Rows
                  <select
                    aria-label="Rows per page"
                    value={pageSize}
                    onChange={(e) => setPageSize(Number(e.target.value))}
                  >
                    {[25, 50, 100, 200].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  aria-label="Previous opportunities page"
                  disabled={current <= 1}
                  onClick={() => setPage(current - 1)}
                >
                  ←
                </button>
                <span>
                  {current} / {pages}
                </span>
                <button
                  aria-label="Next opportunities page"
                  disabled={current >= pages}
                  onClick={() => setPage(current + 1)}
                >
                  →
                </button>
              </div>
            </div>
          </section>
          <details className="op-failures">
            <summary>
              <span>
                Coverage exceptions <b>{scan.failed}</b>
              </span>
              <span>View every failed symbol ↓</span>
            </summary>
            <p>
              {scan.failed} symbols could not be evaluated. Setup counts can
              overlap because one symbol may match multiple rules.
            </p>
            <div className="op-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {scan.failures.map((row) => (
                    <tr key={row.symbol}>
                      <td>{row.symbol}</td>
                      <td>{row.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <footer>
            <span>
              <i className="tiny-dot" /> SAVED RESEARCH · PAPER ONLY
            </span>
            <span>
              Latest received {clock(received)} · No live order execution
            </span>
          </footer>
        </>
      )}
      <OpportunityDetail
        selected={selected}
        scan={scan}
        onClose={() => setSelected(null)}
        onPrepare={onPrepare}
        onResearch={onResearch}
      />
    </section>
  );
}
