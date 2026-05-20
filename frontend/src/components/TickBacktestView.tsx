import { useEffect, useState } from "react";
import Editor from "@monaco-editor/react";

import type { Algorithm, BacktestRunSummary } from "../lib/types";
import BacktestChart, { type PricePoint, type TradeData, type TradeEntry } from "./BacktestChart";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

type DailySummary = {
  date: string;
  num_trades: number;
  total_pnl: number;
  total_dollar_pnl: number;
  total_pnl_pct: number;
  win_rate: number;
  avg_trade_pct: number;
  day_buys: number;
  day_sells: number;
  unrealized_pnl: number;
  position_shares: number;
  position_cost: number;
  day_close_price: number;
};

type DataStatus = {
  symbol: string;
  total_chunks: number;
  dates: { date: string; chunks: number; complete: boolean }[];
  range: { start: string | null; end: string | null };
};

const DEFAULT_TICK_SCRIPT = `STRATEGY_NAME = "unnamed"

# Tick-level strategy: called once for every 5-second bar.
#
# state.tick          — current 5s Candle (.time, .open, .high, .low, .close, .volume)
# state.candles       — dict[Timeframe, list[Candle]]  completed higher-TF candles
# state.current_candles — dict[Timeframe, Candle|None]  in-progress candles
# state.closed        — dict[Timeframe, Candle|None]   candle that just closed this tick
# state.position      — PositionInfo|None  (.shares, .avg_price, .unrealized_pnl)
# state.cash          — available cash
# state.portfolio_value — cash + market value
#
# Return {"signal": "buy"} or {"signal": "sell"} or {"signal": None}

def on_tick(state):
    # Example: buy when a 5m candle closes with RSI crossing above 30
    closed_5m = state.closed.get("5m")
    if closed_5m is None:
        return {"signal": None}

    candles_5m = state.candles.get("5m", [])
    if len(candles_5m) < 15:
        return {"signal": None}

    rsi = ta.rsi(candles_5m, 14)

    if state.position is None:
        if rsi[-2] is not None and rsi[-2] <= 30 and rsi[-1] is not None and rsi[-1] > 30:
            return {"signal": "buy"}
    else:
        if rsi[-2] is not None and rsi[-2] >= 70 and rsi[-1] is not None and rsi[-1] < 70:
            return {"signal": "sell"}

    return {"signal": None}
`;

