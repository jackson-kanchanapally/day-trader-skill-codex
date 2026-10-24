import React, { useEffect, useLayoutEffect, useState } from "react";

const positive = (value) =>
  value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) > 0;
const blank = {
  capital: "",
  risk: "",
  daily: "",
  exposure: "",
  pnl: "",
  positions: "",
};
export default function RequestDialog({ dialog, mode, setMode, seed }) {
  const [symbol, setSymbol] = useState(""),
    [cutoff, setCutoff] = useState(""),
    [account, setAccount] = useState(blank),
    [fresh, setFresh] = useState(false),
    [diagnostic, setDiagnostic] = useState(false),
    [copied, setCopied] = useState(false),
    [copyError, setCopyError] = useState(false);
  useLayoutEffect(() => {
    setSymbol(seed?.symbol || "");
    setCutoff(seed?.as_of || "");
    setAccount(blank);
    setFresh(false);
    setDiagnostic(false);
  }, [seed]);
  const fieldsValid = ["capital", "risk", "daily", "exposure"].every((k) =>
    positive(account[k]),
  );
  const limitsValid =
    fieldsValid &&
    Number(account.risk) <= Number(account.daily) &&
    Number(account.daily) <= Number(account.capital) &&
    Number(account.exposure) <= Number(account.capital);
  const accountValid =
    fresh ||
    (account.pnl.trim() !== "" &&
      Number.isFinite(Number(account.pnl)) &&
      account.positions.trim() !== "");
  const timeValid =
    /^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(cutoff) &&
    Number.isFinite(new Date(cutoff).getTime());
  const valid =
    mode !== "review" ||
    (/^[A-Z0-9&.-]+\.(NS|BO)$/.test(symbol) &&
      limitsValid &&
      accountValid &&
      timeValid);
  const dailyRoom =
    fieldsValid && (fresh || account.pnl.trim() !== "")
      ? Math.max(
          0,
          Number(account.daily) + Math.min(0, fresh ? 0 : Number(account.pnl)),
        )
      : null;
  const prompt =
    mode === "universe"
      ? "Use $trader-scanner to discover the NSE cash-equity universe using scan.py universe --exchange NSE. Save it to trader/universe.txt and report how many stocks were found."
      : mode === "scan"
        ? "Use $trader-scanner to refresh the NSE cash-equity universe using scan.py universe --exchange NSE, saving it to trader/universe.txt. Then run scan.py daily --universe trader/universe.txt across the entire saved universe without --limit. Show matching stocks, their setups, the data cutoff, and counts of successfully scanned and failed symbols."
        : `Use $trader-team to review ${symbol || "[choose symbol]"} at the exact cutoff ${cutoff || "[choose an ISO cutoff with timezone]"}.${seed?.origin_cutoff ? ` Inspect its original saved daily scan at ${seed.origin_cutoff}; distinguish that scan cutoff from the requested review cutoff.` : ""}
My explicitly supplied paper capital is INR ${account.capital || "[required]"}, maximum cost-inclusive loss per trade INR ${account.risk || "[required]"}, maximum daily loss INR ${account.daily || "[required]"}, and maximum position/order value INR ${account.exposure || "[required]"}. ${fresh ? "I confirm a fresh paper account with no open positions and zero realised daily P&L." : `Today’s realised P&L is INR ${account.pnl || "[required]"}; current positions and exposure: ${account.positions || "[required]"}.`}
Obtain dated verified INR tick metadata and permitted market data. Confirm available cash, open risk, cash buffer, concentration limits, trade/order caps, minimum net reward/risk and the applicable cost/slippage assumptions with me before sizing; do not adopt engine defaults as my choices. Inspect completed-bar entry, spread, price bands/halts, liquidity, extension, stops, costs and original dated news. Keep unavailable evidence visible and require independent risk clearance for any exact paper proposal.
${diagnostic ? "I explicitly request the full independent diagnostic team review even if the cutoff is closed-market or has no eligible setup." : "Inspect preflight before dispatch. If prerequisites prevent a full team run, save a coordinator prerequisite assessment tied to the original scan under trader/reviews/SYMBOL-TIMESTAMP/readiness.json following docs/DASHBOARD_DATA.md, preserving the original scan hash and exact symbol evidence, so Individual research can display it. Explain the blocker and do not shift the cutoff silently."} Record real specialist lifecycle events for the dashboard when specialists run. Explain every decision. This request does not approve any simulated fill or live order.`;
  useEffect(() => {
    setCopied(false);
    setCopyError(false);
  }, [prompt]);
  function update(key, value) {
    setAccount((current) => ({ ...current, [key]: value }));
  }
  return (
    <dialog
      ref={dialog}
      className="request-dialog"
      aria-labelledby="request-title"
    >
      <div className="modal-head">
        <div>
          <span className="eyebrow">CODEX REQUEST</span>
          <h2 id="request-title">Prepare your research.</h2>
        </div>
        <button
          className="icon-button"
          onClick={() => dialog.current.close()}
          aria-label="Close Codex request"
        >
          ×
        </button>
      </div>
      <p>
        Supply your inputs, then copy the request into Codex. A copied request
        starts work when you run it there.
      </p>
      <label className="request-type">
        Request type
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="review">Review one stock</option>
          <option value="universe">Discover NSE universe</option>
          <option value="scan">Scan NSE opportunities</option>
        </select>
      </label>
      {mode === "review" ? (
        <>
          <div className="form-grid">
            <label>
              Symbol
              <input
                value={symbol}
                placeholder="NSE .NS or BSE .BO"
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              />
            </label>
            <label>
              Review cutoff · ISO + timezone
              <input
                value={cutoff}
                placeholder="YYYY-MM-DDTHH:MM:SS+05:30"
                onChange={(e) => setCutoff(e.target.value)}
              />
            </label>
          </div>
          <button
            className="request-clock"
            onClick={() =>
              setCutoff(
                new Date(Date.now() + 19800000).toISOString().slice(0, 19) +
                  "+05:30",
              )
            }
          >
            Use current IST cutoff ↗
          </button>
          {seed?.origin_cutoff && (
            <p className="micro">
              Original saved daily cutoff: {seed.origin_cutoff}. Selecting a new
              cutoff requires fresh entry-session evidence.
            </p>
          )}
          <div className="request-divider">YOUR PAPER LIMITS · INR</div>
          <div className="form-grid">
            {[
              ["capital", "Paper capital"],
              ["risk", "Maximum loss per trade"],
              ["daily", "Maximum daily loss"],
              ["exposure", "Maximum position / order value"],
            ].map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  min="0"
                  step="any"
                  inputMode="decimal"
                  value={account[key]}
                  placeholder="Enter your limit"
                  onChange={(e) => update(key, e.target.value)}
                />
              </label>
            ))}
          </div>
          {fieldsValid && !limitsValid && (
            <p className="request-error" role="alert">
              Per-trade loss must fit the daily loss limit; daily loss and
              position value must fit capital.
            </p>
          )}
          <label className="request-check">
            <input
              type="checkbox"
              checked={fresh}
              onChange={(e) => setFresh(e.target.checked)}
            />{" "}
            I confirm a fresh paper account: no positions and zero realised
            daily P&amp;L.
          </label>
          {!fresh && (
            <div className="form-grid">
              <label>
                Today’s realised P&amp;L · INR
                <input
                  type="number"
                  step="any"
                  value={account.pnl}
                  placeholder="Losses use a minus sign"
                  onChange={(e) => update("pnl", e.target.value)}
                />
              </label>
              <label>
                Current positions and exposure
                <textarea
                  value={account.positions}
                  placeholder="Symbols, shares, costs, stops and open risk; or explicitly state none"
                  onChange={(e) => update("positions", e.target.value)}
                />
              </label>
            </div>
          )}
          {Number.isFinite(dailyRoom) && (
            <p className="request-room">
              Daily loss room before open risk:{" "}
              <b>₹{dailyRoom.toLocaleString("en-IN")}</b>
              {dailyRoom === 0
                ? " · Daily loss limit exhausted. New trades require a risk block."
                : ""}
            </p>
          )}
          <label className="request-check">
            <input
              type="checkbox"
              checked={diagnostic}
              onChange={(e) => setDiagnostic(e.target.checked)}
            />{" "}
            Request full independent diagnostic reviews even for a closed-market
            or no-setup cutoff.
          </label>
          <p className="micro">
            Tick metadata, broker costs and missing account constraints are
            verified in Codex before quantities are calculated.
          </p>
        </>
      ) : (
        <p>
          Universe discovery saves symbols. A complete daily scan finds research
          candidates across every saved symbol and reports coverage failures.
        </p>
      )}
      <details className="request-preview">
        <summary>Inspect the exact request</summary>
        <pre className="prompt">{prompt}</pre>
      </details>
      <button
        className="primary"
        disabled={!valid}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(prompt);
            setCopied(true);
          } catch {
            setCopyError(true);
          }
        }}
      >
        {copied ? "Copied to clipboard ✓" : "Copy Codex request ↗"}
      </button>
      <p className="micro" role="status">
        {copyError
          ? "Clipboard unavailable. Expand the request and copy its text."
          : copied
            ? "Ready to paste into Codex."
            : !valid
              ? "Complete the symbol, cutoff and your explicit paper inputs to copy this review."
              : ""}
      </p>
    </dialog>
  );
}
