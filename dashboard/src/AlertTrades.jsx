import React, { useEffect, useState } from "react";

const number = (value, digits = 2) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits }).format(
        value,
      )
    : "—";
const money = (value, digits = 2) =>
  Number.isFinite(value)
    ? (value < 0 ? "−₹" : "₹") + number(Math.abs(value), digits)
    : "—";
const signed = (value, suffix = "") =>
  Number.isFinite(value) ? (value > 0 ? "+" : "") + number(value) + suffix : "—";
const tone = (value) =>
  Number.isFinite(value) ? (value > 0 ? "positive" : value < 0 ? "op-negative" : "") : "";
const clock = (iso) => (iso ? iso.slice(11, 16) : "—");
const day = (iso) =>
  new Intl.DateTimeFormat("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(new Date(iso + "T12:00:00+05:30"));
const STATUS = {
  CLOSED: "Closed",
  OPEN: "Open",
  WAITING: "Waiting",
  NOT_TRIGGERED: "Not triggered",
  SKIPPED: "Skipped",
  NO_DATA: "No data",
  ERROR: "Error",
};
const short = (symbol) => symbol.replace(/\.(NS|BO)$/, "");
const TAG = (code) => code.toLowerCase().replace(/_/g, " ");
const GOOD_TAGS = new Set([
  "FOLLOW_THROUGH",
  "PARTIAL_BREAKEVEN",
  "PARTIAL_SQUARE_OFF",
  "DRIFT_WIN",
  "MARKET_TAILWIND",
  "GAP_PAST_NO_CHASE",
  "CHASE_SKIP",
]);

function Lessons({ lessons }) {
  if (!lessons) return null;
  const notable = lessons.patterns.filter((p) => p.notable);
  return (
    <section className="panel journal-table alert-lessons">
      <div className="panel-head">
        <h2>Lessons learned</h2>
        <span className="micro">
          {lessons.enabled ? "APPLIED TO NEW ALERTS" : "LEARNING OFF"} ·{" "}
          {lessons.trades} CLOSED TRADES
        </span>
      </div>
      <p className="alert-lesson-intro">
        Each closed paper trade is grouped by how its setup looked at alert
        time. A trait becomes an active lesson after {lessons.min_trades}{" "}
        trades if it averages −0.25R or worse and is clearly worse than the
        rest. Active lessons take {lessons.penalty} points off future setups
        with that trait.
      </p>
      {lessons.active.length > 0 && (
        <ul className="alert-lesson-active">
          {lessons.active.map((r) => (
            <li key={r.trait}>
              <b>−{r.penalty}</b> {r.evidence}
            </li>
          ))}
        </ul>
      )}
      {lessons.traits.length > 0 ? (
        <div className="op-table-scroll">
          <table>
            <thead>
              <tr>
                <th>Setup trait</th>
                <th>Trades</th>
                <th>Win rate</th>
                <th>Avg R</th>
                <th>Others avg R</th>
                <th>Net P&amp;L</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {lessons.traits.map((r) => (
                <tr key={r.trait}>
                  <td>{r.label}</td>
                  <td>
                    {r.trades}
                    {r.status === "collecting" && (
                      <small>needs {lessons.min_trades}</small>
                    )}
                  </td>
                  <td>{number(r.win_rate, 0)}%</td>
                  <td className={tone(r.avg_r)}>{signed(r.avg_r, "R")}</td>
                  <td className={tone(r.others_avg_r)}>{signed(r.others_avg_r, "R")}</td>
                  <td className={tone(r.net_pnl)}>{money(r.net_pnl, 0)}</td>
                  <td>
                    <span className={`alert-status s-lesson-${r.status}`}>
                      {r.status === "active"
                        ? `Lesson · −${r.penalty}`
                        : r.status === "collecting"
                          ? "Collecting"
                          : "No issue"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>Lessons start once alerted trades close.</p>
      )}
      {lessons.patterns.length > 0 && (
        <div className="alert-patterns">
          <h3>What went wrong in losing trades</h3>
          <ul>
            {lessons.patterns.map((p) => (
              <li key={p.tag} className={p.notable ? "notable" : ""}>
                <span className="alert-tag bad">{TAG(p.tag)}</span>{" "}
                {p.notable ? p.suggestion : `${p.count} of ${p.of} losses`}
              </li>
            ))}
          </ul>
          {notable.length > 0 && (
            <small>
              Suggestions change trade rules, so they are not applied
              automatically. Backtest before adopting one.
            </small>
          )}
        </div>
      )}
    </section>
  );
}

function Curve({ points }) {
  if (points.length < 2)
    return (
      <div className="journal-no-chart">
        The cumulative P&amp;L curve appears after two settled sessions.
      </div>
    );
  const values = [0, ...points.map((p) => p.cumulative)],
    low = Math.min(...values),
    high = Math.max(...values),
    range = high - low || 1;
  const x = (i) => 20 + (i * 720) / (values.length - 1),
    y = (v) => 20 + ((high - v) * 145) / range;
  return (
    <svg
      viewBox="0 0 760 190"
      className="journal-curve"
      role="img"
      aria-label="Cumulative net paper P&L of alerted trades by session"
    >
      <line x1="20" x2="740" y1={y(0)} y2={y(0)} className="op-high-line" />
      <polyline
        points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
        fill="none"
        stroke={values.at(-1) >= 0 ? "#34d399" : "#fb7185"}
        strokeWidth="2"
      />
      <text x="20" y="185">
        {day(points[0].session)} → {day(points.at(-1).session)}
      </text>
    </svg>
  );
}

function Session({ session }) {
  const closed = session.trades.filter((t) => t.status === "CLOSED"),
    net = closed.reduce((sum, t) => sum + t.net_pnl, 0),
    open = session.trades.filter((t) => t.status === "OPEN");
  return (
    <section className="panel journal-table alert-session">
      <div className="panel-head">
        <h2>
          {day(session.session)}{" "}
          <span className={`alert-badge ${session.final ? "" : "live"}`}>
            {session.final ? "Final" : "Live · updated " + clock(session.updated_at)}
          </span>
        </h2>
        <strong className={tone(net)}>
          {closed.length ? money(net, 0) : "No closed trades"}
          {open.length ? " · open " + money(open.reduce((s, t) => s + t.net_pnl, 0), 0) : ""}
        </strong>
      </div>
      <div className="op-table-scroll">
        <table>
          <thead>
            <tr>
              <th>Stock</th>
              <th>Plan: entry / stop / T1 / T2</th>
              <th>Status</th>
              <th>Entry · IST</th>
              <th>Exits · IST</th>
              <th>Net P&amp;L</th>
              <th>R</th>
            </tr>
          </thead>
          <tbody>
            {session.trades.map((t) => (
              <React.Fragment key={t.symbol}>
              <tr className={t.diagnosis?.why ? "has-why" : ""}>
                <td>
                  <b>{short(t.symbol)}</b>{" "}
                  <span className={`alert-side ${t.side === "BUY" ? "buy" : "sell"}`}>
                    {t.side}
                  </span>
                  <small>score {number(t.score, 0)}</small>
                </td>
                <td>
                  {money(t.entry_planned)} / {money(t.stop)} / {money(t.target1)} /{" "}
                  {money(t.target2)}
                  <small>qty {t.qty_planned}</small>
                </td>
                <td>
                  <span className={`alert-status s-${t.status.toLowerCase()}`}>
                    {STATUS[t.status] || t.status}
                  </span>
                  <small>{t.exit_reason || t.note || ""}</small>
                </td>
                <td>
                  {t.events.length ? (
                    <>
                      {clock(t.events[0].time)} · {t.events[0].qty} @{" "}
                      {money(t.events[0].price)}
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  {t.events.slice(1).map((e, i) => (
                    <div key={i}>
                      {clock(e.time)} · {e.qty} @ {money(e.price)}
                      <small>{e.reason}</small>
                    </div>
                  ))}
                  {t.status === "OPEN" && (
                    <div>
                      {t.open_qty} open, marked {money(t.mark)}
                    </div>
                  )}
                  {!t.events.slice(1).length && t.status !== "OPEN" && "—"}
                </td>
                <td className={tone(t.net_pnl)}>
                  {money(t.net_pnl)}
                  {Number.isFinite(t.costs) && <small>costs {money(t.costs)}</small>}
                </td>
                <td className={tone(t.r_multiple)}>{signed(t.r_multiple, "R")}</td>
              </tr>
              {t.diagnosis?.why && (
                <tr className="alert-why">
                  <td colSpan="7">
                    <b>Why:</b> {t.diagnosis.why}{" "}
                    {t.diagnosis.tags.map((code) => (
                      <span
                        key={code}
                        className={`alert-tag ${GOOD_TAGS.has(code) ? "good" : "bad"}`}
                      >
                        {TAG(code)}
                      </span>
                    ))}
                  </td>
                </tr>
              )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function AlertTrades({ refreshToken }) {
  const [data, setData] = useState(null),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const r = await fetch("/api/alert-trades", {
          cache: "no-store",
          signal: controller.signal,
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Unable to read alert paper trades.");
        setData(d);
        setError("");
      } catch (e) {
        if (e.name !== "AbortError") setError(e.message);
      } finally {
        busy = false;
      }
    }
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [refreshToken]);
  const s = data?.summary;
  return (
    <section className="journal-page alert-trades">
      <div className="page-title">
        <div>
          <span className="eyebrow">TELEGRAM ALERTS · PAPER TRADED</span>
          <h1>
            Alert trades<span>.</span>
          </h1>
          <p>
            Every alerted stock, traded on paper by its own entry, stop, target
            and 15:10 square-off rules, with estimated charges.
          </p>
        </div>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!data ? (
        <section className="panel empty">Reading alert results…</section>
      ) : (
        <>
          <div className="op-summary-grid">
            {[
              ["Net P&L (closed)", money(s.net_pnl, 0), tone(s.net_pnl)],
              ["Closed trades", number(s.closed, 0), ""],
              [
                "Win rate",
                Number.isFinite(s.win_rate) ? number(s.win_rate, 1) + "%" : "—",
                "",
              ],
              ["Average R", signed(s.avg_r, "R"), tone(s.avg_r)],
            ].map(([label, value, cls]) => (
              <div className="op-summary-card" key={label}>
                <span>{label}</span>
                <strong className={cls}>{value}</strong>
              </div>
            ))}
          </div>
          <section className="panel journal-chart">
            <div className="panel-head">
              <h2>Cumulative net P&amp;L</h2>
              <span className="micro">FINAL SESSIONS ONLY</span>
            </div>
            <Curve points={data.curve} />
            <div className="journal-stats">
              <span>
                Alerts <b>{number(s.alerts, 0)}</b>
              </span>
              <span>
                Not triggered <b>{number(s.not_triggered, 0)}</b>
              </span>
              <span>
                Skipped <b>{number(s.skipped, 0)}</b>
              </span>
              <span>
                Profit factor <b>{number(s.profit_factor)}</b>
              </span>
              <span>
                Charges <b>{money(s.costs, 0)}</b>
              </span>
              {s.open > 0 && (
                <span>
                  Open P&amp;L <b className={tone(s.open_pnl)}>{money(s.open_pnl, 0)}</b>
                </span>
              )}
              {Object.entries(s.by_side).map(([side, v]) => (
                <span key={side}>
                  {side} <b className={tone(v.net_pnl)}>{money(v.net_pnl, 0)}</b> ·{" "}
                  {v.closed} trades · {number(v.win_rate, 0)}% wins
                </span>
              ))}
            </div>
          </section>
          <Lessons lessons={data.lessons} />
          {data.upcoming.map((u) => (
            <section className="panel journal-table" key={u.session}>
              <div className="panel-head">
                <h2>
                  {day(u.session)} <span className="alert-badge live">Upcoming</span>
                </h2>
                <span className="micro">PAPER TRADING STARTS AT THE OPEN</span>
              </div>
              {!u.picks.length ? (
                <p>No setup qualified; nothing to trade.</p>
              ) : (
                <div className="op-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Stock</th>
                        <th>Entry</th>
                        <th>No-chase</th>
                        <th>Stop</th>
                        <th>T1 / T2</th>
                        <th>Qty</th>
                        <th>Max loss</th>
                      </tr>
                    </thead>
                    <tbody>
                      {u.picks.map((p) => (
                        <tr key={p.symbol}>
                          <td>
                            <b>{short(p.symbol)}</b>{" "}
                            <span className={`alert-side ${p.side === "BUY" ? "buy" : "sell"}`}>
                              {p.side}
                            </span>
                            <small>
                              score {number(p.score, 0)}
                              {p.lesson_penalty
                                ? ` (−${p.lesson_penalty} from lessons)`
                                : ""}{" "}
                              · risk {p.risk_rating}
                            </small>
                          </td>
                          <td>{money(p.entry)}</td>
                          <td>{money(p.no_chase)}</td>
                          <td>{money(p.stop)}</td>
                          <td>
                            {money(p.target1)} / {money(p.target2)}
                          </td>
                          <td>{p.qty}</td>
                          <td>{money(p.max_loss, 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          ))}
          {!data.sessions.length && (
            <section className="panel empty">
              <span className="empty-symbol">▤</span>
              <h2>No paper-traded sessions yet.</h2>
              <p>
                Results appear here once an alerted session opens; they update
                every 10 minutes during market hours and become final after
                15:40 IST.
              </p>
            </section>
          )}
          {data.sessions.map((session) => (
            <Session key={session.session} session={session} />
          ))}
          <p className="journal-note">{data.note}</p>
        </>
      )}
    </section>
  );
}
