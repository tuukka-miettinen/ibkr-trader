import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";

export type PricePoint = { t: string; o: number; h: number; l: number; c: number; v: number };
export type TradeEntry = { time: string; price: number; shares: number; cost: number };
export type TradeData = {
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  dollar_pnl: number;
  pnl_pct: number;
  shares: number;
  entries: TradeEntry[];
};

type Props = {
  priceData: PricePoint[];
  trades: TradeData[];
  selectedDate: string;
  openEntries?: TradeEntry[];
};

function toTs(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

/** Snap an ISO timestamp down to the start of its minute. */
function snapToMinute(iso: string): UTCTimestamp {
  const d = new Date(iso);
  d.setUTCSeconds(0, 0);
  return Math.floor(d.getTime() / 1000) as UTCTimestamp;
}

function tradingDate(isoTime: string): string {
  const d = new Date(isoTime);
  if (d.getUTCHours() < 8) {
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return d.toISOString().slice(0, 10);
}

function buildRsi(closes: number[], period = 14): (number | null)[] {
  const result: (number | null)[] = Array(closes.length).fill(null);
  if (closes.length <= period) return result;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const delta = closes[i] - closes[i - 1];
    avgGain += Math.max(delta, 0);
    avgLoss += Math.max(-delta, 0);
  }
  avgGain /= period;
  avgLoss /= period;

  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(delta, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-delta, 0)) / period;
    result[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

export default function BacktestChart({ priceData, trades, selectedDate, openEntries = [] }: Props) {
  const priceContainerRef = useRef<HTMLDivElement>(null);
  const rsiContainerRef = useRef<HTMLDivElement>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!priceContainerRef.current || !rsiContainerRef.current || priceData.length === 0) return;

    // Tear down previous charts
    if (priceChartRef.current) { priceChartRef.current.remove(); priceChartRef.current = null; }
    if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null; }

    const width = priceContainerRef.current.clientWidth;

    const chartOpts = {
      width,
      layout: {
        background: { type: ColorType.Solid as const, color: "transparent" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(148,163,184,0.06)" },
        horzLines: { color: "rgba(148,163,184,0.06)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: "rgba(148,163,184,0.15)",
      },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.15)" },
    };

    // ── Price chart ──────────────────────────────────────────────────
    const priceChart = createChart(priceContainerRef.current, { ...chartOpts, height: 260 });
    priceChartRef.current = priceChart;

    // Build a set of candle timestamps for snapping trade markers
    const candleTsSet = new Set(priceData.map((p) => toTs(p.t) as number));

    // Candlestick series
    const candleSeries = priceChart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    candleSeries.setData(
      priceData.map((p) => ({
        time: toTs(p.t),
        open: p.o,
        high: p.h,
        low: p.l,
        close: p.c,
      })),
    );

    // VWAP overlay
    const vwapSeries = priceChart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "VWAP",
    });
    vwapSeries.setData(priceData.map((p) => ({ time: toTs(p.t), value: p.v })));

    // ── Trade markers ────────────────────────────────────────────────
    // Snap trade times to the nearest candle minute so markers render
    function nearestCandleTs(tradeIso: string): UTCTimestamp {
      const snapped = snapToMinute(tradeIso) as number;
      if (candleTsSet.has(snapped)) return snapped as UTCTimestamp;
      let best = snapped;
      let bestDist = Infinity;
      for (const ts of candleTsSet) {
        const d = Math.abs(ts - snapped);
        if (d < bestDist) { bestDist = d; best = ts; }
      }
      return best as UTCTimestamp;
    }

    const markers: Array<{
      time: UTCTimestamp;
      position: "belowBar" | "aboveBar";
      color: string;
      shape: "arrowUp" | "arrowDown";
      text: string;
    }> = [];

    for (const trade of trades) {
      for (const entry of trade.entries) {
        if (entry.time.slice(0, 10) === selectedDate || tradingDate(entry.time) === selectedDate) {
          markers.push({
            time: nearestCandleTs(entry.time),
            position: "belowBar",
            color: "#10b981",
            shape: "arrowUp",
            text: `B $${entry.price.toFixed(2)}`,
          });
        }
      }
      if (trade.exit_time.slice(0, 10) === selectedDate || tradingDate(trade.exit_time) === selectedDate) {
        markers.push({
          time: nearestCandleTs(trade.exit_time),
          position: "aboveBar",
          color: "#ef4444",
          shape: "arrowDown",
          text: `S $${trade.exit_price.toFixed(2)}`,
        });
      }
    }
    // Buy markers for open position entries (buys with no sell yet)
    for (const entry of openEntries) {
      if (entry.time.slice(0, 10) === selectedDate || tradingDate(entry.time) === selectedDate) {
        markers.push({
          time: nearestCandleTs(entry.time),
          position: "belowBar",
          color: "#10b981",
          shape: "arrowUp",
          text: `B $${entry.price.toFixed(2)}`,
        });
      }
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    if (markers.length > 0) candleSeries.setMarkers(markers);

    // ── RSI chart ────────────────────────────────────────────────────
    const rsiChart = createChart(rsiContainerRef.current, { ...chartOpts, height: 100 });
    rsiChartRef.current = rsiChart;

    const closes = priceData.map((p) => p.c);
    const rsiValues = buildRsi(closes, 14);

    const rsiSeries = rsiChart.addLineSeries({
      color: "#a78bfa",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "RSI 14",
    });
    const rsiData = priceData
      .map((p, i) => rsiValues[i] !== null ? { time: toTs(p.t), value: rsiValues[i]! } : null)
      .filter(Boolean) as { time: UTCTimestamp; value: number }[];
    rsiSeries.setData(rsiData);

    // RSI reference lines at 30 and 70
    rsiSeries.createPriceLine({ price: 70, color: "rgba(239,68,68,0.4)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
    rsiSeries.createPriceLine({ price: 30, color: "rgba(16,185,129,0.4)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });

    // Fix RSI scale to 0-100
    rsiChart.priceScale("right").applyOptions({ autoScale: false, scaleMargins: { top: 0.05, bottom: 0.05 } });
    rsiSeries.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });

    // Invisible series with a point at every candle timestamp so logical indices match the price chart
    const anchorSeries = rsiChart.addLineSeries({
      priceLineVisible: false,
      lastValueVisible: false,
      visible: false,
    });
    anchorSeries.setData(priceData.map((p) => ({ time: toTs(p.t), value: 0 })));

    // ── Sync crosshairs & time scales ────────────────────────────────
    let syncing = false;

    priceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range) return;
      syncing = true;
      rsiChart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    });
    rsiChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range) return;
      syncing = true;
      priceChart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    });

    priceChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time) {
        rsiChart.clearCrosshairPosition();
        return;
      }
      const point = rsiData.find((d) => (d.time as number) === (param.time as number));
      if (point) {
        rsiChart.setCrosshairPosition(point.value, point.time, rsiSeries);
      }
    });
    rsiChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time) {
        priceChart.clearCrosshairPosition();
        return;
      }
      const idx = priceData.findIndex((p) => (toTs(p.t) as number) === (param.time as number));
      if (idx >= 0) {
        priceChart.setCrosshairPosition(priceData[idx].c, toTs(priceData[idx].t), candleSeries);
      }
    });

    priceChart.timeScale().fitContent();
    rsiChart.timeScale().fitContent();

    // ── Resize observer ──────────────────────────────────────────────
    const observer = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        priceChart.applyOptions({ width: w });
        rsiChart.applyOptions({ width: w });
      }
    });
    observer.observe(priceContainerRef.current);

    return () => {
      observer.disconnect();
      priceChart.remove();
      rsiChart.remove();
      priceChartRef.current = null;
      rsiChartRef.current = null;
    };
  }, [priceData, trades, selectedDate]);

  if (priceData.length === 0) return null;

  return (
    <div style={{ width: "100%" }}>
      <div
        ref={priceContainerRef}
        style={{ width: "100%", height: 260, borderRadius: "8px 8px 0 0", overflow: "hidden" }}
      />
      <div
        ref={rsiContainerRef}
        style={{ width: "100%", height: 100, borderRadius: "0 0 8px 8px", overflow: "hidden", borderTop: "1px solid rgba(148,163,184,0.1)" }}
      />
    </div>
  );
}
