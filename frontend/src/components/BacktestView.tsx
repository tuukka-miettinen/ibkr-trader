import { useEffect, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type UTCTimestamp,
} from "lightweight-charts";
import type * as Monaco from "monaco-editor";

import type { Timeframe } from "../lib/types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h"];
const BATCH_TIMEFRAMES: Timeframe[] = ["5m", "15m"];
const DEFAULT_BATCH_LIMIT = 1638;
const INDICATOR_COLORS = ["#38bdf8", "#f59e0b", "#a78bfa", "#34d399", "#fb923c", "#f472b6"];
const DEFAULT_BATCH_SYMBOLS = [
  "TSLA", "LUNR", "ASTS", "SATS", "RKLB", "RDW", "GAMB", "HOOD", "TSSI", "CRWV", "SOAR", "UUUU",
  "SGML", "SOUN", "APP", "PMCB", "NBIS", "NIXX", "LNG", "CRM", "YOU", "SPOT", "SOFI", "LTBR",
  "MSFT", "XOM", "JPM", "ADBE", "AREC", "PATH", "META", "LMT", "NFLX", "TM", "BABA", "INUV",
  "BLNE", "CB", "VCX", "AVAI", "RTX", "BMY", "LIN", "ACN", "GOOG", "BYD", "PLTR", "AMZN",
  "ORCL", "XE", "LITE", "TER", "NVDA", "SYPR", "AAPL", "MDB", "UNH", "IONQ", "COIN",
  "SMCI", "IREN", "HIMS", "AMD", "INTC", "MU", "REPL",
].join("\n");

const DEFAULT_SCRIPT = `# ta module: ta.sma, ta.ema, ta.vwap, ta.rsi, ta.atr, ta.bollinger, ta.macd
# Each candle: .time (datetime), .open, .high, .low, .close, .volume
# Extra numeric keys are auto-plotted as lines on the chart.
# Use {"value": x, "separate": True} to force a separate pane for one indicator.
# Optional marker example:
# "markers": [{"text": "RSI bull", "shape": "circle", "position": "belowBar", "color": "#f59e0b"}]

def signals(candles):
    ema20 = ta.ema(candles, 20)
    rsi14 = ta.rsi(candles, 14)
    vwap = ta.vwap(candles)
    results = []

    for i, bar in enumerate(candles):
        signal = None
        markers = []
        if i > 0 and ema20[i] is not None and rsi14[i] is not None and vwap[i] is not None:
            prev_rsi = rsi14[i - 1]
            prev_ema20 = ema20[i - 1]
            if prev_rsi is not None and prev_ema20 is not None:
                long_trend = bar.close > ema20[i] and bar.close > vwap[i]
                short_trend = bar.close < ema20[i] and bar.close < vwap[i]

                if candles[i - 1].close <= prev_ema20 and bar.close > ema20[i]:
                    markers.append({"text": "EMA20+", "shape": "circle", "position": "belowBar", "color": "#38bdf8"})
                elif candles[i - 1].close >= prev_ema20 and bar.close < ema20[i]:
                    markers.append({"text": "EMA20-", "shape": "circle", "position": "aboveBar", "color": "#38bdf8"})

                if prev_rsi <= 30 and rsi14[i] > 30:
                    markers.append({"text": "RSI bull", "shape": "square", "position": "belowBar", "color": "#f59e0b"})
                elif prev_rsi >= 70 and rsi14[i] < 70:
                    markers.append({"text": "RSI bear", "shape": "square", "position": "aboveBar", "color": "#f59e0b"})

                if long_trend and prev_rsi <= 30 and rsi14[i] > 30:
                    signal = "buy"
                elif short_trend and prev_rsi >= 70 and rsi14[i] < 70:
                    signal = "sell"

        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
            "ema_20": ema20[i],
            "rsi_14": rsi14[i],
            "vwap": vwap[i],
            "markers": markers,
        })

    return results
`;

