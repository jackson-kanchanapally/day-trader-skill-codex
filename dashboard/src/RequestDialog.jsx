import React, { useEffect, useLayoutEffect, useState } from "react";

const blank = { capital: "", risk: "", daily: "", exposure: "", pnl: "", positions: "" };
const positive = (value) => value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) > 0;
const stockCode = (value) => {
  const code = value.trim().toUpperCase();
  if (!/^[A-Z0-9&][A-Z0-9&.-]*[A-Z0-9&]$|^[A-Z0-9&]$/.test(code)) return "";
  return /\.(NS|BO)$/.test(code) ? code : `${code}.NS`;
};
const readableTime = (value) => Number.isFinite(new Date(value).getTime())
  ? new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)) + " IST"
  : "Saved scan";
const localTime = (value) => {
  const date = new Date(value || Date.now());
  return new Date(date.getTime() + 19800000).toISOString().slice(0, 16);
};

export default function RequestDialog({ dialog, mode, setMode, seed, requestKey }) {
  const [symbol, setSymbol] = useState("");
  const [purpose, setPurpose] = useState("research");
  const [timeMode, setTimeMode] = useState("latest");
  const [customTime, setCustomTime] = useState("");
  const [account, setAccount] = useState(blank);
  const [fresh, setFresh] = useState(false);
  const [diagnostic, setDiagnostic] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  useLayoutEffect(() => {
    setSymbol((seed?.symbol || "").replace(/\.NS$/, ""));
    setTimeMode(seed?.as_of ? "saved" : "latest");
    setCustomTime("");
    setPurpose("research");
    setAccount(blank);
    setFresh(false);
    setDiagnostic(false);
  }, [seed, requestKey]);

  const code = stockCode(symbol);
  const planning = mode === "review" && purpose === "plan";
  const cutoff = timeMode === "saved" ? seed?.as_of : timeMode === "custom" && customTime ? customTime + "+05:30" : "";
  const timeValid = timeMode === "latest" || (Boolean(cutoff) && Number.isFinite(new Date(cutoff).getTime()) && new Date(cutoff).getTime() <= Date.now());
  const extraLimitsValid = ["daily", "exposure"].every((key) => !account[key].trim() || positive(account[key]));
  const limitsValid = positive(account.capital) && positive(account.risk) && Number(account.risk) <= Number(account.capital)
    && extraLimitsValid
    && (!account.daily.trim() || Number(account.risk) <= Number(account.daily) && Number(account.daily) <= Number(account.capital))
    && (!account.exposure.trim() || Number(account.exposure) <= Number(account.capital))
    && (!account.pnl.trim() || Number.isFinite(Number(account.pnl)));
  const valid = mode !== "review" || (Boolean(code) && timeValid && (!planning || limitsValid));
  const timing = timeMode === "latest"
    ? "Use the latest permitted completed market data when this request runs, and record the exact data cutoff in IST."
    : `Use the exact review cutoff ${cutoff}. Do not shift it silently.`;
  const sourceUsable = !cutoff || new Date(seed?.origin_cutoff).getTime() <= new Date(cutoff).getTime();
  const origin = seed?.origin_cutoff && !sourceUsable
    ? " The saved scan was created after the requested cutoff. Exclude it and use historical evidence available by the requested time."
    : seed?.origin_cutoff
    ? ` Inspect the original saved daily scan at ${seed.origin_cutoff}, preserving its hash and exact symbol evidence. Record that source cutoff separately from the review cutoff.` : "";
  const limits = planning
    ? `My explicitly supplied paper capital is INR ${account.capital || "[required]"} and maximum cost-inclusive loss per trade is INR ${account.risk || "[required]"}.${account.daily.trim() ? ` Maximum daily loss: INR ${account.daily}.` : ""}${account.exposure.trim() ? ` Maximum position/order value: INR ${account.exposure}.` : ""} ${fresh ? "I confirm no open paper positions and zero realised daily P&L." : `${account.pnl.trim() ? `Today's realised P&L: INR ${account.pnl}. ` : ""}${account.positions.trim() ? `Current positions and exposure: ${account.positions}.` : ""}`}
Verify dated INR tick metadata, available cash, open risk, remaining daily loss room, concentration limits, trade/order caps and cost/slippage assumptions before calculating any quantity. Ask for missing account details at that stage. Do not substitute engine defaults for my choices. Require a completed-bar entry, a valid stop/target plan and independent exact-order risk clearance before showing a ready paper proposal.`
    : "This is company research. Continue the available analysis without asking for account details. Explain the setup evidence, catalysts, risks, completed-bar entry availability and invalidation conditions. Leave account-specific sizing and order clearance pending; obtain my actual capital and risk limits if I later request a trade plan. Do not insert example account values just to start a team session.";
  const save = "Save the findings in Individual research using the appropriate contract in docs/DASHBOARD_DATA.md. If a full team cannot start, save a clearly labelled coordinator prerequisite assessment; bind it to the original daily scan when available, otherwise explicitly mark setup evidence unavailable. Never represent coordinator findings as independent specialist reviews.";
  const prompt = mode === "universe"
    ? "Use $trader-scanner to discover the NSE cash-equity universe using scan.py universe --exchange NSE. Save it to trader/universe.txt and report how many stocks were found."
    : mode === "scan"
      ? "Use $trader-scanner to refresh the NSE cash-equity universe using scan.py universe --exchange NSE, saving it to trader/universe.txt. Then run scan.py daily --universe trader/universe.txt across the entire saved universe without --limit. Show matching stocks, their setups, the data cutoff, and counts of successfully scanned and failed symbols."
      : `Use $trader-team to ${planning ? "review a possible paper trade in" : "research"} ${code || "[stock symbol]"}. ${timing}${origin}
${limits}
Inspect original setup evidence and permitted market data, dated original news/filings, liquidity, spread/price-band availability and extension. Date each source and keep unavailable evidence visible. ${planning && diagnostic ? "I explicitly request the full independent diagnostic team review even if this cutoff is closed-market or has no eligible setup. " : "Inspect prerequisites before dispatch; continue the research steps that are available. "}${save} Record real specialist lifecycle events when specialists run. This request does not approve any simulated fill or live order.`;

  useEffect(() => { setCopied(false); setCopyError(false); }, [prompt]);
  const update = (key, value) => setAccount((current) => ({ ...current, [key]: value }));
  const action = mode === "scan" ? "Copy scan request" : mode === "universe" ? "Copy universe request" : planning ? "Copy trade-planning request" : "Copy research request";
  const title = mode === "scan" ? "Scan the NSE market." : mode === "universe" ? "Get the NSE stock list." : planning ? "Plan a paper trade." : "Research a stock.";

  return <dialog ref={dialog} className="request-dialog request-simple" aria-labelledby="request-title">
    <div className="modal-head"><div><span className="eyebrow">TRADE DESK</span><h2 id="request-title">{title}</h2></div><button className="icon-button" onClick={() => dialog.current.close()} aria-label="Close Codex request">×</button></div>
    <p>Copy the request, then paste it into Codex to run.</p>
    <label className="request-type">Task<select aria-label="Task" value={mode} onChange={(e) => { setMode(e.target.value); setPurpose("research"); }}><option value="review">Research a stock</option><option value="scan">Scan the NSE market</option><option value="universe">Get the NSE stock list</option></select></label>
    {mode === "review" ? <>
      <label className="request-stock-label">Stock symbol<input autoFocus autoComplete="off" spellCheck="false" value={symbol} placeholder="e.g. SSWL or RELIANCE" onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></label>
      <div className="request-context">{code ? <b>{code} · {code.endsWith(".BO") ? "BSE" : "NSE"}</b> : <span>NSE by default. BSE symbols can end in .BO.</span>}<span>{timeMode === "latest" ? "Latest available data" : timeMode === "saved" ? `Saved scan · ${readableTime(seed?.as_of)}` : customTime ? readableTime(cutoff) : "Choose a date below"}</span></div>
      <div className="request-purpose" role="group" aria-label="Research or trade planning"><button className={purpose === "research" ? "active" : ""} aria-pressed={purpose === "research"} onClick={() => setPurpose("research")}>Research only</button><button className={purpose === "plan" ? "active" : ""} aria-pressed={purpose === "plan"} onClick={() => setPurpose("plan")}>Plan a paper trade</button></div>
      {planning ? <>
        <div className="form-grid"><label>Paper capital · INR<input type="number" min="0" step="any" inputMode="decimal" value={account.capital} placeholder="Your trading capital" onChange={(e) => update("capital", e.target.value)} /></label><label>Maximum loss per trade · INR<input type="number" min="0" step="any" inputMode="decimal" value={account.risk} placeholder="Your risk limit" onChange={(e) => update("risk", e.target.value)} /></label></div>
        {positive(account.capital) && positive(account.risk) && !limitsValid && <p className="request-error" role="alert">Risk limits must be positive and fit within your capital and daily limit.</p>}
        <details className="request-options"><summary>More trade settings <small>Optional</small></summary><div className="form-grid"><label>Maximum daily loss · INR<input type="number" min="0" step="any" value={account.daily} onChange={(e) => update("daily", e.target.value)} /></label><label>Maximum position value · INR<input type="number" min="0" step="any" value={account.exposure} onChange={(e) => update("exposure", e.target.value)} /></label></div><label className="request-check"><input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} /> No open paper positions and zero realised daily P&amp;L.</label>{!fresh && <div className="form-grid"><label>Today’s realised P&amp;L · INR<input type="number" step="any" value={account.pnl} placeholder="Losses use a minus sign" onChange={(e) => update("pnl", e.target.value)} /></label><label>Current positions<textarea value={account.positions} placeholder="Symbols, shares and open risk, if known" onChange={(e) => update("positions", e.target.value)} /></label></div>}<label className="request-check"><input type="checkbox" checked={diagnostic} onChange={(e) => setDiagnostic(e.target.checked)} /> Include all specialists even when the market is closed.</label></details>
        <p className="request-help">Codex will obtain any remaining account details before calculating a position size.</p>
      </> : <p className="request-help">Review the company first. Add trade sizing when you need it.</p>}
      <details key={`dates-${requestKey}`} className="request-options"><summary>Date options <small>Optional</small></summary><label className="request-type">Data to research<select aria-label="Data to research" value={timeMode} onChange={(e) => { setTimeMode(e.target.value); if (e.target.value === "custom" && !customTime) setCustomTime(localTime(seed?.as_of)); }}><option value="latest">Latest available data</option>{seed?.as_of && <option value="saved">This saved scan</option>}<option value="custom">Choose a date and time</option></select></label>{timeMode === "custom" && <label className="request-stock-label">Date and time · IST<input type="datetime-local" value={customTime} onChange={(e) => setCustomTime(e.target.value)} /></label>}{!timeValid && <p className="request-error">Choose a date and time that has already passed.</p>}</details>
    </> : <p>{mode === "scan" ? "Refresh the NSE list and scan every saved symbol for daily setups. The result includes all matches and coverage failures." : "Discover NSE cash-equity symbols and save the list for market scans."}</p>}
    <details key={`preview-${requestKey}`} className="request-preview"><summary>Preview request</summary><pre className="prompt">{prompt}</pre></details>
    <button className="primary" disabled={!valid} onClick={async () => { try { await navigator.clipboard.writeText(prompt); setCopied(true); } catch { setCopyError(true); } }}>{copied ? "Copied ✓" : `${action} ↗`}</button>
    <p className="micro" role="status">{copyError ? "Open Preview request to copy the text manually." : copied ? "Paste into Codex to run it." : mode === "review" && !code ? "Enter a stock symbol to continue." : ""}</p>
  </dialog>;
}
