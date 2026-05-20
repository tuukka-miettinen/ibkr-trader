import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";

import MiniSymbolChart, { type MiniCandle } from "./MiniSymbolChart";
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

const POSITIVE_COLOR = "#15803d";
const NEGATIVE_COLOR = "#b91c1c";
const NEUTRAL_COLOR = "#5a5a5a";

function pnlColor(v: number) {
  return v > 0 ? POSITIVE_COLOR : v < 0 ? NEGATIVE_COLOR : NEUTRAL_COLOR;
}

// ────────────────────────────────────────────────────────────────────
// Component
// ────────────────────────────────────────────────────────────────────

export default function LiveTradingView({ initialSessionId }: { initialSessionId?: string } = {}) {
  const [, navigate] = useLocation();
  // ── State: session list ──
  const [sessions, setSessions] = useState<LiveSession[]>([]);
  const activeSessionId = initialSessionId ?? null;

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
  const [formMaxDailyEntries, setFormMaxDailyEntries] = useState(10);
  const [formMaxTotalExposure, setFormMaxTotalExposure] = useState(50000);
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
  const [symbolCandles, setSymbolCandles] = useState<Record<string, MiniCandle[]>>({});
  const symbolCandlesRef = useRef<Record<string, MiniCandle[]>>({});
  const candleSyncTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [symbolTestResults, setSymbolTestResults] = useState<Record<string, { ok: boolean; exchange?: string; last_price?: number; error?: string; note?: string } | "loading">>({});
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const [accountInfo, setAccountInfo] = useState<{ ok: boolean; net_liquidation?: number; total_cash?: number; buying_power?: number; error?: string } | null>(null);

  // ── Load sessions + favorite algorithms on mount ──
  useEffect(() => {
    refreshSessions();
    loadAlgorithms();
    loadAccountInfo();
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
      const res = await fetch(`${API_BASE}/api/tick-backtest/algorithms/favorites`);
      if (res.ok) {
        const data = await res.json();
        const algos = (data.algorithms ?? []) as Algorithm[];
        // Deduplicate: keep latest favorite version per name
        const byName = new Map<string, Algorithm>();
        for (const a of algos) {
          const existing = byName.get(a.name);
          if (!existing || a.version > existing.version) byName.set(a.name, a);
        }
        setAlgorithms(Array.from(byName.values()));
      } else {
        setAlgorithms([]);
      }
    } catch {
      setAlgorithms([]);
    }
  }

  async function loadAccountInfo() {
    try {
      const res = await fetch(`${API_BASE}/api/live/account`);
      if (res.ok) {
        const data = await res.json();
        setAccountInfo(data);
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
          max_daily_entries: formMaxDailyEntries,
          max_daily_loss: formMaxDailyLoss,
          max_total_exposure: formMaxTotalExposure,
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
      navigate(`/paper-trading/${data.session.id}`);
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

  async function cloneSession(sessionId: string) {
    setActionLoading(true);
    setActionError(null);
    try {
      const res = await fetch(`${API_BASE}/api/live/sessions/${sessionId}/clone`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to clone session");
      }
      const data = await res.json();
      await refreshSessions();
      navigate(`/paper-trading/${data.session.id}`);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function renameSession(sessionId: string, currentName: string) {
    const nextName = prompt("Rename session", currentName)?.trim();
    if (!nextName || nextName === currentName) return;

    setActionLoading(true);
    setActionError(null);
    try {
      const res = await fetch(`${API_BASE}/api/live/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nextName }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to rename session");
      }

      setSessions((prev) => prev.map((s) => (s.id === sessionId ? { ...s, name: data.session.name } : s)));
      setSessionDetail((prev) => (
        prev && prev.session.id === sessionId
          ? { ...prev, session: { ...prev.session, name: data.session.name } }
          : prev
      ));
      await refreshSessions();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(false);
    }
  }

  // ── Load session detail ──
  async function loadSessionDetail(sessionId: string, { skipCandles = false } = {}) {
    try {
      const [detailRes, tradesRes] = await Promise.all([
        fetch(`${API_BASE}/api/live/sessions/${sessionId}`),
        fetch(`${API_BASE}/api/live/sessions/${sessionId}/trades`),
      ]);
      let symbols: LiveSessionSymbol[] = [];
      if (detailRes.ok) {
        const data = await detailRes.json();
        setSessionDetail(data);
        symbols = data.symbols ?? [];
      }
      if (tradesRes.ok) {
        const data = await tradesRes.json();
        setTrades(data.trades ?? []);
      }

      if (skipCandles) return;

      // Fetch candles for each symbol: historical 1m candles + any live aggregator candles
      const candleMap: Record<string, MiniCandle[]> = {};
      await Promise.all(
        symbols.map(async (s) => {
          try {
            // Fetch historical 1m candles
            const [histRes, liveRes] = await Promise.all([
              fetch(`${API_BASE}/api/candles?symbol=${s.symbol}&timeframe=1m&limit=120`),
              fetch(`${API_BASE}/api/live/sessions/${sessionId}/candles/${s.symbol}`),
            ]);
            const histCandles: MiniCandle[] = [];
            if (histRes.ok) {
              const data = await histRes.json();
              for (const c of data.candles ?? []) {
                histCandles.push({
                  time: c.time,
                  open: c.open,
                  high: c.high,
                  low: c.low,
                  close: c.close,
                  volume: c.volume,
                });
              }
            }
            // Live aggregator candles (from session runtime)
            let liveCandles: MiniCandle[] = [];
            if (liveRes.ok) {
              const data = await liveRes.json();
              liveCandles = data.candles ?? [];
            }
            // Merge: use historical as base, append any live candles with timestamps beyond the last historical
            const lastHistTime = histCandles.length > 0 ? histCandles[histCandles.length - 1].time : "";
            const merged = [...histCandles];
            for (const lc of liveCandles) {
              if (lc.time > lastHistTime) {
                merged.push(lc);
              }
            }
            if (merged.length > 0) {
              candleMap[s.symbol] = merged;
            }
          } catch { /* ignore */ }
        }),
      );
      // Only set candles for symbols that returned data; preserve existing tick data for others
      if (Object.keys(candleMap).length > 0) {
        symbolCandlesRef.current = { ...symbolCandlesRef.current, ...candleMap };
        setSymbolCandles({ ...symbolCandlesRef.current });
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
      setSymbolCandles({});
      symbolCandlesRef.current = {};
    }
  }, [activeSessionId]);

  // ── Sync candle ref → state when chart is expanded ──
  useEffect(() => {
    if (!expandedSymbol) {
      if (candleSyncTimerRef.current) {
        clearInterval(candleSyncTimerRef.current);
        candleSyncTimerRef.current = null;
      }
      return;
    }
    // Immediate sync
    setSymbolCandles({ ...symbolCandlesRef.current });
    // Periodic sync every 2s while chart is open
    candleSyncTimerRef.current = setInterval(() => {
      setSymbolCandles({ ...symbolCandlesRef.current });
    }, 2000);
    return () => {
      if (candleSyncTimerRef.current) {
        clearInterval(candleSyncTimerRef.current);
        candleSyncTimerRef.current = null;
      }
    };
  }, [expandedSymbol]);

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
          // Update stats
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
          // Append 5s candle to ref (avoid expensive state spread on every tick)
          if (evt.open != null && evt.high != null && evt.low != null && evt.close != null) {
            const prev = symbolCandlesRef.current[evt.symbol] ?? [];
            symbolCandlesRef.current[evt.symbol] = [
              ...prev,
              {
                time: evt.time,
                open: evt.open,
                high: evt.high,
                low: evt.low,
                close: evt.close,
                volume: evt.volume ?? 0,
              },
            ];
          }
          return;
        }

        if (evt.type === "candle") {
          // Candle events are now redundant — tick events provide 5s chart data.
          // Appending both would create out-of-order timestamps.
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
          // Also refresh detail for position updates (skip candles to preserve tick data)
          if (activeSessionId) loadSessionDetail(activeSessionId, { skipCandles: true });
          return;
        }

        if (evt.type === "error") {
          setActionError(`IBKR: ${evt.message}`);
          return;
        }

        if (evt.type === "status") {
          if (evt.status === "stopped" || evt.status === "error") {
            refreshSessions();
            if (activeSessionId) loadSessionDetail(activeSessionId, { skipCandles: true });
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

  async function testSymbolConnection(symbol: string) {
    if (!symbol.trim()) return;
    const sym = symbol.trim().toUpperCase();
    setSymbolTestResults((prev) => ({ ...prev, [sym]: "loading" }));
    try {
      const res = await fetch(`${API_BASE}/api/live/test-symbol/${encodeURIComponent(sym)}`);
      if (res.ok) {
        const data = await res.json();
        setSymbolTestResults((prev) => ({ ...prev, [sym]: data }));
      } else {
        setSymbolTestResults((prev) => ({ ...prev, [sym]: { ok: false, error: "API error" } }));
      }
    } catch {
      setSymbolTestResults((prev) => ({ ...prev, [sym]: { ok: false, error: "Network error" } }));
    }
  }

  // ── Render ──
  return (
    <div className="backtest-view">
      {/* Header bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Paper Trading</h2>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {activeSessionId && (
            <button type="button" onClick={() => navigate("/paper-trading")} style={{ fontSize: "0.8rem" }}>
              ← Sessions
            </button>
          )}
          <button type="button" onClick={() => setShowCreate(!showCreate)} style={{ fontSize: "0.8rem" }}>
            {showCreate ? "Cancel" : "+ New Session"}
          </button>
        </div>
      </div>

      {actionError && <p className="error-banner" style={{ marginBottom: "0.5rem" }}>{actionError}</p>}

      {/* ── Account summary bar ── */}
      {accountInfo && accountInfo.ok && (
        <div
          style={{
            display: "flex",
            gap: "1.5rem",
            alignItems: "center",
            padding: "0.4rem 0.75rem",
            background: "#f3f3f3",
            border: "1px solid #000000",
            marginBottom: "0.75rem",
            fontSize: "0.85rem",
          }}
        >
          <span style={{ fontWeight: 700, color: "#5a5a5a", fontSize: "0.75rem" }}>IBKR ACCOUNT</span>
          {accountInfo.net_liquidation != null && (
            <span>
              <span style={{ color: "#5a5a5a" }}>Net Liq: </span>
              <strong>{fmt$(accountInfo.net_liquidation)}</strong>
            </span>
          )}
          {accountInfo.total_cash != null && (
            <span>
              <span style={{ color: "#5a5a5a" }}>Cash: </span>
              <strong>{fmt$(accountInfo.total_cash)}</strong>
            </span>
          )}
          {accountInfo.buying_power != null && (
            <span>
              <span style={{ color: "#5a5a5a" }}>Buying Power: </span>
              <strong>{fmt$(accountInfo.buying_power)}</strong>
            </span>
          )}
          <button
            type="button"
            onClick={loadAccountInfo}
            style={{ fontSize: "0.7rem", padding: "0.1rem 0.4rem", marginLeft: "auto" }}
          >
            ↻ Refresh
          </button>
        </div>
      )}

      {/* ── Create form ── */}
      {showCreate && (
        <div
          style={{
            background: "#ffffff",
            border: "1px solid #000000",
            borderRadius: "0",
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
              Daily Entries / Strategy
              <input type="number" min={1} max={1000} value={formMaxDailyEntries} onChange={(e) => setFormMaxDailyEntries(Number(e.target.value))} style={{ width: 60 }} />
            </label>
            <label>
              Max Total Exposure
              <input type="number" min={100} value={formMaxTotalExposure} onChange={(e) => setFormMaxTotalExposure(Number(e.target.value))} style={{ width: 100 }} />
            </label>
            <label>
              Max Daily Loss
              <input type="number" min={0} value={formMaxDailyLoss} onChange={(e) => setFormMaxDailyLoss(Number(e.target.value))} style={{ width: 100 }} />
            </label>
          </div>

          {/* Default strategy */}
          <div style={{ marginBottom: "0.75rem" }}>
            <label style={{ fontSize: "0.8rem" }}>
              Default Strategy (favorites only)
              <select
                value={formDefaultAlgo}
                onChange={(e) => setFormDefaultAlgo(e.target.value)}
                style={{ marginLeft: "0.5rem", minWidth: 200 }}
                disabled={algorithms.length === 0}
              >
                <option value="">— Select —</option>
                {algorithms.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} v{a.version}
                  </option>
                ))}
              </select>
            </label>
            {algorithms.length === 0 && (
              <div style={{ fontSize: "0.75rem", color: "#707070", marginTop: "0.35rem" }}>
                No favorite strategies yet. In Tick Backtest, run a strategy and add it to favorites first.
              </div>
            )}
          </div>

          {/* Symbols */}
          <div style={{ marginBottom: "0.75rem" }}>
            <div style={{ fontSize: "0.8rem", color: "#5a5a5a", marginBottom: "0.35rem" }}>Symbols</div>
            {formSymbols.map((row, idx) => (
              <div key={idx} style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.3rem", flexWrap: "wrap" }}>
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
                  disabled={algorithms.length === 0}
                >
                  <option value="">{formDefaultAlgo ? "(use default)" : "— Select strategy —"}</option>
                  {algorithms.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} v{a.version}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => testSymbolConnection(row.symbol)}
                  disabled={!row.symbol.trim() || symbolTestResults[row.symbol.toUpperCase()] === "loading"}
                  style={{ fontSize: "0.7rem", padding: "0.15rem 0.4rem" }}
                >
                  {symbolTestResults[row.symbol.toUpperCase()] === "loading" ? "Testing…" : "Test"}
                </button>
                {(() => {
                  const r = symbolTestResults[row.symbol.toUpperCase()];
                  if (!r || r === "loading") return null;
                  if (r.ok) {
                    return (
                      <span style={{ fontSize: "0.7rem", color: r.note ? NEUTRAL_COLOR : POSITIVE_COLOR }}>
                        {r.note ? "⚠" : "✓"} {r.exchange}{r.last_price != null ? ` · $${r.last_price}` : ""}
                        {r.note ? ` — ${r.note}` : ""}
                      </span>
                    );
                  }
                  return <span style={{ fontSize: "0.7rem", color: NEGATIVE_COLOR }}>✗ {r.error}</span>;
                })()}
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
            <p style={{ color: "#5a5a5a" }}>No sessions yet. Click "+ New Session" to get started.</p>
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
                      <td onClick={() => navigate(`/paper-trading/${s.id}`)} style={{ fontWeight: 600 }}>
                        {s.name}
                      </td>
                      <td onClick={() => navigate(`/paper-trading/${s.id}`)}>
                        <span
                          style={{
                            color:
                              s.status === "running" || s.is_running
                                ? POSITIVE_COLOR
                                : s.status === "error"
                                  ? NEGATIVE_COLOR
                                  : NEUTRAL_COLOR,
                            fontWeight: 600,
                          }}
                        >
                          {s.is_running ? "● running" : s.status}
                        </span>
                      </td>
                      <td onClick={() => navigate(`/paper-trading/${s.id}`)}>{s.order_type}</td>
                      <td onClick={() => navigate(`/paper-trading/${s.id}`)}>
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
                              style={{ fontSize: "0.75rem", background: "#ffffff", color: NEGATIVE_COLOR, border: `1px solid ${NEGATIVE_COLOR}` }}
                            >
                              ⚠ Kill
                            </button>
                          </>
                        )}
                        <button type="button" onClick={() => renameSession(s.id, s.name)} disabled={actionLoading} style={{ fontSize: "0.75rem", marginLeft: "0.3rem" }}>
                          ✎ Rename
                        </button>
                        <button type="button" onClick={() => cloneSession(s.id)} disabled={actionLoading} style={{ fontSize: "0.75rem", marginLeft: "0.3rem" }}>
                          ⧉ Copy
                        </button>
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
              background: sessionDetail.is_running ? "#f3f3f3" : "#f3f3f3",
              borderRadius: "0",
              marginBottom: "0.75rem",
              border: `1px solid ${sessionDetail.is_running ? "#000000" : "#000000"}`,
            }}
          >
            <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
              <span style={{ fontWeight: 700 }}>{sessionDetail.session.name}</span>
              <span
                style={{
                  color: sessionDetail.is_running ? POSITIVE_COLOR : sessionDetail.session.status === "error" ? NEGATIVE_COLOR : NEUTRAL_COLOR,
                  fontWeight: 600,
                  fontSize: "0.85rem",
                }}
              >
                {sessionDetail.is_running ? "● RUNNING" : sessionDetail.session.status.toUpperCase()}
              </span>
              {wsConnected && <span style={{ fontSize: "0.7rem", color: POSITIVE_COLOR }}>WS ●</span>}
              {sessionDetail.session.started_at && (
                <span style={{ fontSize: "0.75rem", color: "#707070" }}>
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
                    style={{ background: "#ffffff", color: NEGATIVE_COLOR, border: `1px solid ${NEGATIVE_COLOR}`, fontWeight: 700 }}
                  >
                    ⚠ KILL
                  </button>
                </>
              )}
              <button type="button" onClick={() => renameSession(activeSessionId, sessionDetail.session.name)} disabled={actionLoading}>
                ✎ Rename
              </button>
              <button type="button" onClick={() => cloneSession(activeSessionId)} disabled={actionLoading}>
                ⧉ Copy
              </button>
            </div>
          </div>

          {sessionDetail.session.error_message && (
            <p className="error-banner" style={{ marginBottom: "0.5rem" }}>{sessionDetail.session.error_message}</p>
          )}

          {/* Portfolio summary */}
          {sessionDetail.total_pnl !== null && (
            <div className="backtest-summary live-session-summary" style={{ marginBottom: "0.75rem" }}>
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
                <span>Max Total Exposure</span>
                <strong>{fmt$(sessionDetail.session.max_total_exposure)}</strong>
              </div>
              <div>
                <span>Max Daily Loss</span>
                <strong>{fmt$(sessionDetail.session.max_daily_loss)}</strong>
              </div>
            </div>
          )}

          {/* Symbol grid */}
          <div className="backtest-trades backtest-batch-results" style={{ marginBottom: "0.75rem" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Ticker</th>
                  <th style={{ textAlign: "right" }}>Price</th>
                  <th style={{ textAlign: "right" }}>Position</th>
                  <th style={{ textAlign: "right" }}>Cash</th>
                  <th style={{ textAlign: "right" }}>Unreal. P&L</th>
                  <th style={{ textAlign: "right" }}>Real. P&L</th>
                  <th style={{ textAlign: "right" }}>Daily P&L</th>
                  <th style={{ textAlign: "right" }}>Portfolio</th>
                  <th style={{ textAlign: "center" }}>B/S/T</th>
                  <th style={{ textAlign: "center" }}>Daily</th>
                </tr>
              </thead>
              <tbody>
                {[...sessionDetail.symbols].sort((a, b) => a.symbol.localeCompare(b.symbol)).map((s) => {
                  const unrealized = s.unrealized_pnl ?? 0;
                  const realized = s.realized_pnl ?? 0;
                  const hasPosition = (s.current_shares ?? 0) > 0;
                  const portfolioVal = s.portfolio_value ?? s.cash_remaining + (s.current_shares * (s.last_price ?? 0));
                  const isExpanded = expandedSymbol === s.symbol;
                  const symTrades = trades.filter((t) => t.symbol === s.symbol);
                  const buys = symTrades.filter((t) => t.side === "buy").length;
                  const sells = symTrades.filter((t) => t.side === "sell").length;
                  return (
                    <tr key={s.id} style={{ cursor: "pointer" }} onClick={() => setExpandedSymbol(isExpanded ? null : s.symbol)}>
                      <td style={{ fontWeight: 700 }}>
                        <span style={{ marginRight: "0.35rem", fontSize: "0.7rem", color: "#707070" }}>{isExpanded ? "▼" : "▶"}</span>
                        {s.symbol}
                        {s.delayed && (
                          <span
                            style={{
                              marginLeft: "0.4rem",
                              fontSize: "0.6rem",
                              fontWeight: 600,
                              background: "#f3f3f3",
                              color: "#000000",
                              padding: "0.05rem 0.25rem",
                              borderRadius: "0",
                              verticalAlign: "middle",
                            }}
                          >
                            DELAYED
                          </span>
                        )}
                      </td>
                      <td style={{ textAlign: "right", fontWeight: 600 }}>
                        {s.last_price != null ? fmt$(s.last_price) : "—"}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {hasPosition
                          ? `${s.current_shares.toFixed(0)} @ ${fmt$(s.avg_price ?? (s.current_cost / s.current_shares))}`
                          : "—"}
                      </td>
                      <td style={{ textAlign: "right" }}>{fmt$(s.cash_remaining)}</td>
                      <td style={{ textAlign: "right", color: pnlColor(unrealized), fontWeight: 600 }}>
                        {hasPosition ? fmtSigned$(unrealized) : "—"}
                      </td>
                      <td style={{ textAlign: "right", color: pnlColor(realized), fontWeight: 600 }}>
                        {fmtSigned$(realized)}
                      </td>
                      <td style={{ textAlign: "right", color: pnlColor(s.daily_realized_pnl ?? 0), fontWeight: 600 }}>
                        {fmtSigned$(s.daily_realized_pnl ?? 0)}
                      </td>
                      <td style={{ textAlign: "right", fontWeight: 600 }}>
                        {fmt$(portfolioVal)}
                      </td>
                      <td style={{ textAlign: "center", fontWeight: 600, fontSize: "0.85rem" }}>
                        <span style={{ color: POSITIVE_COLOR }}>{buys}</span>
                        <span style={{ color: "#707070" }}>/</span>
                        <span style={{ color: NEGATIVE_COLOR }}>{sells}</span>
                        <span style={{ color: "#707070" }}>/</span>
                        <span style={{ color: "#000000" }}>{symTrades.length}</span>
                      </td>
                      <td style={{ textAlign: "center", fontSize: "0.85rem" }}>
                        <span style={{ fontWeight: 600 }}>{s.daily_entry_count ?? 0}</span>
                        <span style={{ color: "#707070" }}>/{s.max_daily_entries ?? "∞"}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Expanded chart accordion */}
          {expandedSymbol && sessionDetail.symbols.some((s) => s.symbol === expandedSymbol) && (
            <div
              style={{
                background: "#ffffff",
                border: "1px solid #000000",
                borderRadius: "0",
                padding: "0.75rem",
                marginBottom: "0.75rem",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <span style={{ fontWeight: 700, fontSize: "1rem" }}>
                  {expandedSymbol}
                  {(() => {
                    const s = sessionDetail.symbols.find((sym) => sym.symbol === expandedSymbol);
                    return s?.last_price != null ? (
                      <span style={{ fontWeight: 600, marginLeft: "0.5rem", color: "#000000" }}>
                        {fmt$(s.last_price)}
                      </span>
                    ) : null;
                  })()}
                </span>
                <button
                  type="button"
                  onClick={() => setExpandedSymbol(null)}
                  style={{ fontSize: "0.75rem", padding: "0.15rem 0.4rem" }}
                >
                  ✕ Close
                </button>
              </div>
              <MiniSymbolChart
                key={expandedSymbol}
                candles={symbolCandles[expandedSymbol] ?? []}
                trades={trades}
                symbol={expandedSymbol}
                sessionStartTime={sessionDetail.session.started_at}
              />
            </div>
          )}

          {/* Trade log */}
          <div className="backtest-trades backtest-batch-results">
            <h3 style={{ margin: "0 0 0.5rem", fontSize: "0.9rem" }}>Trade Log</h3>
            {trades.length === 0 ? (
              <p style={{ color: "#5a5a5a", fontSize: "0.85rem" }}>No trades yet.</p>
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
                      <td style={{ color: t.side === "buy" ? POSITIVE_COLOR : NEGATIVE_COLOR, fontWeight: 600 }}>
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
