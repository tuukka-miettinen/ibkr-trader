import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

const POSITIVE_COLOR = "#15803d";
const NEGATIVE_COLOR = "#b91c1c";
const NEUTRAL_COLOR = "#555555";

import type { LiveTrade } from "../lib/types";

export type MiniCandle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type Props = {
  candles: MiniCandle[];
  trades: LiveTrade[];
  symbol: string;
  sessionStartTime?: string | null;
};

function toTs(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function snapToNearest(iso: string, candleTsSet: Set<number>): UTCTimestamp {
  const snapped = Math.floor(new Date(iso).getTime() / 1000);
  if (candleTsSet.has(snapped)) return snapped as UTCTimestamp;
  let best = snapped;
  let bestDist = Infinity;
  for (const ts of candleTsSet) {
    const d = Math.abs(ts - snapped);
    if (d < bestDist) { bestDist = d; best = ts; }
  }
  return best as UTCTimestamp;
}

function buildIntradayVwap(candles: MiniCandle[]): { time: UTCTimestamp; value: number }[] {
  let cumTPV = 0;
  let cumVol = 0;
  let session = "";

  return candles.flatMap((c) => {
    const day = c.time.slice(0, 10);
    if (day !== session) {
      session = day;
      cumTPV = 0;
      cumVol = 0;
    }
    cumVol += c.volume;
    if (cumVol === 0) return [];
    cumTPV += ((c.high + c.low + c.close) / 3) * c.volume;
    return [{ time: toTs(c.time), value: +(cumTPV / cumVol).toFixed(4) }];
  });
}

function buildRsi(closes: number[], period = 14): (number | null)[] {
  const result: (number | null)[] = Array(closes.length).fill(null);
  if (closes.length <= period) return result;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    avgGain += Math.max(d, 0);
    avgLoss += Math.max(-d, 0);
  }
  avgGain /= period;
  avgLoss /= period;
  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
    result[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
}

const CHART_OPTS = {
  layout: {
    background: { type: ColorType.Solid as const, color: "#ffffff" },
    textColor: "#000000",
    fontSize: 10,
  },
  grid: {
    vertLines: { color: "rgba(0,0,0,0.08)" },
    horzLines: { color: "rgba(0,0,0,0.08)" },
  },
  crosshair: { mode: CrosshairMode.Normal },
  rightPriceScale: { borderColor: "rgba(0,0,0,0.18)" },
};

export default function MiniSymbolChart({ candles, trades, symbol, sessionStartTime }: Props) {
  const priceRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const renderedCountRef = useRef(0);
  const observerRef = useRef<ResizeObserver | null>(null);

  // ── Create chart once per symbol ──
  useEffect(() => {
    if (!priceRef.current || !rsiRef.current) return;

    const width = priceRef.current.clientWidth;

    // ── Price chart ──
    const priceChart = createChart(priceRef.current, {
      ...CHART_OPTS,
      width,
      height: 180,
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: "rgba(0,0,0,0.18)",
      },
    });
    priceChartRef.current = priceChart;

    const candleSeries = priceChart.addCandlestickSeries({
      upColor: POSITIVE_COLOR,
      downColor: NEGATIVE_COLOR,
      borderUpColor: POSITIVE_COLOR,
      borderDownColor: NEGATIVE_COLOR,
      wickUpColor: POSITIVE_COLOR,
      wickDownColor: NEGATIVE_COLOR,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    candleSeriesRef.current = candleSeries;

    const vwapSeries = priceChart.addLineSeries({
      color: NEUTRAL_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "VWAP",
    });
    vwapSeriesRef.current = vwapSeries;

    // ── RSI chart ──
    const rsiChart = createChart(rsiRef.current, {
      ...CHART_OPTS,
      width,
      height: 70,
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: "rgba(0,0,0,0.18)",
      },
    });
    rsiChartRef.current = rsiChart;

    const rsiSeries = rsiChart.addLineSeries({
      color: NEUTRAL_COLOR,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "RSI",
    });
    rsiSeriesRef.current = rsiSeries;

    rsiSeries.createPriceLine({ price: 70, color: "rgba(185,28,28,0.45)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
    rsiSeries.createPriceLine({ price: 30, color: "rgba(21,128,61,0.45)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
    rsiChart.priceScale("right").applyOptions({ autoScale: false, scaleMargins: { top: 0.05, bottom: 0.05 } });
    rsiSeries.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });

    // Sync crosshairs & time scales
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

    // Resize
    const observer = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        priceChart.applyOptions({ width: w });
        rsiChart.applyOptions({ width: w });
      }
    });
    observer.observe(priceRef.current);
    observerRef.current = observer;

    renderedCountRef.current = 0;

    return () => {
      observer.disconnect();
      priceChart.remove();
      rsiChart.remove();
      priceChartRef.current = null;
      rsiChartRef.current = null;
      candleSeriesRef.current = null;
      vwapSeriesRef.current = null;
      rsiSeriesRef.current = null;
      renderedCountRef.current = 0;
    };
  }, [symbol]);

  // ── Update data incrementally ──
  useEffect(() => {
    const cs = candleSeriesRef.current;
    const vs = vwapSeriesRef.current;
    const rs = rsiSeriesRef.current;
    const pc = priceChartRef.current;
    if (!cs || !vs || !rs || !pc || candles.length === 0) return;

    const prevCount = renderedCountRef.current;

    if (prevCount === 0) {
      // First render — set all data
      cs.setData(
        candles.map((c) => ({ time: toTs(c.time), open: c.open, high: c.high, low: c.low, close: c.close })),
      );
      vs.setData(buildIntradayVwap(candles));

      const closes = candles.map((c) => c.close);
      const rsiValues = buildRsi(closes, 14);
      const rsiData = candles
        .map((c, i) => rsiValues[i] !== null ? { time: toTs(c.time), value: rsiValues[i]! } : null)
        .filter(Boolean) as { time: UTCTimestamp; value: number }[];
      rs.setData(rsiData);

      // Anchor series for RSI time scale
      const rsiChart = rsiChartRef.current;
      if (rsiChart) {
        const anchor = rsiChart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, visible: false });
        anchor.setData(candles.map((c) => ({ time: toTs(c.time), value: 0 })));
      }

      pc.timeScale().scrollToRealTime();
      renderedCountRef.current = candles.length;
    } else if (candles.length > prevCount) {
      // Incremental update — just update/add new bars
      for (let i = prevCount; i < candles.length; i++) {
        const c = candles[i];
        cs.update({ time: toTs(c.time), open: c.open, high: c.high, low: c.low, close: c.close });
      }

      // Update VWAP for new bars
      const vwapData = buildIntradayVwap(candles);
      for (let i = Math.max(0, prevCount - 1); i < vwapData.length; i++) {
        vs.update(vwapData[i]);
      }

      // Update RSI for new bars
      const closes = candles.map((c) => c.close);
      const rsiValues = buildRsi(closes, 14);
      for (let i = Math.max(0, prevCount - 1); i < candles.length; i++) {
        if (rsiValues[i] !== null) {
          rs.update({ time: toTs(candles[i].time), value: rsiValues[i]! });
        }
      }

      renderedCountRef.current = candles.length;
    }
  }, [candles]);

  // ── Update trade markers ──
  useEffect(() => {
    const cs = candleSeriesRef.current;
    if (!cs || candles.length === 0) return;

    const candleTsSet = new Set(candles.map((c) => toTs(c.time) as number));
    const symbolTrades = trades.filter((t) => t.symbol === symbol);
    const markers: Array<{
      time: UTCTimestamp;
      position: "belowBar" | "aboveBar";
      color: string;
      shape: "arrowUp" | "arrowDown" | "square" | "circle";
      text: string;
    }> = [];

    for (const t of symbolTrades) {
      if (!t.created_at) continue;
      markers.push({
        time: snapToNearest(t.created_at, candleTsSet),
        position: t.side === "buy" ? "belowBar" : "aboveBar",
        color: t.side === "buy" ? POSITIVE_COLOR : NEGATIVE_COLOR,
        shape: t.side === "buy" ? "arrowUp" : "arrowDown",
        text: `${t.side === "buy" ? "B" : "S"} $${t.price.toFixed(2)}`,
      });
    }

    // Session start marker
    if (sessionStartTime) {
      markers.push({
        time: snapToNearest(sessionStartTime, candleTsSet),
        position: "aboveBar",
        color: NEUTRAL_COLOR,
        shape: "square",
        text: "▶ Session",
      });
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    cs.setMarkers(markers);
  }, [trades, candles.length, symbol, sessionStartTime]);

  if (candles.length === 0) {
    return <p style={{ color: "#5a5a5a", fontSize: "0.8rem", margin: "0.5rem 0" }}>Waiting for candle data…</p>;
  }

  return (
    <div style={{ width: "100%" }}>
      <div ref={priceRef} style={{ width: "100%", height: 180, overflow: "hidden", border: "1px solid #000000" }} />
      <div ref={rsiRef} style={{ width: "100%", height: 70, overflow: "hidden", border: "1px solid #000000", borderTop: "0" }} />
    </div>
  );
}