type Signal = { time: string; signal: "buy" | "sell" | null };
type SignalMarker = {
  time: string;
  text: string;
  shape: "circle" | "square" | "arrowUp" | "arrowDown";
  position: "aboveBar" | "belowBar" | "inBar";
  color: string;
};
type Trade = {
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
};
type Summary = {
  num_trades: number;
  total_pnl: number;
  total_pnl_pct: number;
  win_rate: number;
};
type DailyBatchSummary = Summary & { date: string };
type BatchAggregate = {
  overall: Summary;
  daily: DailyBatchSummary[];
};
type BacktestData = {
  symbol?: string;
  timeframe?: Timeframe;
  candles: { time: string; open: number; high: number; low: number; close: number }[];
  signals: Signal[];
  markers: SignalMarker[];
  indicators: Record<string, (number | null)[]>;
  indicator_separate?: Record<string, boolean>;
  trades: Trade[];
  summary: Summary;
};
type BatchRow = {
  symbol: string;
  timeframe: Timeframe;
  status: "ok" | "error";
  summary?: Summary;
  error?: string;
};
type BatchSortKey = "symbol" | "timeframe" | "status" | "trades" | "winRate" | "pnl" | "pnlPct";

function hasTrades(summary?: Summary | null) {
  return (summary?.num_trades ?? 0) > 0;
}

