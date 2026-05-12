import { useEffect, useRef, useState } from "react";

import type {
  Algorithm,
  LiveSession,
  LiveSessionSymbol,
  LiveTrade,
  LiveWsEvent,
} from "../lib/types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const WS_BASE =
  import.meta.env.VITE_WS_URL?.replace(/\/ws\/market$/, "") ??
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;

function fmt$(v: number) {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function fmtSigned$(v: number) {
  return `${v >= 0 ? "+" : ""}${fmt$(v)}`;
}
function fmtPct(v: number) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}
function pnlColor(v: number) {
  return v > 0 ? "#10b981" : v < 0 ? "#ef4444" : "#94a3b8";
}

// ────────────────────────────────────────────────────────────────────
// Component
// ────────────────────────────────────────────────────────────────────

export default function LiveTradingView() {
  // ── State: session list ──
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  // ── State: create form ──
  const [showCreate, setShowCreate] = useState(false);
  const [formName, setFormName] = useState("Paper Session");
  const [formSymbols, setFormSymbols] = useState<{ symbol: string; algorithm_id: string }[]>([
    { symbol: "", algorithm_id: "" },
  ]);
  const [formDefaultAlgo, setFormDefaultAlgo] = useState("");
  const [formCapital, setFormCapital] = useState(10000);
  const [formPositionSize, setFormPositionSize] = useState(1000);
  const [formMaxEntries, setFormMaxEntries] = useState(5);
  const [formMaxDailyLoss, setFormMaxDailyLoss] = useState(500);
  const [formOrderType, setFormOrderType] = useState<"market" | "limit">("market");
  const [algorithms, setAlgorithms] = useState<Algorithm[]>([]);

  // ── State: active session detail ──
  const [sessionDetail, setSessionDetail] = useState<{
    session: LiveSession;
    symbols: LiveSessionSymbol[];
    is_running: boolean;
    total_pnl: number | null;
    total_value: number | null;
  } | null>(null);
  const [trades, setTrades] = useState<LiveTrade[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // ── Load sessions + algorithms on mount ──
  useEffect(() => {
    refreshSessions();
    loadAlgorithms();
  }, []);

  async function refreshSessions() {
    try {
      const res = await fetch(`${API_BASE}/api/live/sessions`);
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions ?? []);
      }
    } catch { /* ignore */ }
  }

  async function loadAlgorithms() {
    try {
      const res = await fetch(`${API_BASE}/api/tick-backtest/algorithms`);
      if (res.ok) {
        const data = await res.json();
        const algos = (data.algorithms ?? []) as Algorithm[];
        // Deduplicate: keep latest version per name
        const byName = new Map<string, Algorithm>();
        for (const a of algos) {
          const existing = byName.get(a.name);
          if (!existing || a.version > existing.version) byName.set(a.name, a);
        }
        setAlgorithms(Array.from(byName.values()));
      }
    } catch { /* ignore */ }
  }

  // ── Create session ──
  async function handleCreate() {
    setActionLoading(true);
    setActionError(null);
    try {
      const symbols = formSymbols
        .filter((s) => s.symbol.trim())
        .map((s) => ({
          symbol: s.symbol.trim().toUpperCase(),
          algorithm_id: s.algorithm_id || formDefaultAlgo,
        }));
      if (symbols.length === 0) {
        setActionError("Add at least one symbol");
        return;
      }
      if (symbols.some((s) => !s.algorithm_id)) {
        setActionError("Select a strategy for each symbol or set a default");
        return;
      }

      const res = await fetch(`${API_BASE}/api/live/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: formName,
          symbols,
          default_algorithm_id: formDefaultAlgo || null,
          capital_per_symbol: formCapital,
          position_size: formPositionSize,
          max_entries: formMaxEntries,
          max_daily_loss: formMaxDailyLoss,
          order_type: formOrderType,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create session");
      }
      const data = await res.json();
      setShowCreate(false);
      await refreshSessions();
      setActiveSessionId(data.session.id);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  // ── Session actions ──
  async function doAction(action: "start" | "stop" | "kill", sessionId: string) {
    setActionLoading(true);
    setActionError(null);
    try {
      const res = await fetch(`${API_BASE}/api/live/sessions/${sessionId}/${action}`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || `Failed to ${action}`);
      }
      await refreshSessions();
      if (activeSessionId === sessionId) {
        await loadSessionDetail(sessionId);
      }
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  // ── Load session detail ──
  async function loadSessionDetail(sessionId: string) {
    try {
      const [detailRes, tradesRes] = await Promise.all([
        fetch(`${API_BASE}/api/live/sessions/${sessionId}`),
        fetch(`${API_BASE}/api/live/sessions/${sessionId}/trades`),
      ]);
      if (detailRes.ok) {
        const data = await detailRes.json();
        setSessionDetail(data);
      }
      if (tradesRes.ok) {
        const data = await tradesRes.json();
        setTrades(data.trades ?? []);
      }
    } catch { /* ignore */ }
  }

  // ── Select session ──
  useEffect(() => {
    if (activeSessionId) {
      loadSessionDetail(activeSessionId);
    } else {
      setSessionDetail(null);
      setTrades([]);
    }
  }, [activeSessionId]);

  // ── WebSocket for live updates ──
  useEffect(() => {
    if (!activeSessionId || !sessionDetail?.is_running) {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
        setWsConnected(false);
      }
      return;
    }
    const ws = new WebSocket(`${WS_BASE}/api/live/ws/${activeSessionId}`);
    wsRef.current = ws;

    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onerror = () => setWsConnected(false);

    ws.onmessage = (e) => {
      try {
        const evt: LiveWsEvent = JSON.parse(e.data);
        if (evt.type === "heartbeat") return;

        if (evt.type === "snapshot") {
          setSessionDetail((prev) =>
            prev
              ? {
                  ...prev,
                  symbols: prev.symbols.map((s) => {
                    const live = evt.symbols[s.symbol];
                    return live ? { ...s, ...live } : s;
                  }),
                  total_pnl: evt.total_pnl,
                  total_value: evt.total_value,
                }
              : prev,
          );
          return;
        }

        if (evt.type === "tick") {
          setSessionDetail((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              symbols: prev.symbols.map((s) =>
                s.symbol === evt.symbol
                  ? {
                      ...s,
                      last_price: evt.price,
                      unrealized_pnl: evt.unrealized_pnl,
                      realized_pnl: evt.realized_pnl,
                      cash_remaining: evt.cash,
                      portfolio_value: evt.portfolio_value,
                      current_shares: evt.position_shares,
                      tick_count: evt.tick_count,
                    }
                  : s,
              ),
            };
          });
          return;
        }

        if (evt.type === "trade") {
          // Prepend to trade list
          setTrades((prev) => [
            {
              id: `live-${Date.now()}`,
              symbol: evt.symbol,
              side: evt.side,
              order_type: "market",
              shares: evt.shares,
              price: evt.price,
              cost: evt.cost ?? evt.proceeds ?? 0,
              pnl: evt.pnl ?? null,
              pnl_pct: evt.pnl_pct ?? null,
              ibkr_order_id: null,
              status: "filled",
              created_at: evt.time,
            } satisfies LiveTrade,
            ...prev,
          ]);
          // Also refresh detail for position updates
          if (activeSessionId) loadSessionDetail(activeSessionId);
          return;
        }

        if (evt.type === "status") {
          if (evt.status === "stopped" || evt.status === "error") {
            refreshSessions();
            if (activeSessionId) loadSessionDetail(activeSessionId);
          }
        }
      } catch { /* ignore */ }
    };

    return () => {
      ws.close();
      wsRef.current = null;
      setWsConnected(false);
    };
  }, [activeSessionId, sessionDetail?.is_running]);

  // ── Form helpers ──
  function addSymbolRow() {
    setFormSymbols((prev) => [...prev, { symbol: "", algorithm_id: "" }]);
  }
  function removeSymbolRow(idx: number) {
    setFormSymbols((prev) => prev.filter((_, i) => i !== idx));
  }
  function updateSymbolRow(idx: number, field: "symbol" | "algorithm_id", value: string) {
    setFormSymbols((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)));
  }

  // ── Render ──
  return (
    <div className="backtest-view">
      {/* Header bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Paper Trading</h2>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {activeSessionId && (
            <button type="button" onClick={() => setActiveSessionId(null)} style={{ fontSize: "0.8rem" }}>
              ← Sessions
            </button>
          )}
          <button type="button" onClick={() => setShowCreate(!showCreate)} style={{ fontSize: "0.8rem" }}>
            {showCreate ? "Cancel" : "+ New Session"}
          </button>
        </div>
      </div>

      {actionError && <p className="error-banner" style={{ marginBottom: "0.5rem" }}>{actionError}</p>}

      {/* ── Create form ── */}
      {showCreate && (
        <div
          style={{
            background: "rgba(15, 23, 42, 0.95)",
            border: "1px solid rgba(148, 163, 184, 0.2)",
            borderRadius: "8px",
            padding: "1rem",
            marginBottom: "1rem",
          }}
        >
          <h3 style={{ margin: "0 0 0.75rem", fontSize: "0.95rem" }}>Create Session</h3>

          <div className="backtest-inputs" style={{ marginBottom: "0.75rem" }}>
            <label>
              Name
              <input value={formName} onChange={(e) => setFormName(e.target.value)} style={{ width: 180 }} />
            </label>
            <label>
              Order Type
              <select value={formOrderType} onChange={(e) => setFormOrderType(e.target.value as "market" | "limit")}>
                <option value="market">Market</option>
                <option value="limit">Limit</option>
              </select>
            </label>
            <label>
              Capital / Symbol
              <input type="number" min={100} value={formCapital} onChange={(e) => setFormCapital(Number(e.target.value))} style={{ width: 100 }} />
            </label>
            <label>
              Buy Size
              <input type="number" min={100} value={formPositionSize} onChange={(e) => setFormPositionSize(Number(e.target.value))} style={{ width: 100 }} />
            </label>
            <label>
              Max Entries
              <input type="number" min={1} max={100} value={formMaxEntries} onChange={(e) => setFormMaxEntries(Number(e.target.value))} style={{ width: 60 }} />
            </label>
            <label>
              Max Daily Loss
              <input type="number" min={0} value={formMaxDailyLoss} onChange={(e) => setFormMaxDailyLoss(Number(e.target.value))} style={{ width: 100 }} />
            </label>
          </div>

          {/* Default strategy */}
          <div style={{ marginBottom: "0.75rem" }}>
            <label style={{ fontSize: "0.8rem" }}>
              Default Strategy (applied when not set per symbol)
              <select
                value={formDefaultAlgo}
                onChange={(e) => setFormDefaultAlgo(e.target.value)}
                style={{ marginLeft: "0.5rem", minWidth: 200 }}
              >
                <option value="">— Select —</option>
                {algorithms.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} v{a.version}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* Symbols */}
          <div style={{ marginBottom: "0.75rem" }}>
            <div style={{ fontSize: "0.8rem", color: "#94a3b8", marginBottom: "0.35rem" }}>Symbols</div>
            {formSymbols.map((row, idx) => (
              <div key={idx} style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.3rem" }}>
                <input
                  type="text"
                  placeholder="AAPL"
                  value={row.symbol}
                  onChange={(e) => updateSymbolRow(idx, "symbol", e.target.value.toUpperCase())}
                  style={{ width: 80 }}
                />
                <select
                  value={row.algorithm_id}
                  onChange={(e) => updateSymbolRow(idx, "algorithm_id", e.target.value)}
                  style={{ minWidth: 180 }}
                >
                  <option value="">{formDefaultAlgo ? "(use default)" : "— Select strategy —"}</option>
                  {algorithms.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} v{a.version}
                    </option>
                  ))}
                </select>
                {formSymbols.length > 1 && (
                  <button type="button" onClick={() => removeSymbolRow(idx)} style={{ fontSize: "0.7rem", padding: "0.1rem 0.3rem" }}>
                    ✕
                  </button>
                )}
              </div>
            ))}
            <button type="button" onClick={addSymbolRow} style={{ fontSize: "0.75rem", marginTop: "0.25rem" }}>
              + Add symbol
            </button>
          </div>

          <button type="button" onClick={handleCreate} disabled={actionLoading} style={{ fontWeight: 600 }}>
            {actionLoading ? "Creating…" : "Create Session"}
          </button>
        </div>
      )}

      {/* ── Session list ── */}
      {!activeSessionId && !showCreate && (
        <div>
          {sessions.length === 0 ? (
            <p style={{ color: "#94a3b8" }}>No sessions yet. Click "+ New Session" to get started.</p>
          ) : (
            <div className="backtest-trades backtest-batch-results">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Status</th>
                    <th>Order Type</th>
                    <th>Created</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id} style={{ cursor: "pointer" }}>
                      <td onClick={() => setActiveSessionId(s.id)} style={{ fontWeight: 600 }}>
                        {s.name}
                      </td>
                      <td onClick={() => setActiveSessionId(s.id)}>
                        <span
                          style={{
                            color:
                              s.status === "running" || s.is_running
                                ? "#10b981"
                                : s.status === "error"
                                  ? "#ef4444"
                                  : "#94a3b8",
                            fontWeight: 600,
                          }}
                        >
                          {s.is_running ? "● running" : s.status}
                        </span>
                      </td>
                      <td onClick={() => setActiveSessionId(s.id)}>{s.order_type}</td>
                      <td onClick={() => setActiveSessionId(s.id)}>
                        {s.created_at ? new Date(s.created_at).toLocaleString() : "—"}
                      </td>
                      <td>
                        {(s.status === "created" || s.status === "stopped") && (
                          <button type="button" onClick={() => doAction("start", s.id)} disabled={actionLoading} style={{ fontSize: "0.75rem", marginRight: "0.3rem" }}>
                            ▶ Start
                          </button>
                        )}
                        {s.is_running && (
                          <>
                            <button type="button" onClick={() => doAction("stop", s.id)} disabled={actionLoading} style={{ fontSize: "0.75rem", marginRight: "0.3rem" }}>
                              ■ Stop
                            </button>
                            <button
                              type="button"
                              onClick={() => doAction("kill", s.id)}
                              disabled={actionLoading}
                              style={{ fontSize: "0.75rem", background: "#991b1b", color: "#fca5a5", border: "1px solid #ef4444" }}
                            >
                              ⚠ Kill
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Active session detail ── */}
      {activeSessionId && sessionDetail && (
        <div>
          {/* Status bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "0.5rem 0.75rem",
              background: sessionDetail.is_running ? "rgba(16, 185, 129, 0.1)" : "rgba(148, 163, 184, 0.08)",
              borderRadius: "8px",
              marginBottom: "0.75rem",
              border: `1px solid ${sessionDetail.is_running ? "rgba(16, 185, 129, 0.3)" : "rgba(148, 163, 184, 0.15)"}`,
            }}
          >
            <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
              <span style={{ fontWeight: 700 }}>{sessionDetail.session.name}</span>
              <span
                style={{
                  color: sessionDetail.is_running ? "#10b981" : sessionDetail.session.status === "error" ? "#ef4444" : "#94a3b8",
                  fontWeight: 600,
                  fontSize: "0.85rem",
                }}
              >
                {sessionDetail.is_running ? "● RUNNING" : sessionDetail.session.status.toUpperCase()}
              </span>
              {wsConnected && <span style={{ fontSize: "0.7rem", color: "#10b981" }}>WS ●</span>}
              {sessionDetail.session.started_at && (
                <span style={{ fontSize: "0.75rem", color: "#64748b" }}>
                  Started: {new Date(sessionDetail.session.started_at).toLocaleString()}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              {(sessionDetail.session.status === "created" || sessionDetail.session.status === "stopped") && (
                <button type="button" onClick={() => doAction("start", activeSessionId)} disabled={actionLoading}>
                  ▶ Start
                </button>
              )}
              {sessionDetail.is_running && (
                <>
                  <button type="button" onClick={() => doAction("stop", activeSessionId)} disabled={actionLoading}>
                    ■ Stop
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm("Kill session? This will cancel ALL open orders.")) {
                        doAction("kill", activeSessionId);
                      }
                    }}
                    disabled={actionLoading}
                    style={{ background: "#991b1b", color: "#fca5a5", border: "1px solid #ef4444", fontWeight: 700 }}
                  >
                    ⚠ KILL
                  </button>
                </>
              )}
            </div>
          </div>

          {sessionDetail.session.error_message && (
            <p className="error-banner" style={{ marginBottom: "0.5rem" }}>{sessionDetail.session.error_message}</p>
          )}

          {/* Portfolio summary */}
          {sessionDetail.total_pnl !== null && (
            <div className="backtest-summary" style={{ marginBottom: "0.75rem" }}>
              <div>
                <span>Total P&L</span>
                <strong style={{ color: pnlColor(sessionDetail.total_pnl ?? 0) }}>
                  {fmtSigned$(sessionDetail.total_pnl ?? 0)}
                </strong>
              </div>
              <div>
                <span>Portfolio Value</span>
                <strong>{fmt$(sessionDetail.total_value ?? 0)}</strong>
              </div>
              <div>
                <span>Order Type</span>
                <strong>{sessionDetail.session.order_type}</strong>
              </div>
              <div>
                <span>Max Daily Loss</span>
                <strong>{fmt$(sessionDetail.session.max_daily_loss)}</strong>
              </div>
            </div>
          )}

          {/* Symbol cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "0.75rem", marginBottom: "0.75rem" }}>
            {sessionDetail.symbols.map((s) => {
              const unrealized = s.unrealized_pnl ?? 0;
              const realized = s.realized_pnl ?? 0;
              const hasPosition = (s.current_shares ?? 0) > 0;
              const portfolioVal = s.portfolio_value ?? s.cash_remaining + (s.current_shares * (s.last_price ?? 0));
              return (
                <div
                  key={s.id}
                  style={{
                    background: "rgba(15, 23, 42, 0.9)",
                    border: "1px solid rgba(148, 163, 184, 0.15)",
                    borderRadius: "8px",
                    padding: "0.75rem",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.5rem" }}>
                    <span style={{ fontWeight: 700, fontSize: "1rem" }}>{s.symbol}</span>
                    <span style={{ fontSize: "1rem", fontWeight: 600 }}>
                      {s.last_price != null ? fmt$(s.last_price) : "—"}
                    </span>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.25rem 1rem", fontSize: "0.8rem" }}>
                    <div>
                      <span style={{ color: "#64748b" }}>Position</span>
                      <div style={{ fontWeight: 600 }}>
                        {hasPosition ? `${(s.current_shares).toFixed(2)} sh @ ${fmt$(s.avg_price ?? (s.current_cost / s.current_shares))}` : "None"}
                      </div>
                    </div>
                    <div>
                      <span style={{ color: "#64748b" }}>Cash</span>
                      <div style={{ fontWeight: 600 }}>{fmt$(s.cash_remaining)}</div>
                    </div>
                    <div>
                      <span style={{ color: "#64748b" }}>Unrealized P&L</span>
                      <div style={{ fontWeight: 600, color: pnlColor(unrealized) }}>
                        {hasPosition ? fmtSigned$(unrealized) : "—"}
                      </div>
                    </div>
                    <div>
                      <span style={{ color: "#64748b" }}>Realized P&L</span>
                      <div style={{ fontWeight: 600, color: pnlColor(realized) }}>
                        {fmtSigned$(realized)}
                      </div>
                    </div>
                    <div>
                      <span style={{ color: "#64748b" }}>Daily P&L</span>
                      <div style={{ fontWeight: 600, color: pnlColor(s.daily_realized_pnl ?? 0) }}>
                        {fmtSigned$(s.daily_realized_pnl ?? 0)}
                      </div>
                    </div>
                    <div>
                      <span style={{ color: "#64748b" }}>Portfolio</span>
                      <div style={{ fontWeight: 600 }}>{fmt$(portfolioVal)}</div>
                    </div>
                    {s.tick_count != null && (
                      <div style={{ gridColumn: "1 / -1", color: "#475569", fontSize: "0.7rem", marginTop: "0.25rem" }}>
                        {s.tick_count.toLocaleString()} ticks
                        {s.last_tick_time && ` · last: ${new Date(s.last_tick_time).toLocaleTimeString()}`}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Trade log */}
          <div className="backtest-trades backtest-batch-results">
            <h3 style={{ margin: "0 0 0.5rem", fontSize: "0.9rem" }}>Trade Log</h3>
            {trades.length === 0 ? (
              <p style={{ color: "#94a3b8", fontSize: "0.85rem" }}>No trades yet.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Shares</th>
                    <th>Price</th>
                    <th>Cost</th>
                    <th>P&L</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id}>
                      <td>{t.created_at ? new Date(t.created_at).toLocaleString() : "—"}</td>
                      <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                      <td style={{ color: t.side === "buy" ? "#10b981" : "#ef4444", fontWeight: 600 }}>
                        {t.side.toUpperCase()}
                      </td>
                      <td>{t.shares.toFixed(4)}</td>
                      <td>{fmt$(t.price)}</td>
                      <td>{fmt$(t.cost)}</td>
                      <td style={{ color: pnlColor(t.pnl ?? 0) }}>
                        {t.pnl != null ? `${fmtSigned$(t.pnl)} (${fmtPct(t.pnl_pct ?? 0)})` : "—"}
                      </td>
                      <td>{t.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
