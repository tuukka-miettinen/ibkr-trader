import { useEffect, useRef, useState } from "react";
import { Link, Route, Switch, useLocation } from "wouter";

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
  const [location] = useLocation();
  const socketRef = useRef<WebSocket | null>(null);

  const activeTab = location.startsWith("/paper-trading")
    ? "paper-trading"
    : location.startsWith("/tick-backtest")
      ? "tick-backtest"
      : "live";
  const isLiveChartRoute = activeTab === "live";

  useEffect(() => {
    if (!isLiveChartRoute) {
      return;
    }
    // Clear chart immediately so the user sees instant visual feedback
    // when symbol or timeframe changes. The WebSocket effect below then
    // reconnects and delivers a fresh snapshot.
    setCandles([]);
    setLoading(true);
    setError(null);
  }, [isLiveChartRoute, symbol, timeframe]);

  useEffect(() => {
    if (!isLiveChartRoute) {
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
      return;
    }

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

      if (message.type === "error") {
        setError(message.message);
        setLoading(false);
        return;
      }

      setError("Unexpected WebSocket message");
      setLoading(false);
    };

    socket.onclose = () => {
      setStatus("Disconnected");
      setLoading(false);
    };

    socket.onerror = () => {
      setError("WebSocket connection failed");
      setLoading(false);
    };

    return () => {
      socket.close();
    };
  }, [isLiveChartRoute, symbol, timeframe]);

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
        <Link href="/" className={`tab${activeTab === "live" ? " active" : ""}`}>Live chart</Link>
        <Link href="/tick-backtest" className={`tab${activeTab === "tick-backtest" ? " active" : ""}`}>Tick Backtest</Link>
        <Link href="/paper-trading" className={`tab${activeTab === "paper-trading" ? " active" : ""}`}>Paper Trading</Link>
      </div>

      <Switch>
        <Route path="/tick-backtest">
          <TickBacktestView />
        </Route>
        <Route path="/paper-trading/:sessionId">
          {(params) => <LiveTradingView initialSessionId={params.sessionId} />}
        </Route>
        <Route path="/paper-trading">
          <LiveTradingView />
        </Route>
        <Route path="/">
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
        </Route>
      </Switch>
    </main>
  );
}