function formatSigned(value: number, suffix = "") {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}${suffix}`;
}

function compareBatchRows(left: BatchRow, right: BatchRow, sortKey: BatchSortKey) {
  switch (sortKey) {
    case "symbol":
      return left.symbol.localeCompare(right.symbol);
    case "timeframe":
      return left.timeframe.localeCompare(right.timeframe);
    case "status":
      return left.status.localeCompare(right.status);
    case "trades":
      return (left.summary?.num_trades ?? -1) - (right.summary?.num_trades ?? -1);
    case "winRate":
      return (left.summary?.win_rate ?? -1) - (right.summary?.win_rate ?? -1);
    case "pnl":
      return (left.summary?.total_pnl ?? Number.NEGATIVE_INFINITY) - (right.summary?.total_pnl ?? Number.NEGATIVE_INFINITY);
    case "pnlPct":
      return (left.summary?.total_pnl_pct ?? Number.NEGATIVE_INFINITY) - (right.summary?.total_pnl_pct ?? Number.NEGATIVE_INFINITY);
  }
}

function parseSymbols(text: string) {
  const seen = new Set<string>();
  const symbols: string[] = [];
  for (const rawLine of text.split(/[,\s]+/)) {
    const symbol = rawLine.trim().toUpperCase();
    if (!symbol || seen.has(symbol)) {
      continue;
    }
    seen.add(symbol);
    symbols.push(symbol);
  }
  return symbols;
}
type ChartMarker = {
  time: UTCTimestamp;
  text: string;
  shape: "circle" | "square" | "arrowUp" | "arrowDown";
  position: "aboveBar" | "belowBar" | "inBar";
  color: string;
};
type LinePoint = { time: UTCTimestamp; value: number };
type HistogramPoint = { time: UTCTimestamp; value: number; color: string };
type IndicatorBucket = "trend" | "auxiliary";

function ts(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function isOscillatorIndicator(key: string): boolean {
  const lower = key.toLowerCase();
  return lower.startsWith("rsi") || lower.startsWith("macd") || lower.includes("signal") || lower.includes("hist");
}

function getMedian(values: number[]) {
  if (values.length === 0) {
    return null;
  }

  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }

  return sorted[middle];
}

function getIndicatorBucket(values: (number | null)[], closeValues: number[]): IndicatorBucket {
  const numericValues = values.filter((value): value is number => value != null).map((value) => Math.abs(value));
  const indicatorMedian = getMedian(numericValues);
  const closeMedian = getMedian(closeValues.map((value) => Math.abs(value)));

  if (!indicatorMedian || !closeMedian) {
    return "trend";
  }

  const ratio = indicatorMedian / closeMedian;
  return ratio >= 0.25 && ratio <= 4 ? "trend" : "auxiliary";
}

function isSeparateIndicator(
  key: string,
  values: (number | null)[],
  closeValues: number[],
  indicatorSeparate?: Record<string, boolean>,
) {
  const separate = indicatorSeparate?.[key];
  if (typeof separate === "boolean") {
    return separate;
  }

  return getIndicatorBucket(values, closeValues) === "auxiliary";
}

function buildLineData(times: UTCTimestamp[], values: (number | null)[]) {
  return values
    .map((value, index) => (value != null ? { time: times[index], value } : null))
    .filter(Boolean) as LinePoint[];
}

function buildLevelData(times: UTCTimestamp[], value: number) {
  return times.map((time) => ({ time, value }));
}

function buildEma(values: number[], period: number): Array<number | null> {
  const result: Array<number | null> = Array.from({ length: values.length }, () => null);
  if (values.length < period) {
    return result;
  }

  const smoothing = 2 / (period + 1);
  let emaValue = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  result[period - 1] = emaValue;

  for (let index = period; index < values.length; index += 1) {
    emaValue = values[index] * smoothing + emaValue * (1 - smoothing);
    result[index] = Number(emaValue.toFixed(4));
  }

  return result;
}

function buildMomentumMacd(candles: BacktestData["candles"]) {
  const closes = candles.map((candle) => candle.close);
  const fastEma = buildEma(closes, 3);
  const slowEma = buildEma(closes, 10);
  const macdValues = closes.map((_, index) => (
    fastEma[index] !== null && slowEma[index] !== null
      ? Number((fastEma[index]! - slowEma[index]!).toFixed(4))
      : null
  ));

  const validMacdValues = macdValues.filter((value): value is number => value !== null);
  const signalValues: Array<number | null> = Array.from({ length: candles.length }, () => null);
  if (validMacdValues.length >= 16) {
    const firstMacdIndex = macdValues.findIndex((value) => value !== null);
    let signalValue = validMacdValues.slice(0, 16).reduce((sum, value) => sum + value, 0) / 16;
    const signalStartIndex = firstMacdIndex + 15;
    signalValues[signalStartIndex] = Number(signalValue.toFixed(4));

    const smoothing = 2 / 17;
    for (let index = signalStartIndex + 1; index < macdValues.length; index += 1) {
      const macdValue = macdValues[index];
      if (macdValue === null) {
        continue;
      }
      signalValue = macdValue * smoothing + signalValue * (1 - smoothing);
      signalValues[index] = Number(signalValue.toFixed(4));
    }
  }

  const macdLine: LinePoint[] = [];
  const signalLine: LinePoint[] = [];
  const histogram: HistogramPoint[] = [];

  candles.forEach((candle, index) => {
    const time = ts(candle.time);
    const macdValue = macdValues[index];
    const signalValue = signalValues[index];

    if (macdValue !== null) {
      macdLine.push({ time, value: macdValue });
    }
    if (signalValue !== null) {
      signalLine.push({ time, value: signalValue });
    }
    if (macdValue !== null && signalValue !== null) {
      const histogramValue = Number((macdValue - signalValue).toFixed(4));
      histogram.push({
        time,
        value: histogramValue,
        color: histogramValue >= 0 ? "rgba(16, 185, 129, 0.72)" : "rgba(239, 68, 68, 0.72)",
      });
    }
  });

  return { macdLine, signalLine, histogram };
}

function buildFallbackRsi(candles: BacktestData["candles"], period = 14): LinePoint[] {
  if (candles.length <= period) {
    return [];
  }

  const closes = candles.map((candle) => candle.close);
  let averageGain = 0;
  let averageLoss = 0;

  for (let index = 1; index <= period; index += 1) {
    const delta = closes[index] - closes[index - 1];
    averageGain += Math.max(delta, 0);
    averageLoss += Math.max(-delta, 0);
  }

  averageGain /= period;
  averageLoss /= period;

  const points: LinePoint[] = [];
  const firstValue = averageLoss === 0 ? 100 : 100 - (100 / (1 + averageGain / averageLoss));
  points.push({ time: ts(candles[period].time), value: Number(firstValue.toFixed(2)) });

  for (let index = period + 1; index < closes.length; index += 1) {
    const delta = closes[index] - closes[index - 1];
    const gain = Math.max(delta, 0);
    const loss = Math.max(-delta, 0);
    averageGain = ((averageGain * (period - 1)) + gain) / period;
    averageLoss = ((averageLoss * (period - 1)) + loss) / period;
    const rsi = averageLoss === 0 ? 100 : 100 - (100 / (1 + averageGain / averageLoss));
    points.push({ time: ts(candles[index].time), value: Number(rsi.toFixed(2)) });
  }

  return points;
}

export default function BacktestView() {
  const [symbol, setSymbol] = useState("AAPL");
  const [timeframe, setTimeframe] = useState<Timeframe>("15m");
  const [limit, setLimit] = useState(DEFAULT_BATCH_LIMIT);
  const [symbolsText, setSymbolsText] = useState(DEFAULT_BATCH_SYMBOLS);
  const [script, setScript] = useState(DEFAULT_SCRIPT);
  const [data, setData] = useState<BacktestData | null>(null);
  const [batchRows, setBatchRows] = useState<BatchRow[]>([]);
  const [batchAggregate, setBatchAggregate] = useState<BatchAggregate | null>(null);
  const [batchSortKey, setBatchSortKey] = useState<BatchSortKey>("pnl");
  const [batchSortDirection, setBatchSortDirection] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(false);
  const [batchLoading, setBatchLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [scriptError, setScriptError] = useState<string | null>(null);
  const [scriptValidated, setScriptValidated] = useState(false);

  const priceContainerRef = useRef<HTMLDivElement | null>(null);
  const auxiliaryContainerRef = useRef<HTMLDivElement | null>(null);
  const macdContainerRef = useRef<HTMLDivElement | null>(null);
  const rsiContainerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const auxiliaryChartRef = useRef<IChartApi | null>(null);
  const macdChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceOverlaySeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const auxiliarySeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const macdLineSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const macdHistogramSeriesRef = useRef<ISeriesApi<"Histogram">[]>([]);
  const rsiSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const rsiBandSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const syncReadyRef = useRef(false);
  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof Monaco | null>(null);

  function clearScriptMarkers() {
    const monaco = monacoRef.current;
    const model = editorRef.current?.getModel();
    if (!monaco || !model) {
      return;
    }
    monaco.editor.setModelMarkers(model, "script-validation", []);
  }

  function setScriptMarkers(message: string) {
    const monaco = monacoRef.current;
    const model = editorRef.current?.getModel();
    if (!monaco || !model) {
      return;
    }

    const match = message.match(/<strategy>, line (\d+)(?:, column (\d+))?/i);
    const lineNumber = match ? Number(match[1]) : 1;
    const column = match?.[2] ? Number(match[2]) : 1;
    monaco.editor.setModelMarkers(model, "script-validation", [{
      startLineNumber: lineNumber,
      endLineNumber: lineNumber,
      startColumn: column,
      endColumn: column + 1,
      message,
      severity: monaco.MarkerSeverity.Error,
    }]);
  }

  async function validateScript(nextScript = script) {
    try {
      const res = await fetch(`${API_BASE}/api/backtest/validate-script`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script: nextScript }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }

      clearScriptMarkers();
      setScriptError(null);
      setScriptValidated(true);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setScriptError(message);
      setScriptValidated(false);
      setScriptMarkers(message);
      return false;
    }
  }

  const handleEditorMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    editor.onDidBlurEditorText(() => {
      void validateScript(editor.getValue());
    });
  };

  useEffect(() => {
    if (!priceContainerRef.current || !auxiliaryContainerRef.current || !macdContainerRef.current || !rsiContainerRef.current) {
      return;
    }

    const priceChart = createChart(priceContainerRef.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#07111f" }, textColor: "#dbe7f5" },
      grid: { vertLines: { color: "rgba(148,163,184,0.12)" }, horzLines: { color: "rgba(148,163,184,0.12)" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.25)" },
      timeScale: { borderColor: "rgba(148,163,184,0.25)", timeVisible: true },
    });

    const createPaneChart = (element: HTMLDivElement) => createChart(element, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#07111f" }, textColor: "#dbe7f5" },
      grid: { vertLines: { color: "rgba(148,163,184,0.08)" }, horzLines: { color: "rgba(148,163,184,0.12)" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "rgba(148,163,184,0.25)",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: { borderColor: "rgba(148,163,184,0.25)", timeVisible: true, visible: false },
      handleScroll: false,
      handleScale: false,
    });

    const auxiliaryChart = createPaneChart(auxiliaryContainerRef.current);
    const macdChart = createPaneChart(macdContainerRef.current);
    const rsiChart = createPaneChart(rsiContainerRef.current);

    candleRef.current = priceChart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    priceChartRef.current = priceChart;
    auxiliaryChartRef.current = auxiliaryChart;
    macdChartRef.current = macdChart;
    rsiChartRef.current = rsiChart;

    const syncRange = (range: LogicalRange | null) => {
      if (!range || !syncReadyRef.current) {
        return;
      }
      auxiliaryChart.timeScale().setVisibleLogicalRange(range);
      macdChart.timeScale().setVisibleLogicalRange(range);
      rsiChart.timeScale().setVisibleLogicalRange(range);
    };

    priceChart.timeScale().subscribeVisibleLogicalRangeChange(syncRange);

    return () => {
      priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(syncRange);
      priceChart.remove();
      auxiliaryChart.remove();
      macdChart.remove();
      rsiChart.remove();
    };
  }, []);

  useEffect(() => {
    const priceChart = priceChartRef.current;
    const auxiliaryChart = auxiliaryChartRef.current;
    const macdChart = macdChartRef.current;
    const rsiChart = rsiChartRef.current;
    const candleSeries = candleRef.current;
    if (!data || !priceChart || !auxiliaryChart || !macdChart || !rsiChart || !candleSeries) {
      return;
    }

    for (const series of priceOverlaySeriesRef.current) {
      try { priceChart.removeSeries(series); } catch {}
    }
    for (const series of auxiliarySeriesRef.current) {
      try { auxiliaryChart.removeSeries(series); } catch {}
    }
    for (const series of macdLineSeriesRef.current) {
      try { macdChart.removeSeries(series); } catch {}
    }
    for (const series of macdHistogramSeriesRef.current) {
      try { macdChart.removeSeries(series); } catch {}
    }
    for (const series of rsiSeriesRef.current) {
      try { rsiChart.removeSeries(series); } catch {}
    }
    for (const series of rsiBandSeriesRef.current) {
      try { rsiChart.removeSeries(series); } catch {}
    }

    priceOverlaySeriesRef.current = [];
    auxiliarySeriesRef.current = [];
    macdLineSeriesRef.current = [];
    macdHistogramSeriesRef.current = [];
    rsiSeriesRef.current = [];
    rsiBandSeriesRef.current = [];

    candleSeries.setData(
      data.candles.map((candle) => ({
        time: ts(candle.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    );

    const chartMarkers: ChartMarker[] = [
      ...data.signals
        .filter((signal) => signal.signal != null)
        .map((signal): ChartMarker => ({
          time: ts(signal.time),
          position: signal.signal === "buy" ? "belowBar" : "aboveBar",
          color: signal.signal === "buy" ? "#22c55e" : "#f43f5e",
          shape: signal.signal === "buy" ? "arrowUp" : "arrowDown",
          text: signal.signal === "buy" ? "BUY" : "SELL",
        })),
      ...data.markers.map((marker): ChartMarker => ({
        time: ts(marker.time),
        position: marker.position,
        color: marker.color,
        shape: marker.shape,
        text: marker.text,
      })),
    ];
    candleSeries.setMarkers(chartMarkers);

    const times = data.candles.map((candle) => ts(candle.time));
    const closeValues = data.candles.map((candle) => candle.close);
    const indicatorEntries = Object.entries(data.indicators).filter(([key]) => !isOscillatorIndicator(key));
    const overlayIndicators = indicatorEntries
      .filter(([key, values]) => !isSeparateIndicator(key, values, closeValues, data.indicator_separate))
      .slice(0, 6);
    const auxiliaryIndicators = indicatorEntries
      .filter(([key, values]) => isSeparateIndicator(key, values, closeValues, data.indicator_separate))
      .slice(0, 6);

    overlayIndicators.forEach(([key, values], index) => {
      const lineSeries = priceChart.addLineSeries({
        color: INDICATOR_COLORS[index % INDICATOR_COLORS.length],
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        title: key,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      lineSeries.setData(buildLineData(times, values));
      priceOverlaySeriesRef.current.push(lineSeries);
    });

    auxiliaryIndicators.forEach(([key, values], index) => {
      const lineSeries = auxiliaryChart.addLineSeries({
        color: INDICATOR_COLORS[index % INDICATOR_COLORS.length],
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        title: key,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      lineSeries.setData(buildLineData(times, values));
      auxiliarySeriesRef.current.push(lineSeries);
    });

    const momentumMacd = buildMomentumMacd(data.candles);
    const macdHistogramSeries = macdChart.addHistogramSeries({
      base: 0,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    macdHistogramSeries.setData(momentumMacd.histogram);
    macdHistogramSeriesRef.current.push(macdHistogramSeries);

    const macdLineSeries = macdChart.addLineSeries({
      color: "#38bdf8",
      lineWidth: 2,
      priceLineVisible: false,
      title: "MACD 3-10",
    });
    macdLineSeries.setData(momentumMacd.macdLine);
    macdLineSeriesRef.current.push(macdLineSeries);

    const signalLineSeries = macdChart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 2,
      priceLineVisible: false,
      title: "Signal 16",
    });
    signalLineSeries.setData(momentumMacd.signalLine);
    macdLineSeriesRef.current.push(signalLineSeries);

    const rsiEntries = Object.entries(data.indicators).filter(([key]) => key.toLowerCase().startsWith("rsi"));
    const rsiValues = rsiEntries.length > 0 ? buildLineData(times, rsiEntries[0][1]) : buildFallbackRsi(data.candles);
    const rsiSeries = rsiChart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 2,
      priceLineVisible: false,
      title: rsiEntries[0]?.[0] ?? "rsi_14",
    });
    rsiSeries.setData(rsiValues);
    rsiSeriesRef.current.push(rsiSeries);

    const rsiBand70 = rsiChart.addLineSeries({
      color: "rgba(239, 68, 68, 0.7)",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "70",
    });
    rsiBand70.setData(buildLevelData(times, 70));
    rsiBandSeriesRef.current.push(rsiBand70);

    const rsiBand30 = rsiChart.addLineSeries({
      color: "rgba(16, 185, 129, 0.7)",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "30",
    });
    rsiBand30.setData(buildLevelData(times, 30));
    rsiBandSeriesRef.current.push(rsiBand30);

    syncReadyRef.current = true;
    priceChart.timeScale().fitContent();
    const range = priceChart.timeScale().getVisibleLogicalRange();
    if (range) {
      auxiliaryChart.timeScale().setVisibleLogicalRange(range);
      macdChart.timeScale().setVisibleLogicalRange(range);
      rsiChart.timeScale().setVisibleLogicalRange(range);
    }
  }, [data]);

  async function runBacktest() {
    setLoading(true);
    setError(null);
    try {
      const isValid = await validateScript();
      if (!isValid) {
        return;
      }

      const res = await fetch(`${API_BASE}/api/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, timeframe, limit, script }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadBacktestDetail(nextSymbol: string, nextTimeframe: Timeframe) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: nextSymbol, timeframe: nextTimeframe, limit, script }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      setSymbol(nextSymbol);
      setTimeframe(nextTimeframe);
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function runBatchBacktest() {
    setBatchLoading(true);
    setBatchError(null);
    try {
      const isValid = await validateScript();
      if (!isValid) {
        return;
      }

      const symbols = parseSymbols(symbolsText);
      if (symbols.length === 0) {
        throw new Error("Add at least one stock symbol.");
      }

      const res = await fetch(`${API_BASE}/api/backtest/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols, timeframes: BATCH_TIMEFRAMES, limit, script }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }

      const body = await res.json();
      const nextRows = (body.rows ?? []) as BatchRow[];
      setBatchRows(nextRows);
      setBatchAggregate((body.aggregate ?? null) as BatchAggregate | null);
    } catch (err) {
      setBatchError(err instanceof Error ? err.message : String(err));
      setBatchRows([]);
      setBatchAggregate(null);
    } finally {
      setBatchLoading(false);
    }
  }

  const summary = data?.summary;
  const symbolHasMovement = new Map<string, boolean>();
  for (const row of batchRows) {
    const hasMovement = row.status === "error" || hasTrades(row.summary);
    symbolHasMovement.set(row.symbol, (symbolHasMovement.get(row.symbol) ?? false) || hasMovement);
  }
  const visibleBatchRows = [...batchRows]
    .filter((row) => symbolHasMovement.get(row.symbol) ?? true)
    .sort((left, right) => {
      const result = compareBatchRows(left, right, batchSortKey);
      return batchSortDirection === "asc" ? result : -result;
    });

  function toggleBatchSort(sortKey: BatchSortKey) {
    if (batchSortKey === sortKey) {
      setBatchSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setBatchSortKey(sortKey);
    setBatchSortDirection(sortKey === "symbol" || sortKey === "timeframe" || sortKey === "status" ? "asc" : "desc");
  }

  function renderSortLabel(label: string, sortKey: BatchSortKey) {
    if (batchSortKey !== sortKey) {
      return label;
    }
    return `${label} ${batchSortDirection === "asc" ? "▲" : "▼"}`;
  }

  return (
    <div className="backtest-shell">
      <div className="backtest-top">
        <div className="control-bar">
          <label>
            Bars
            <input type="number" min={50} max={10000} value={limit} onChange={(event) => setLimit(Number(event.target.value))} />
          </label>
          <button type="button" onClick={runBatchBacktest} disabled={batchLoading || loading}>
            {batchLoading ? "Running batch..." : "> Run selected stocks"}
          </button>
          <button type="button" onClick={runBacktest} disabled={loading || batchLoading}>
            {loading ? "Loading chart..." : `> Reload chart ${symbol} ${timeframe}`}
          </button>
        </div>

        <div className="backtest-stock-picker">
          <div className="backtest-stock-picker-header">
            <strong>Selected Stocks</strong>
            <span>Runs every symbol on 5m and 15m using about one trading month of bars</span>
          </div>
          <textarea
            value={symbolsText}
            onChange={(event) => setSymbolsText(event.target.value.toUpperCase())}
            spellCheck={false}
          />
        </div>

        <div className={`strategy-editor${scriptError ? " has-error" : scriptValidated ? " is-valid" : ""}`}>
          <Editor
            height="100%"
            defaultLanguage="python"
            theme="vs-dark"
            value={script}
            onMount={handleEditorMount}
            onChange={(value) => {
              setScript(value ?? "");
              setScriptValidated(false);
              setScriptError(null);
              clearScriptMarkers();
            }}
            options={{
              minimap: { enabled: false },
              lineNumbers: "on",
              fontSize: 13,
              fontFamily: "Cascadia Code, Fira Code, Consolas, monospace",
              scrollBeyondLastLine: false,
              wordWrap: "off",
              automaticLayout: true,
              tabSize: 4,
              insertSpaces: true,
              padding: { top: 14, bottom: 14 },
            }}
          />
        </div>
        {scriptError ? <p className="script-validation error">{scriptError}</p> : null}
        {!scriptError && scriptValidated ? <p className="script-validation success">Python script is valid.</p> : null}
      </div>

      {error ? <p className="error-banner">{error}</p> : null}
      {batchError ? <p className="error-banner">{batchError}</p> : null}

      {batchAggregate ? (
        <div className="backtest-summary">
          <div><span>Batch Trades</span><strong>{batchAggregate.overall.num_trades}</strong></div>
          <div>
            <span>Batch Win rate</span>
            <strong style={{ color: hasTrades(batchAggregate.overall) ? "#e2e8f0" : "#94a3b8" }}>
              {hasTrades(batchAggregate.overall) ? `${batchAggregate.overall.win_rate}%` : "N/A"}
            </strong>
          </div>
          <div>
            <span>Total P&amp;L All</span>
            <strong style={{ color: hasTrades(batchAggregate.overall) ? (batchAggregate.overall.total_pnl >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
              {hasTrades(batchAggregate.overall) ? formatSigned(batchAggregate.overall.total_pnl) : "N/A"}
            </strong>
          </div>
          <div>
            <span>Total P&amp;L % All</span>
            <strong style={{ color: hasTrades(batchAggregate.overall) ? (batchAggregate.overall.total_pnl_pct >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
              {hasTrades(batchAggregate.overall) ? formatSigned(batchAggregate.overall.total_pnl_pct, "%") : "N/A"}
            </strong>
          </div>
        </div>
      ) : null}

      {batchAggregate && batchAggregate.daily.length > 0 ? (
        <div className="backtest-trades backtest-batch-results">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Trades</th>
                <th>Win rate</th>
                <th>Total P&amp;L</th>
                <th>Total P&amp;L %</th>
              </tr>
            </thead>
            <tbody>
              {batchAggregate.daily.map((day) => (
                <tr key={day.date}>
                  <td>{day.date}</td>
                  <td>{day.num_trades}</td>
                  <td style={{ color: hasTrades(day) ? "#e2e8f0" : "#94a3b8" }}>{hasTrades(day) ? `${day.win_rate}%` : "N/A"}</td>
                  <td style={{ color: hasTrades(day) ? (day.total_pnl >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                    {hasTrades(day) ? formatSigned(day.total_pnl) : "N/A"}
                  </td>
                  <td style={{ color: hasTrades(day) ? (day.total_pnl_pct >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                    {hasTrades(day) ? formatSigned(day.total_pnl_pct, "%") : "N/A"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {batchRows.length > 0 ? (
        <div className="backtest-trades backtest-batch-results">
          <table>
            <thead>
              <tr>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("symbol")}>{renderSortLabel("Symbol", "symbol")}</button></th>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("timeframe")}>{renderSortLabel("Timeframe", "timeframe")}</button></th>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("status")}>{renderSortLabel("Status", "status")}</button></th>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("trades")}>{renderSortLabel("Trades", "trades")}</button></th>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("winRate")}>{renderSortLabel("Win rate", "winRate")}</button></th>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("pnl")}>{renderSortLabel("Total P&amp;L", "pnl")}</button></th>
                <th><button type="button" className="table-sort-button" onClick={() => toggleBatchSort("pnlPct")}>{renderSortLabel("P&amp;L %", "pnlPct")}</button></th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {visibleBatchRows.map((row) => {
                const isSelected = row.symbol === symbol && row.timeframe === timeframe;
                return (
                  <tr
                    key={`${row.symbol}-${row.timeframe}`}
                    className={row.status === "ok" ? "is-clickable" : undefined}
                    data-selected={isSelected ? "true" : undefined}
                    onClick={() => {
                      if (row.status === "ok") {
                        void loadBacktestDetail(row.symbol, row.timeframe);
                      }
                    }}
                  >
                    <td>{row.symbol}</td>
                    <td>{row.timeframe}</td>
                    <td>{row.status}</td>
                    <td>{row.summary?.num_trades ?? "-"}</td>
                    <td style={{ color: hasTrades(row.summary) ? "#e2e8f0" : "#94a3b8" }}>
                      {row.summary ? (hasTrades(row.summary) ? `${row.summary.win_rate}%` : "N/A") : "-"}
                    </td>
                    <td style={{ color: hasTrades(row.summary) ? ((row.summary?.total_pnl ?? 0) >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                      {row.summary ? (hasTrades(row.summary) ? formatSigned(row.summary.total_pnl) : "N/A") : "-"}
                    </td>
                    <td style={{ color: hasTrades(row.summary) ? ((row.summary?.total_pnl_pct ?? 0) >= 0 ? "#10b981" : "#ef4444") : "#94a3b8" }}>
                      {row.summary ? (hasTrades(row.summary) ? formatSigned(row.summary.total_pnl_pct, "%") : "N/A") : "-"}
                    </td>
                    <td>{row.error ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {summary ? (
        <div className="backtest-summary">
          <div><span>Detail</span><strong>{symbol} {timeframe}</strong></div>
          <div><span>Trades</span><strong>{summary.num_trades}</strong></div>
          <div><span>Win rate</span><strong>{summary.win_rate}%</strong></div>
          <div>
            <span>Total P&amp;L</span>
            <strong style={{ color: summary.total_pnl >= 0 ? "#10b981" : "#ef4444" }}>
              {summary.total_pnl >= 0 ? "+" : ""}{summary.total_pnl.toFixed(2)}
            </strong>
          </div>
          <div>
            <span>P&amp;L %</span>
            <strong style={{ color: summary.total_pnl_pct >= 0 ? "#10b981" : "#ef4444" }}>
              {summary.total_pnl_pct >= 0 ? "+" : ""}{summary.total_pnl_pct.toFixed(2)}%
            </strong>
          </div>
        </div>
      ) : null}

      <div className="backtest-chart-stack">
        <div className="backtest-chart" ref={priceContainerRef} />
        <div className="backtest-indicator-wrap">
          <div className="backtest-indicator-header">
            <strong>Auxiliary Indicators</strong>
            <span>ATR / low-scale / state outputs</span>
          </div>
          <div className="backtest-indicator-chart" ref={auxiliaryContainerRef} />
        </div>
        <div className="backtest-indicator-wrap">
          <div className="backtest-indicator-header">
            <strong>Momentum MACD</strong>
            <span>Linda Raschke 3-10-16</span>
          </div>
          <div className="backtest-indicator-chart" ref={macdContainerRef} />
        </div>
        <div className="backtest-indicator-wrap">
          <div className="backtest-indicator-header">
            <strong>RSI</strong>
            <span>RSI 14 with 30/70 bands</span>
          </div>
          <div className="backtest-indicator-chart" ref={rsiContainerRef} />
        </div>
      </div>

      {data && data.trades.length > 0 ? (
        <div className="backtest-trades">
          <table>
            <thead>
              <tr>
                <th>Entry</th><th>Entry $</th>
                <th>Exit</th><th>Exit $</th>
                <th>P&amp;L</th><th>P&amp;L %</th>
              </tr>
            </thead>
            <tbody>
              {data.trades.map((trade, index) => (
                <tr key={index}>
                  <td>{new Date(trade.entry_time).toLocaleString()}</td>
                  <td>{trade.entry_price.toFixed(2)}</td>
                  <td>{new Date(trade.exit_time).toLocaleString()}</td>
                  <td>{trade.exit_price.toFixed(2)}</td>
                  <td style={{ color: trade.pnl >= 0 ? "#10b981" : "#ef4444" }}>
                    {trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)}
                  </td>
                  <td style={{ color: trade.pnl_pct >= 0 ? "#10b981" : "#ef4444" }}>
                    {trade.pnl_pct >= 0 ? "+" : ""}{trade.pnl_pct.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
