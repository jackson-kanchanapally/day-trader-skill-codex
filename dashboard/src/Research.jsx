import React, { useEffect, useState } from "react";

const date = (value) =>
  value
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value)) + " IST"
    : "—";
const safeUrl = (value) => {
  try {
    const u = new URL(value);
    return u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
};
const money = (value) =>
  Number.isFinite(value)
    ? "₹" +
      new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value)
    : "—";
async function read(path, signal) {
  const r = await fetch(path, { cache: "no-store", signal });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || "Research is unavailable.");
  return d;
}
export default function Research({
  target,
  symbol,
  onOpenTeam,
  onPrepare,
  refreshToken,
}) {
  const [list, setList] = useState(null),
    [selected, setSelected] = useState(target || ""),
    [detail, setDetail] = useState(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    if (target) setSelected(target);
    setQuery(symbol || "");
  }, [target, symbol]);
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      if (busy) return;
      busy = true;
      try {
        const d = await read("/api/research", controller.signal);
        setList(d);
        setRevision((n) => n + 1);
        setSelected(
          (id) =>
            target ||
            id ||
            d.items.find((r) => !symbol || r.symbol === symbol)?.id ||
            "",
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
  }, [refreshToken]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetail(null);
    read("/api/research/" + encodeURIComponent(selected), controller.signal)
      .then(setDetail)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [selected, revision]);
  const items = (list?.items || []).filter((r) =>
    (r.symbol + " " + r.name + " " + r.label)
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <section className="research-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">ONE RECORD FOR EVERY REVIEW STAGE</span>
          <h1>
            Individual research<span>.</span>
          </h1>
          <p>
            Saved prerequisite findings and independent team decisions,
            connected to each company.
          </p>
        </div>
        <button className="op-secondary" onClick={() => onPrepare()}>
          ＋ Prepare a review
        </button>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!!list?.rejected && (
        <p className="notice">
          {list.rejected} saved assessment(s) failed their source or identity
          binding and are excluded.
        </p>
      )}
      <div className="research-layout">
        <aside className="panel research-directory">
          <label>
            Find research
            <input
              aria-label="Search individual research"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Company symbol or review type…"
            />
          </label>
          {items.map((r) => (
            <button
              key={r.id}
              className={selected === r.id ? "selected" : ""}
              onClick={() => {
                setSelected(r.id);
                setDetail(null);
              }}
            >
              <span>
                <b>{r.symbol}</b>
                <small>
                  {r.kind === "ASSESSMENT"
                    ? "PREREQUISITES"
                    : "INDEPENDENT TEAM"}
                </small>
              </span>
              {r.name !== r.symbol && <p>{r.name}</p>}
              <p>{date(r.as_of)}</p>
              <em>{r.label}</em>
            </button>
          ))}
          {!items.length && (
            <div className="empty small">
              {list
                ? "No saved research matches. Prepare a review from an opportunity to start."
                : "Reading saved research…"}
            </div>
          )}
        </aside>
        <div className="research-content">
          {detail ? (
            <>
              <section className="panel research-summary">
                <span className="eyebrow">{detail.label.toUpperCase()}</span>
                <div>
                  <h2>{detail.symbol}</h2>
                  <span className="decision-pill caution">
                    {detail.status.replaceAll("_", " ")}
                  </span>
                </div>
                <p>Decision cutoff: {date(detail.as_of)}</p>
                {detail.source_scan_cutoff && (
                  <p>
                    Original daily source cutoff:{" "}
                    {date(detail.source_scan_cutoff)}
                  </p>
                )}
                {detail.name !== detail.symbol && <p>{detail.name}</p>}
                <p className="research-boundary">
                  {detail.kind === "ASSESSMENT"
                    ? "These are coordinator prerequisite findings. Independent specialists have not issued clearance for this assessment."
                    : "These decisions belong to this saved snapshot. A current trade requires fresh evidence and a new exact-order risk check."}
                </p>
                <div className="research-actions">
                  {detail.session_id && (
                    <button
                      className="op-secondary"
                      onClick={() => onOpenTeam(detail.session_id)}
                    >
                      Open specialist desks ↗
                    </button>
                  )}
                  <button
                    className="op-secondary"
                    onClick={() =>
                      onPrepare({
                        symbol: detail.symbol,
                        as_of: detail.as_of,
                        origin_cutoff: detail.source_scan_cutoff,
                      })
                    }
                  >
                    Prepare follow-up review ↗
                  </button>
                </div>
              </section>
              {detail.integrity_errors?.map((e, i) => (
                <p className="notice error" key={i}>
                  {e}
                </p>
              ))}
              {detail.evidence_status === "UNAVAILABLE" && (
                <p className="notice">
                  Setup evidence is unavailable for this prerequisite note. No
                  daily setup or independent clearance is confirmed.
                </p>
              )}
              {detail.proposal && (
                <section className="panel research-evidence">
                  <span className="eyebrow">
                    ARCHIVED NUMERICAL PLAN · SAVED CUTOFF ONLY
                  </span>
                  <p>
                    This draft retains the sealed engine's levels and cost
                    assumptions. Independent decisions above determine its saved
                    readiness; a new order requires a fresh check.
                  </p>
                  <div className="execution-metrics">
                    {[
                      ["Entry", money(detail.proposal.entry)],
                      ["Stop", money(detail.proposal.stop)],
                      ["Target", money(detail.proposal.target)],
                      ["Shares", detail.proposal.qty],
                      [
                        "Estimated max loss",
                        money(detail.proposal.max_loss_estimate),
                      ],
                      [
                        "Net reward / risk",
                        Number.isFinite(detail.proposal.net_reward_risk)
                          ? detail.proposal.net_reward_risk.toFixed(2)
                          : "—",
                      ],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <span>{label}</span>
                        <b>{value}</b>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {detail.tick && (
                <section className="panel research-evidence">
                  <span className="eyebrow">
                    DATED TICK EVIDENCE AT SAVED CUTOFF
                  </span>
                  <h3>
                    {detail.tick.status === "VERIFIED"
                      ? `₹${detail.tick.supplied} · verified dated record`
                      : "Tick evidence needs verification"}
                  </h3>
                  <p>
                    {detail.tick.published_at
                      ? `Published / snapshot available: ${date(detail.tick.published_at)}.`
                      : ""}{" "}
                    Later sessions require applicable metadata.
                  </p>
                  {safeUrl(detail.tick.source) && (
                    <a
                      href={safeUrl(detail.tick.source)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open recorded source ↗
                    </a>
                  )}
                </section>
              )}
              <section className="research-findings">
                <div className="section-title">
                  <h2>Decisions and prerequisites</h2>
                  <span className="micro">
                    {detail.kind === "ASSESSMENT"
                      ? "COORDINATOR FINDINGS"
                      : "VALIDATED SAVED REVIEWS"}
                  </span>
                </div>
                {detail.findings.map((r, i) => (
                  <article className="panel" key={r.role + "-" + i}>
                    <div>
                      <h3>{r.role.replaceAll("_", " ")}</h3>
                      <span>
                        {String(r.status || "PENDING").replaceAll("_", " ")}
                      </span>
                    </div>
                    <p>{r.decision}</p>
                  </article>
                ))}
              </section>
              {detail.sources?.length > 0 && (
                <section className="panel research-evidence">
                  <span className="eyebrow">RECORDED ORIGINAL SOURCES</span>
                  {detail.sources
                    .filter((s) => safeUrl(s.url))
                    .map((s, i) => (
                      <a
                        href={safeUrl(s.url)}
                        target="_blank"
                        rel="noreferrer"
                        key={i}
                      >
                        {new URL(s.url).hostname} ↗
                        <small>
                          Published {s.published_date || date(s.published_at)} ·{" "}
                          {s.publication_timezone || "See recorded timestamp"}
                        </small>
                      </a>
                    ))}
                </section>
              )}
            </>
          ) : (
            <section className="panel empty">
              <h2>
                {selected
                  ? "Reading the saved findings…"
                  : "Research begins with evidence."}
              </h2>
              <p>Select a saved record or prepare a review for a company.</p>
            </section>
          )}
        </div>
      </div>
    </section>
  );
}
