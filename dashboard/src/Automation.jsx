import React, { useEffect, useState } from "react";

const money = (n) => Number.isFinite(n) ? new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(n) : "—";
const number = (n) => Number.isFinite(n) ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(n) : "—";
const when = (s) => s && Number.isFinite(new Date(s).getTime()) ? new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }).format(new Date(s)) + " IST" : "—";
const label = (s) => String(s || "Pending").replaceAll("_", " ");
async function read(path, options = {}) {
  const r = await fetch(path, { cache: "no-store", ...options });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "Automatic paper account is unavailable");
  return d;
}
function Curve({ points }) {
  if (points.length < 2) return <p className="micro">The paper equity curve appears as observed account values accumulate.</p>;
  const values = points.map((p) => p.performance_equity??p.equity), low = Math.min(...values), high = Math.max(...values), range = high - low || 1;
  return <svg viewBox="0 0 900 180" role="img" aria-label="Simulated equity after costs with capital transfers removed" className="journal-curve">
    <polyline points={values.map((v,i) => `${15+i*870/Math.max(1,values.length-1)},${15+(high-v)*135/range}`).join(" ")} fill="none" stroke={values.at(-1)>=values[0]?"#34d399":"#fb7185"} strokeWidth="2" />
    <text x="15" y="173">{when(points[0].at)} → {when(points.at(-1).at)}</text>
  </svg>;
}
export default function Automation() {
  const [data,setData]=useState(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  useEffect(() => {
    const controller=new AbortController(); let active=true,fetching=false;
    async function refresh() { if(fetching) return; fetching=true; try { const next=await read("/api/automation",{signal:controller.signal}); if(active){setData(next);setError("");} } catch(e){if(active&&e.name!=="AbortError")setError(e.message);} finally{fetching=false;} }
    refresh(); const timer=setInterval(refresh,5000);
    return () => {active=false;controller.abort();clearInterval(timer);};
  },[]);
  async function toggle() {
    setBusy(true);
    try { const auth=await read("/api/runner"); await read("/api/automation/entries",{method:"POST",headers:{"Content-Type":"application/json","X-Trader-Token":auth.token||""},body:JSON.stringify({enabled:!data.enabled})}); setData(await read("/api/automation")); }
    catch(e){setError(e.message);} finally{setBusy(false);}
  }
  const daily=data?.scanner?.daily||{},orb=data?.scanner?.orb||{},p=data?.performance||{},j=data?.journal||{};
  return <section className="journal-page">
    <div className="page-title"><div><span className="eyebrow">FULLY AUTOMATIC · SIMULATED FUNDS</span><h1>Automatic paper trading<span>.</span></h1><p>Full NSE discovery, ranked setups, risk checks and simulated bracket management.</p></div>
      {data?.configured&&<button className="op-secondary" disabled={busy} onClick={toggle}>{data.enabled?"Pause new entries":"Resume new entries"}</button>}
    </div>
    {error&&<p className="notice error" role="alert">{error}</p>}
    {!data?<section className="panel empty">Reading worker status…</section>:!data.configured?<section className="panel empty"><h2>Automatic paper trading is not configured.</h2><p>Configure the simulated account and start its background worker to begin.</p></section>:<>
      <div className="market-readiness-banner"><span className={`decision-pill ${data.worker_running?"caution":"warning"}`}>{label(data.phase)}</span><div><b>{data.worker_running?"Worker heartbeat is current":"Worker is offline or its heartbeat is stale"}</b><p>Last heartbeat {when(data.heartbeat_at)}. {data.enabled?"New entries are enabled under the saved paper policy.":"New entries are paused. Existing positions retain their protective exits."}</p></div></div>
      <div className="op-summary-grid">{[["Paper equity",money(p.equity)],["Realized net P&L",money(j.realized_pnl)],["Unrealized net P&L",money(p.unrealized_pnl)],["Paper return",number(p.return_pct)+"%"],["Maximum observed drawdown",number(p.max_drawdown_pct)+"%"],["Recorded costs",money(j.total_costs)]].map(([title,value])=><div className="op-summary-card" key={title}><span>{title}</span><strong>{value}</strong></div>)}</div>
      <p className="journal-note">{data.note} Missing current marks leave equity and unrealized results unavailable.</p>
      {data.pending_capital_change&&<p className="notice">Capital will change to {money(data.pending_capital_change.capital)} automatically after existing positions close. New entries are paused; protective exits remain active. The daily loss limit is {money(data.pending_capital_change.daily_loss)}.</p>}
      <section className="panel journal-chart"><div className="panel-head"><h2>Observed paper equity</h2><span className="micro">{j.closed_exits||0} exits · win rate {number(j.win_rate)}%</span></div><Curve points={data.equity_curve||[]} /></section>
      <section className="panel"><div className="panel-head"><h2>Account limits</h2></div><div className="journal-stats"><span>Starting funds <b>{money(data.risk.budget)}</b></span><span>Risk per trade <b>{money(data.risk.max_loss_per_trade_inr)}</b></span><span>Daily loss limit <b>{money(data.risk.max_daily_loss_inr)}</b></span><span>New trades per day <b>{data.risk.max_trades_per_day}</b></span><span>Flatten by <b>{data.risk.flat_by} IST</b></span></div></section>
      <section className="panel"><div className="panel-head"><h2>Market coverage</h2><span className="micro">{label(data.scanner?.phase)} · {label(data.scanner?.stage)}</span></div><p>Daily cutoff {when(daily.daily_cutoff)}: {daily.requested??"—"} requested, {daily.scanned??"—"} scanned, {daily.failed??"—"} failed, {daily.unavailable??"—"} unavailable, {daily.stale??"—"} stale, {daily.skipped??"—"} skipped. {daily.matches??"—"} daily matches; {data.scanner?.shortlisted??"—"} pass the automatic filters.</p><p>ORB cutoff {when(orb.as_of)}: {orb.requested??"—"} requested, {orb.scanned??"—"} scanned, {orb.failed??"—"} failed, {orb.skipped??"—"} skipped.</p>{data.scanner?.error&&<p className="notice error">{data.scanner.error}</p>}</section>
      {Number.isFinite(daily.fresh_coverage)&&<p className="journal-note">Fresh daily screening coverage {number(daily.fresh_coverage*100)}%; retrieval coverage {number(daily.retrieval_coverage*100)}%. Stale and insufficient histories cannot supply entry candidates.</p>}
      {data.pending&&<p className="notice">{data.pending.symbol}: {data.pending.qty} shares await the first permitted observed fill candle. Submitted {when(data.pending.submitted_at)}.</p>}
      <section className="panel"><div className="panel-head"><h2>Managed positions</h2></div><div className="op-table-scroll"><table><thead><tr><th>Symbol</th><th>Shares</th><th>Stop</th><th>Target</th><th>Last observed mark</th></tr></thead><tbody>{data.positions.map(r=><tr key={r.symbol}><td>{r.symbol}</td><td>{r.qty}</td><td>{money(r.stop)}</td><td>{money(r.target)}</td><td>{money(r.mark?.price)} · {when(r.mark?.at)}</td></tr>)}</tbody></table></div>{!data.positions.length&&<p>No open simulated positions.</p>}</section>
      <section className="panel"><div className="panel-head"><h2>Ranked entry candidates</h2><span className="micro">Rule score · current confirmation required</span></div><div className="op-table-scroll"><table><thead><tr><th>Symbol</th><th>Score</th><th>Daily setups</th><th>Entry now</th><th>Checked</th></tr></thead><tbody>{data.ranked.map(r=><tr key={r.symbol}><td>{r.symbol}</td><td>{number(r.score)}</td><td>{r.setups.map(label).join(", ")}</td><td>{r.eligible?"Fresh confirmation":"Expired / unavailable"}</td><td>{when(r.checked_at)}</td></tr>)}</tbody></table></div>{!data.ranked.length&&<p>No qualifying fresh entries in the latest completed scan.</p>}</section>
      <section className="panel"><div className="panel-head"><h2>Decisions and simulated fills</h2></div><div className="op-table-scroll"><table><thead><tr><th>Observed</th><th>Symbol</th><th>Decision</th><th>Result / reason</th></tr></thead><tbody>{data.decisions.map((r,i)=><tr key={i}><td>{when(r.at)}</td><td>{r.symbol||"Account"}</td><td>{label(r.status)}</td><td>{r.reason||r.outcome||(r.qty?`${r.qty} shares at ${money(r.price||r.planned_entry)}`:"")}{Number.isFinite(r.net_pnl)&&` · net ${money(r.net_pnl)}`}</td></tr>)}</tbody></table></div></section>
      {data.errors?.map((e,i)=><p className="notice error" key={i}>{e}</p>)}
    </>}
  </section>;
}
