import { useEffect, useMemo, useRef, useState } from "react";

import BacktestView from "./components/BacktestView";
import CandlestickChart from "./components/CandlestickChart";
import type { Candle, SocketMessage, Timeframe, TimelineEvent } from "./lib/types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const SOCKET_URL =
  import.meta.env.VITE_WS_URL ??
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/market`;
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h"];

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
  const [symbolInput, setSymbolInput] = useState("AAPL");
  const [symbol, setSymbol] = useState("AAPL");
  const [timeframe, setTimeframe] = useState<Timeframe>("1m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [status, setStatus] = useState("Connecting");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"live" | "backtest">("live");
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Clear chart immediately so the user sees instant visual feedback
    // when symbol or timeframe changes. The WebSocket effect below then
    // reconnects and delivers a fresh snapshot.
    setCandles([]);
    setEvents([]);
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
        setEvents(message.events);
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

  const latestCandle = candles[candles.length - 1];
  const sortedEvents = useMemo(
    () => [...events].sort((left, right) => new Date(left.time).getTime() - new Date(right.time).getTime()),
    [events],
  );

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
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Trading Platform Foundation</p>
          <h1>Candlestick chart with event overlays and a Python live feed</h1>
        </div>
        <div className="status-grid">
          <div>
            <span>Status</span>
            <strong>{status}</strong>
          </div>
          <div>
            <span>Symbol</span>
            <strong>{symbol}</strong>
          </div>
          <div>
            <span>Last close</span>
            <strong>{latestCandle ? latestCandle.close.toFixed(2) : "--"}</strong>
          </div>
        </div>
      </section>

      <div className="tab-bar">
        <button type="button" className={`tab${view === "live" ? " active" : ""}`} onClick={() => setView("live")}>Live chart</button>
        <button type="button" className={`tab${view === "backtest" ? " active" : ""}`} onClick={() => setView("backtest")}>Backtest</button>
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
              <div className="panel-header">
                <h2>Live candlesticks</h2>
                <p>WebSocket updates from FastAPI with event markers on the series.</p>
              </div>
              <CandlestickChart candles={candles} events={events} timeframe={timeframe} />
            </article>

            <aside className="event-panel">
              <div className="panel-header">
                <h2>Timeline events</h2>
                <p>Separate event objects so earnings and future annotations stay reusable for strategies and backtests.</p>
              </div>
              <ul className="event-list">
                {sortedEvents.map((event) => (
                  <li key={event.id}>
                    <span className="event-type">{event.event_type}</span>
                    <strong>{event.title}</strong>
                    <p>{event.summary}</p>
                    <time>{new Date(event.time).toLocaleString()}</time>
                  </li>
                ))}
              </ul>
            </aside>
          </section>
        </>
      )}

      {view === "backtest" && <BacktestView />}
    </main>
  );
}