function formatCurrency(v: number) {
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function formatSignedCurrency(v: number) {
  return `${v >= 0 ? "+" : ""}${formatCurrency(v)}`;
}
function formatSigned(v: number, suffix = "") {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}${suffix}`;
}
function dateInputValue(input: Date) {
  const year = input.getFullYear();
  const month = String(input.getMonth() + 1).padStart(2, "0");
  const day = String(input.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function todayInputValue() {
  return dateInputValue(new Date());
}

function daysAgoInputValue(daysAgo: number) {
  const now = new Date();
  now.setDate(now.getDate() - daysAgo);
  return dateInputValue(now);
}

const STORAGE_KEY = "tick-backtest-last-strategy";

function loadLastStrategy(): { name: string; script: string } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return null;
}

function saveLastStrategy(name: string, script: string) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ name, script }));
  } catch { /* ignore */ }
}

export default function TickBacktestView() {
  const saved = loadLastStrategy();
  const [symbol, setSymbol] = useState("NBIS");
  const [startDate, setStartDate] = useState(daysAgoInputValue(7));
  const [endDate, setEndDate] = useState(todayInputValue());
  const [script, setScript] = useState(saved?.script ?? DEFAULT_TICK_SCRIPT);
  const [startingCapital, setStartingCapital] = useState(10000);
  const [positionSize, setPositionSize] = useState(1000);
  const [maxEntries, setMaxEntries] = useState(5);
  const [feePerShare, setFeePerShare] = useState(0.005);
  const [feeMinOrder, setFeeMinOrder] = useState(1.00);
  const [feeMaxPct, setFeeMaxPct] = useState(1.0);
  const [extended, setExtended] = useState(true);

  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);

  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runProgress, setRunProgress] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<{
    algorithm: { id: string; name: string; version: number };
    run: { id: string };
    tick_count: number;
    resolved_start_date?: string;
    resolved_end_date?: string;
    trading_day_count?: number;
    summary: Record<string, number>;
    daily: DailySummary[];
    trades: TradeData[];
    open_entries?: TradeEntry[];
    price_series: Record<string, PricePoint[]>;
    ticks_per_day?: Record<string, number>;
  } | null>(null);

  const [algorithms, setAlgorithms] = useState<Algorithm[]>([]);
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [showComparison, setShowComparison] = useState(false);
  const [favorites, setFavorites] = useState<Algorithm[]>([]);
  const [showStrategyPicker, setShowStrategyPicker] = useState(false);
  const [chartDay, setChartDay] = useState<string | null>(null);

  async function refreshDataStatus(sym?: string) {
    const s = sym ?? symbol;
    if (!s.trim()) { setDataStatus(null); return; }
    try {
      const res = await fetch(`${API_BASE}/api/tick-backtest/data-status/${s}`);
      if (res.ok) setDataStatus(await res.json());
      else setDataStatus(null);
    } catch { setDataStatus(null); }
  }

  useEffect(() => {
    refreshDataStatus(symbol);
  }, [symbol]);

  async function handleRun() {
    setRunLoading(true);
    setRunError(null);
    setRunResult(null);
    setRunProgress("Starting...");
    try {
      if (startDate > endDate) {
        throw new Error("Start date must be on or before end date");
      }

      const res = await fetch(`${API_BASE}/api/tick-backtest/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          start_date: startDate,
          end_date: endDate,
          extended,
          script,
          starting_capital: startingCapital,
          position_size: positionSize,
          max_entries: maxEntries,
          fee_per_share: feePerShare,
          fee_min_order: feeMinOrder,
          fee_max_pct: feeMaxPct,
          candle_timeframes: ["1m", "5m", "15m"],
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Run failed");
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No response stream");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.stage === "error") {
              throw new Error(evt.message);
            } else if (evt.stage === "done") {
              setRunResult(evt.result);
              const days = evt.result.daily as DailySummary[];
              const firstTradeDay = days.find((d: DailySummary) => d.num_trades > 0);
              setChartDay(firstTradeDay?.date ?? days[0]?.date ?? null);
            } else {
              setRunProgress(evt.message);
            }
          } catch (e) {
            if (e instanceof SyntaxError) continue;
            throw e;
          }
        }
      }

      refreshDataStatus();
      // Save last used strategy
      const extractedName = script.match(/^STRATEGY_NAME\s*=\s*["'](.+?)["']/m)?.[1] ?? "unnamed";
      saveLastStrategy(extractedName, script);
    } catch (err: unknown) {
      setRunError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunLoading(false);
      setRunProgress(null);
    }
  }

  async function loadComparison() {
    setShowComparison(true);
    try {
      const [algoRes, runRes] = await Promise.all([
        fetch(`${API_BASE}/api/tick-backtest/algorithms`),
        fetch(`${API_BASE}/api/tick-backtest/runs`),
      ]);
      if (algoRes.ok) {
        const data = await algoRes.json();
        setAlgorithms(data.algorithms || []);
      }
      if (runRes.ok) {
        const data = await runRes.json();
        setRuns(data.runs || []);
      }
    } catch { /* ignore */ }
  }

  async function loadAlgorithmScript(algoId: string) {
    try {
      const res = await fetch(`${API_BASE}/api/tick-backtest/algorithms/${algoId}`);
      if (res.ok) {
        const data = await res.json();
        setScript(data.script);
        saveLastStrategy(data.name, data.script);
      }
    } catch { /* ignore */ }
  }

  async function loadStrategies() {
    setShowStrategyPicker(true);
    try {
      const [algoRes, favRes] = await Promise.all([
        fetch(`${API_BASE}/api/tick-backtest/algorithms`),
        fetch(`${API_BASE}/api/tick-backtest/algorithms/favorites`),
      ]);
      if (algoRes.ok) {
        const data = await algoRes.json();
        // Deduplicate: keep only the latest version per name
        const byName = new Map<string, Algorithm>();
        for (const a of data.algorithms || []) {
          const existing = byName.get(a.name);
          if (!existing || a.version > existing.version) {
            byName.set(a.name, a);
          }
        }
        setAlgorithms(Array.from(byName.values()));
      }
      if (favRes.ok) {
        const data = await favRes.json();
        setFavorites(data.algorithms || []);
      }
    } catch { /* ignore */ }
  }

  async function toggleFavorite(algoId: string) {
    try {
      const res = await fetch(`${API_BASE}/api/tick-backtest/algorithms/${algoId}/favorite`, {
        method: "PATCH",
      });
      if (res.ok) {
        // Refresh both lists
        const [algoRes, favRes] = await Promise.all([
          fetch(`${API_BASE}/api/tick-backtest/algorithms`),
          fetch(`${API_BASE}/api/tick-backtest/algorithms/favorites`),
        ]);
        if (algoRes.ok) {
          const data = await algoRes.json();
          const byName = new Map<string, Algorithm>();
          for (const a of data.algorithms || []) {
            const existing = byName.get(a.name);
            if (!existing || a.version > existing.version) {
              byName.set(a.name, a);
            }
          }
          setAlgorithms(Array.from(byName.values()));
        }
        if (favRes.ok) {
          const data = await favRes.json();
          setFavorites(data.algorithms || []);
        }
      }
    } catch { /* ignore */ }
  }

  const summary = runResult?.summary;

  return (
    <div className="backtest-view">
      {/* Top bar: symbol, date range, cached status */}
      <div className="backtest-config" style={{ marginBottom: "0.5rem" }}>
        <div className="backtest-inputs" style={{ alignItems: "center" }}>
          <label>
            Symbol
            <input type="text" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: 80 }} />
          </label>
          <label>
            Start date
            <input type="date" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>
          <label>
            End date
            <input type="date" value={endDate} min={startDate} max={todayInputValue()} onChange={(e) => setEndDate(e.target.value)} />
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", cursor: "pointer" }}>
            <input type="checkbox" checked={extended} onChange={(e) => setExtended(e.target.checked)} />
            Extended hours
          </label>
          {dataStatus && dataStatus.dates.length > 0 && (
            <div style={{ fontSize: "0.75rem", color: "#94a3b8", display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ color: "#64748b" }}>Cached:</span>
              {dataStatus.dates.map((d) => {
                const expected = extended ? 16 : 7;
                return (
                  <span key={d.date}>
                    {d.date}: {d.chunks}/{expected}{d.chunks >= expected ? " ✓" : ""}
                  </span>
                );
              })}
            </div>
          )}
          {!dataStatus?.dates?.length && symbol.trim() && (
            <span style={{ fontSize: "0.75rem", color: "#64748b" }}>No cached data</span>
          )}
          <span style={{ fontSize: "0.75rem", color: "#64748b" }}>
            Range uses full trading days from the start of the selected day through the end day.
          </span>
        </div>
      </div>

      <div className="backtest-main">
        {/* Left: editor */}
        <div className="backtest-editor">
          <div className="editor-toolbar">
            <button type="button" onClick={handleRun} disabled={runLoading}>
              {runLoading ? "Running…" : "▶ Run"}
            </button>
            <button type="button" onClick={loadStrategies} title="Load strategy">
              📂
            </button>
            <button type="button" onClick={loadComparison}>
              Compare
            </button>
          </div>
          {showStrategyPicker && (
            <div className="strategy-picker" style={{
              background: "rgba(15, 23, 42, 0.95)",
              border: "1px solid rgba(148, 163, 184, 0.2)",
              borderRadius: "8px",
              padding: "0.75rem",
              marginBottom: "0.5rem",
              maxHeight: "300px",
              overflowY: "auto",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <strong style={{ fontSize: "0.85rem" }}>Load Strategy</strong>
                <button type="button" onClick={() => setShowStrategyPicker(false)} style={{ fontSize: "0.7rem", padding: "0.1rem 0.4rem" }}>✕</button>
              </div>
              {favorites.length > 0 && (
                <>
                  <div style={{ fontSize: "0.7rem", color: "#f59e0b", marginBottom: "0.25rem", fontWeight: 600 }}>★ Favorites</div>
                  {favorites.map((a) => (
                    <div key={a.id} className="strategy-picker-item" style={{
                      display: "flex", alignItems: "center", gap: "0.5rem",
                      padding: "0.3rem 0.5rem", borderRadius: "4px", cursor: "pointer",
                      fontSize: "0.8rem", marginBottom: "0.15rem",
                    }}>
                      <button type="button" onClick={() => toggleFavorite(a.id)}
                        style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: "0.85rem", color: "#f59e0b" }}
                        title="Remove from favorites">★</button>
                      <span style={{ flex: 1, cursor: "pointer" }} onClick={() => { loadAlgorithmScript(a.id); setShowStrategyPicker(false); }}>
                        {a.name} <span style={{ color: "#64748b" }}>v{a.version}</span>
                      </span>
                    </div>
                  ))}
                  <hr style={{ border: "none", borderTop: "1px solid rgba(148,163,184,0.15)", margin: "0.5rem 0" }} />
                </>
              )}
              <div style={{ fontSize: "0.7rem", color: "#94a3b8", marginBottom: "0.25rem" }}>All Strategies</div>
              {algorithms.length === 0 && (
                <div style={{ fontSize: "0.75rem", color: "#64748b" }}>No strategies saved yet.</div>
              )}
              {algorithms.map((a) => (
                <div key={a.id} className="strategy-picker-item" style={{
                  display: "flex", alignItems: "center", gap: "0.5rem",
                  padding: "0.3rem 0.5rem", borderRadius: "4px", cursor: "pointer",
                  fontSize: "0.8rem", marginBottom: "0.15rem",
                }}>
                  <button type="button" onClick={() => toggleFavorite(a.id)}
                    style={{ background: "none", border: "none", cursor: "pointer", padding: 0, fontSize: "0.85rem", color: a.is_favorite ? "#f59e0b" : "#475569" }}
                    title={a.is_favorite ? "Remove from favorites" : "Add to favorites"}>
                    {a.is_favorite ? "★" : "☆"}
                  </button>
                  <span style={{ flex: 1, cursor: "pointer" }} onClick={() => { loadAlgorithmScript(a.id); setShowStrategyPicker(false); }}>
                    {a.name} <span style={{ color: "#64748b" }}>v{a.version}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
          <Editor
            height="500px"
            defaultLanguage="python"
            value={script}
            onChange={(v) => setScript(v ?? "")}
            theme="vs-dark"
            options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false }}
          />
        </div>

        {/* Right: config + results */}
        <div className="backtest-sidebar">
          {/* Backtest config */}
          <div className="backtest-config">
            <h3>Config</h3>
            <div className="backtest-inputs">
              <label>
                Capital
                <input type="number" min={100} value={startingCapital} onChange={(e) => setStartingCapital(Number(e.target.value))} />
              </label>
              <label>
                Buy Size
                <input type="number" min={100} value={positionSize} onChange={(e) => setPositionSize(Number(e.target.value))} />
              </label>
              <label>
                Max Entries
                <input type="number" min={1} max={100} value={maxEntries} onChange={(e) => setMaxEntries(Number(e.target.value))} />
              </label>
              <label>
                Fee/Share $
                <input type="number" min={0} step={0.001} value={feePerShare} onChange={(e) => setFeePerShare(Number(e.target.value))} />
              </label>
              <label>
                Fee Min $
                <input type="number" min={0} step={0.1} value={feeMinOrder} onChange={(e) => setFeeMinOrder(Number(e.target.value))} />
              </label>
              <label>
                Fee Max %
                <input type="number" min={0} max={10} step={0.1} value={feeMaxPct} onChange={(e) => setFeeMaxPct(Number(e.target.value))} />
              </label>
            </div>
          </div>

          {runLoading && (
            <div className="loading-banner" style={{ margin: "0.5rem 0", display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span className="spinner" />
              <span>{runProgress || "Starting..."}</span>
            </div>
          )}
          {runError && <p className="error-banner">{runError}</p>}

          {/* Results summary */}
          {summary && (
            <div className="backtest-summary">
              <div>
                <span>Strategy</span>
                <strong>{runResult.algorithm.name} v{runResult.algorithm.version}</strong>
              </div>
              <div>
                <span>Symbol</span>
                <strong>{symbol}</strong>
              </div>
              {runResult.resolved_start_date && runResult.resolved_end_date && (
                <div>
                  <span>Trading window</span>
                  <strong>{runResult.resolved_start_date} → {runResult.resolved_end_date}</strong>
                </div>
              )}
              <div><span>Ticks</span><strong>{runResult.tick_count.toLocaleString()}</strong></div>
              <div><span>Trades</span><strong>{summary.num_trades}</strong></div>
              <div><span>Win Rate</span><strong>{summary.win_rate}%</strong></div>
              <div>
                <span>Total P&L</span>
                <strong style={{ color: summary.total_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                  {formatSignedCurrency(summary.total_pnl)} ({formatSigned(summary.total_pnl_pct, "%")})
                </strong>
              </div>
              <div><span>Final Balance</span><strong>{formatCurrency(summary.final_balance)}</strong></div>
              {summary.total_fees > 0 && (
                <div><span>Total Fees</span><strong style={{ color: "#f59e0b" }}>{formatCurrency(summary.total_fees)}</strong></div>
              )}
            </div>
          )}

          {/* Daily grid */}
          {runResult && runResult.daily.length > 0 && (
            <div className="backtest-trades backtest-batch-results">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Buys</th>
                    <th>Sells</th>
                    <th>Trades</th>
                    <th>Win Rate</th>
                    <th>Avg Trade</th>
                    <th>Realized P&L</th>
                    <th>Unrealized P&L</th>
                    <th>Position</th>
                  </tr>
                </thead>
                <tbody>
                  {runResult.daily.map((day, dayIdx) => {
                    const hasTrades = day.num_trades > 0;
                    const hasUnrealized = (day.unrealized_pnl ?? 0) !== 0;
                    const hasPosition = (day.position_shares ?? 0) > 0;
                    const isSelected = chartDay === day.date;
                    // Detect partial day: last day with significantly fewer ticks than the median
                    const dayTicks = runResult.ticks_per_day?.[day.date] ?? 0;
                    const allTickCounts = Object.values(runResult.ticks_per_day ?? {});
                    const medianTicks = allTickCounts.length > 1
                      ? [...allTickCounts].sort((a, b) => a - b)[Math.floor(allTickCounts.length / 2)]
                      : dayTicks;
                    const isPartial = dayIdx === runResult.daily.length - 1 && medianTicks > 0 && dayTicks < medianTicks * 0.5;
                    return (
                      <tr key={day.date} onClick={() => setChartDay(day.date)}
                        style={{
                          cursor: "pointer",
                          background: isSelected ? "rgba(56, 189, 248, 0.1)" : undefined,
                          borderLeft: isSelected ? "2px solid #38bdf8" : "2px solid transparent",
                        }}>
                        <td>
                          {day.date}
                          {isPartial && <span style={{ color: "#f59e0b", fontSize: "0.7rem", marginLeft: "0.35rem" }} title={`${dayTicks.toLocaleString()} ticks (partial day)`}>⚠ partial</span>}
                        </td>
                        <td style={{ color: day.day_buys > 0 ? "#10b981" : "#94a3b8" }}>{day.day_buys || "—"}</td>
                        <td style={{ color: day.day_sells > 0 ? "#ef4444" : "#94a3b8" }}>{day.day_sells || "—"}</td>
                        <td>{day.num_trades}</td>
                        <td style={{ color: hasTrades ? "#e2e8f0" : "#94a3b8" }}>{hasTrades ? `${day.win_rate}%` : "—"}</td>
                        <td style={{ color: hasTrades ? ((day.avg_trade_pct ?? 0) >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                          {hasTrades ? formatSigned(day.avg_trade_pct ?? 0, "%") : "—"}
                        </td>
                        <td style={{ color: hasTrades ? (day.total_pnl >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                          {hasTrades ? formatSignedCurrency(day.total_pnl) : "—"}
                        </td>
                        <td style={{ color: hasUnrealized ? ((day.unrealized_pnl ?? 0) >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                          {hasUnrealized ? formatSignedCurrency(day.unrealized_pnl ?? 0) : hasPosition ? "$0.00" : "—"}
                        </td>
                        <td style={{ color: hasPosition ? "#e2e8f0" : "#94a3b8" }}>
                          {hasPosition ? `${(day.position_shares ?? 0).toFixed(2)} sh` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Price chart with VWAP and trade markers */}
          {runResult && chartDay && runResult.price_series?.[chartDay] && (() => {
            const chartTicks = runResult.ticks_per_day?.[chartDay] ?? 0;
            const allCounts = Object.values(runResult.ticks_per_day ?? {});
            const medCount = allCounts.length > 1
              ? [...allCounts].sort((a, b) => a - b)[Math.floor(allCounts.length / 2)]
              : chartTicks;
            const chartPartial = medCount > 0 && chartTicks < medCount * 0.5;
            return (
            <div style={{ marginTop: "0.5rem" }}>
              <div style={{ fontSize: "0.8rem", color: "#94a3b8", marginBottom: "0.25rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <span>Price &amp; VWAP — {chartDay}</span>
                {chartPartial && (
                  <span style={{ fontSize: "0.7rem", color: "#f59e0b", background: "rgba(245,158,11,0.1)", padding: "0.1rem 0.4rem", borderRadius: "4px" }}>
                    ⚠ Partial day ({chartTicks.toLocaleString()} ticks)
                  </span>
                )}
              </div>
              <BacktestChart
                priceData={runResult.price_series[chartDay]}
                trades={runResult.trades}
                openEntries={runResult.open_entries}
                selectedDate={chartDay}
              />
            </div>
            );
          })()}
        </div>
      </div>

      {/* Comparison section */}
      {showComparison && (
        <div className="backtest-batch-results" style={{ marginTop: "1.5rem" }}>
          <h3>Algorithm Comparison</h3>
          {runs.length === 0 ? (
            <p style={{ color: "#94a3b8" }}>No runs saved yet. Run a tick backtest to get started.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Algorithm</th>
                  <th>Version</th>
                  <th>Symbol</th>
                  <th>Days</th>
                  <th>Trades</th>
                  <th>Win Rate</th>
                  <th>P&L</th>
                  <th>P&L %</th>
                  <th>Final Balance</th>
                  <th>Date</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>{run.algorithm_name}</td>
                    <td>v{run.algorithm_version}</td>
                    <td>{run.symbol}</td>
                    <td>{run.lookback_days ?? "—"}</td>
                    <td>{run.num_trades}</td>
                    <td>{run.win_rate.toFixed(1)}%</td>
                    <td style={{ color: run.total_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                      {formatSignedCurrency(run.total_pnl)}
                    </td>
                    <td style={{ color: run.total_pnl_pct >= 0 ? "#10b981" : "#ef4444" }}>
                      {formatSigned(run.total_pnl_pct, "%")}
                    </td>
                    <td>{formatCurrency(run.final_balance)}</td>
                    <td style={{ fontSize: "0.7rem", color: "#94a3b8" }}>
                      {run.created_at ? new Date(run.created_at).toLocaleDateString() : "—"}
                    </td>
                    <td>
                      <button
                        type="button"
                        style={{ fontSize: "0.7rem", padding: "0.15rem 0.5rem" }}
                        onClick={() => loadAlgorithmScript(run.algorithm_id)}
                      >
                        Load
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
