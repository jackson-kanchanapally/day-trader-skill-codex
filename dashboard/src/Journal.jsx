import React, { useEffect, useState } from "react";

const number = (value) =>
  Number.isFinite(value)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)
    : "—";
const money = (value) => (Number.isFinite(value) ? "₹" + number(value) : "—");
const when = (value) =>
  new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
function EquityCurve({ points }) {
  if (points.length < 2)
    return (
      <div className="journal-no-chart">
        The realized P&amp;L curve appears after two recorded paper exits.
      </div>
    );
  const values = [0, ...points.map((p) => p.realized_pnl)],
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
      aria-label="Cumulative realized paper profit and loss after recorded exits"
    >
      <line x1="20" x2="740" y1={y(0)} y2={y(0)} className="op-high-line" />
      <polyline
        points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
        fill="none"
        stroke={values.at(-1) >= 0 ? "#34d399" : "#fb7185"}
        strokeWidth="2"
      />
      <text x="20" y="185">
        First paper exit → latest
      </text>
    </svg>
  );
}
export default function Journal({ refreshToken, onResearch }) {
  const [data, setData] = useState(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [account, setAccount] = useState("auto");
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const r = await fetch(`/api/journal?account=${account}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "Unable to read paper journal.");
        setData(d);
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
  }, [refreshToken, account]);
  const exits = (data?.exits || []).filter((e) =>
    e.symbol.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <section className="journal-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">RECORDED PAPER OUTCOMES</span>
          <h1>
            Trading journal<span>.</span>
          </h1>
          <p>
            Follow the account, actual simulated fills and results after
            recorded costs.
          </p>
        </div>
        <label>Paper account<select aria-label="Paper account" value={account} onChange={(e)=>setAccount(e.target.value)}><option value="auto">Automated account</option><option value="manual">Manual tickets</option></select></label>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!data ? (
        <section className="panel empty">
          Reading the local paper ledger…
        </section>
      ) : data.status !== "RECORDED" ? (
        <section className="panel empty">
          <span className="empty-symbol">▤</span>
          <h2>
            {data.status === "NO_ACCOUNT"
              ? "No recorded paper trades yet."
              : "Paper ledger requires review."}
          </h2>
          <p>{data.note}</p>
          <p className="micro">
            Only approved, recorded simulated fills count as performance. A scan
            or specialist review creates no trade.
          </p>
        </section>
      ) : (
        <>
          <div className="op-summary-grid">
            {[
              ["Available paper cash", money(data.cash)],
              ["Realized net P&L", money(data.realized_pnl)],
              ["Recorded costs", money(data.total_costs)],
              ["Closed sell fills", number(data.closed_exits)],
            ].map(([label, value]) => (
              <div className="op-summary-card" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <p className="journal-note">{data.note}</p>
          <section className="panel journal-chart">
            <div className="panel-head">
              <h2>Realized account P&amp;L</h2>
              <span className="micro">
                UNREALIZED P&amp;L REQUIRES CURRENT MARKS
              </span>
            </div>
            <EquityCurve points={data.equity_curve} />
            <div className="journal-stats">
              <span>
                Exit win rate{" "}
                <b>
                  {Number.isFinite(data.win_rate)
                    ? number(data.win_rate) + "%"
                    : "—"}
                </b>
              </span>
              <span>
                Profit factor <b>{number(data.profit_factor)}</b>
              </span>
              <span>
                Average net per exit <b>{money(data.average_exit_pnl)}</b>
              </span>
            </div>
          </section>
          <div className="research-layout">
            <section className="panel journal-table">
              <h2>Setup outcomes</h2>
              <p>
                FIFO allocations from recorded entries and exits. Market regime
                remains unclassified until recorded evidence is available.
              </p>
              <div className="op-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Entry setup</th>
                      <th>Exit allocations</th>
                      <th>Net P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.groups.map((g) => (
                      <tr key={g.setup}>
                        <td>{g.setup}</td>
                        <td>{g.exit_allocations}</td>
                        <td
                          className={
                            g.net_pnl >= 0 ? "positive" : "op-negative"
                          }
                        >
                          {money(g.net_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.groups.length && <p>No exits have been recorded.</p>}
            </section>
            <section className="panel journal-table">
              <h2>Open paper positions</h2>
              <div className="op-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Shares</th>
                      <th>Remaining lot cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.positions.map((p) => (
                      <tr key={p.ticker || p.symbol}>
                        <td>{p.ticker || p.symbol}</td>
                        <td>{p.shares || p.qty}</td>
                        <td>
                          {money(
                            (p.lots || []).reduce((sum, l) => sum + l.cost, 0),
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.positions.length && <p>No open paper positions.</p>}
            </section>
          </div>
          <section className="panel journal-table">
            <div className="panel-head">
              <h2>Recorded exits</h2>
              <input
                aria-label="Filter journal by symbol"
                placeholder="Filter symbol…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="op-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time · IST</th>
                    <th>Symbol</th>
                    <th>Shares</th>
                    <th>Fill price</th>
                    <th>Exit costs</th>
                    <th>Realized net P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {exits.map((e) => (
                    <tr key={e.id}>
                      <td>{when(e.at)}</td>
                      <td>
                        <button
                          className="journal-research-link"
                          onClick={() => onResearch(null, e.symbol)}
                        >
                          {e.symbol} ↗
                        </button>
                      </td>
                      <td>{e.qty}</td>
                      <td>{money(e.price)}</td>
                      <td>{money(e.fees)}</td>
                      <td
                        className={e.net_pnl >= 0 ? "positive" : "op-negative"}
                      >
                        {money(e.net_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel journal-table journal-entries">
            <h2>Recorded entries and original plans</h2>
            <div className="op-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time · IST</th>
                    <th>Symbol / setup</th>
                    <th>Shares</th>
                    <th>Fill</th>
                    <th>Planned entry</th>
                    <th>Stop</th>
                    <th>Target</th>
                    <th>Entry costs</th>
                  </tr>
                </thead>
                <tbody>
                  {data.entries
                    .filter((e) =>
                      e.symbol.toLowerCase().includes(query.toLowerCase()),
                    )
                    .map((e) => (
                      <tr key={e.id}>
                        <td>{when(e.at)}</td>
                        <td>
                          <button
                            className="journal-research-link"
                            onClick={() => onResearch(null, e.symbol)}
                          >
                            {e.symbol} ↗
                          </button>
                          <small>{e.setup}</small>
                        </td>
                        <td>{e.qty}</td>
                        <td>{money(e.price)}</td>
                        <td>{money(e.planned_entry)}</td>
                        <td>{money(e.stop)}</td>
                        <td>{money(e.target)}</td>
                        <td>{money(e.fees)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
