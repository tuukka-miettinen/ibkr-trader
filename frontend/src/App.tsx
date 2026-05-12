import { useEffect, useRef, useState } from "react";

import CandlestickChart from "./components/CandlestickChart";
import LiveTradingView from "./components/LiveTradingView";
import TickBacktestView from "./components/TickBacktestView";
import type { Candle, SocketMessage, Timeframe } from "./lib/types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const SOCKET_URL =
  import.meta.env.VITE_WS_URL ??
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/market`;
const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "1h"];

function mergeCandleUpdate(existing: Candle[], incoming: Candle): Candle[] {
  const next = existing.slice();
  const last = next[next.length - 1];
  if (last && last.time === incoming.time) {
    next[next.length - 1] = incoming;
    return next;
  }

  next.push(incoming);
  if (next.length > 300) {
    next.shift();
  }
  return next;
}

export default function App() {
  const [symbolInput, setSymbolInput] = useState("NBIS");
  const [symbol, setSymbol] = useState("NBIS");
  const [timeframe, setTimeframe] = useState<Timeframe>("1m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState("Connecting");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"live" | "tick-backtest" | "paper-trading">("live");
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Clear chart immediately so the user sees instant visual feedback
    // when symbol or timeframe changes. The WebSocket effect below then
    // reconnects and delivers a fresh snapshot.
    setCandles([]);
    setLoading(true);
    setError(null);
  }, [symbol, timeframe]);

  useEffect(() => {
    const socket = new WebSocket(SOCKET_URL);
    socketRef.current = socket;

    socket.onopen = () => {
      setStatus("Connected");
      setError(null);
      socket.send(JSON.stringify({ type: "subscribe", symbol, timeframe }));
    };

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as SocketMessage;
      if (message.type === "snapshot") {
        setCandles(message.candles);
        setLoading(false);
        return;
      }

      if (message.type === "candle_update") {
        setCandles((current) => mergeCandleUpdate(current, message.candle));
        return;
      }

      if (message.type === "status") {
        setStatus(message.message);
        return;
      }

      setError(message.message);
    };

    socket.onclose = () => {
      setStatus("Disconnected");
    };

    socket.onerror = () => {
      setError("WebSocket connection failed");
    };

    return () => {
      socket.close();
    };
  }, [symbol, timeframe]);

  function handleApplySymbol() {
    const nextSymbol = symbolInput.trim().toUpperCase();
    if (!nextSymbol || nextSymbol === symbol) {
      return;
    }
    // setSymbol triggers the useEffect that clears the chart immediately
    // and tears down + re-opens the WebSocket with the new symbol.
    setSymbol(nextSymbol);
  }

  return (
    <main className="page-shell">
      <div className="tab-bar">
        <button type="button" className={`tab${view === "live" ? " active" : ""}`} onClick={() => setView("live")}>Live chart</button>
        <button type="button" className={`tab${view === "tick-backtest" ? " active" : ""}`} onClick={() => setView("tick-backtest")}>Tick Backtest</button>
        <button type="button" className={`tab${view === "paper-trading" ? " active" : ""}`} onClick={() => setView("paper-trading")}>Paper Trading</button>
      </div>

      {view === "live" && (
        <>
          <section className="control-bar">
            <label>
              Symbol
              <input value={symbolInput} onChange={(event) => setSymbolInput(event.target.value.toUpperCase())} />
            </label>
            <label>
              Timeframe
              <select value={timeframe} onChange={(event) => setTimeframe(event.target.value as Timeframe)}>
                {TIMEFRAMES.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={handleApplySymbol}>
              Load symbol
            </button>
          </section>

          {loading && !error ? <p className="loading-banner">Loading {symbol} {timeframe}…</p> : null}
          {error ? <p className="error-banner">{error}</p> : null}

          <section className="content-grid">
            <article className="chart-panel">
              <CandlestickChart candles={candles} events={[]} timeframe={timeframe} />
            </article>
          </section>
        </>
      )}

      {view === "tick-backtest" && <TickBacktestView />}

      {view === "paper-trading" && <LiveTradingView />}
    </main>
  );
}
