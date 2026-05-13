import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";

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
};

function toTs(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function snapToMinute(iso: string): UTCTimestamp {
  const d = new Date(iso);
  d.setUTCSeconds(0, 0);
  return Math.floor(d.getTime() / 1000) as UTCTimestamp;
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

export default function MiniSymbolChart({ candles, trades, symbol }: Props) {
  const priceRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!priceRef.current || !rsiRef.current || candles.length === 0) return;

    if (priceChartRef.current) { priceChartRef.current.remove(); priceChartRef.current = null; }
    if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null; }

    const width = priceRef.current.clientWidth;
    const chartOpts = {
      width,
      layout: {
        background: { type: ColorType.Solid as const, color: "transparent" },
        textColor: "#94a3b8",
        fontSize: 10,
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

    // ── Price chart ──
    const priceChart = createChart(priceRef.current, { ...chartOpts, height: 180 });
    priceChartRef.current = priceChart;

    const candleTsSet = new Set(candles.map((c) => toTs(c.time) as number));

    const candleSeries = priceChart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
      priceLineVisible: false,
      lastValueVisible: true,
    });
    candleSeries.setData(
      candles.map((c) => ({ time: toTs(c.time), open: c.open, high: c.high, low: c.low, close: c.close })),
    );

    // VWAP
    const vwapData = buildIntradayVwap(candles);
    if (vwapData.length > 0) {
      const vwapSeries = priceChart.addLineSeries({
        color: "#f59e0b",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "VWAP",
      });
      vwapSeries.setData(vwapData);
    }

    // Trade markers
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

    const symbolTrades = trades.filter((t) => t.symbol === symbol);
    const markers: Array<{
      time: UTCTimestamp;
      position: "belowBar" | "aboveBar";
      color: string;
      shape: "arrowUp" | "arrowDown";
      text: string;
    }> = [];

    for (const t of symbolTrades) {
      if (!t.created_at) continue;
      markers.push({
        time: nearestCandleTs(t.created_at),
        position: t.side === "buy" ? "belowBar" : "aboveBar",
        color: t.side === "buy" ? "#10b981" : "#ef4444",
        shape: t.side === "buy" ? "arrowUp" : "arrowDown",
        text: `${t.side === "buy" ? "B" : "S"} $${t.price.toFixed(2)}`,
      });
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    if (markers.length > 0) candleSeries.setMarkers(markers);

    // ── RSI chart ──
    const rsiChart = createChart(rsiRef.current, { ...chartOpts, height: 70 });
    rsiChartRef.current = rsiChart;

    const closes = candles.map((c) => c.close);
    const rsiValues = buildRsi(closes, 14);

    const rsiSeries = rsiChart.addLineSeries({
      color: "#a78bfa",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "RSI",
    });
    const rsiData = candles
      .map((c, i) => rsiValues[i] !== null ? { time: toTs(c.time), value: rsiValues[i]! } : null)
      .filter(Boolean) as { time: UTCTimestamp; value: number }[];
    rsiSeries.setData(rsiData);

    rsiSeries.createPriceLine({ price: 70, color: "rgba(239,68,68,0.4)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
    rsiSeries.createPriceLine({ price: 30, color: "rgba(16,185,129,0.4)", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });

    rsiChart.priceScale("right").applyOptions({ autoScale: false, scaleMargins: { top: 0.05, bottom: 0.05 } });
    rsiSeries.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });

    // Anchor series for RSI to keep time scales in sync
    const anchorSeries = rsiChart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, visible: false });
    anchorSeries.setData(candles.map((c) => ({ time: toTs(c.time), value: 0 })));

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

    priceChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time) { rsiChart.clearCrosshairPosition(); return; }
      const pt = rsiData.find((d) => (d.time as number) === (param.time as number));
      if (pt) rsiChart.setCrosshairPosition(pt.value, pt.time, rsiSeries);
    });
    rsiChart.subscribeCrosshairMove((param) => {
      if (!param || !param.time) { priceChart.clearCrosshairPosition(); return; }
      const idx = candles.findIndex((c) => (toTs(c.time) as number) === (param.time as number));
      if (idx >= 0) priceChart.setCrosshairPosition(candles[idx].close, toTs(candles[idx].time), candleSeries);
    });

    priceChart.timeScale().fitContent();
    rsiChart.timeScale().fitContent();

    // Resize
    const observer = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        priceChart.applyOptions({ width: w });
        rsiChart.applyOptions({ width: w });
      }
    });
    observer.observe(priceRef.current);

    return () => {
      observer.disconnect();
      priceChart.remove();
      rsiChart.remove();
      priceChartRef.current = null;
      rsiChartRef.current = null;
    };
  }, [candles, trades, symbol]);

  if (candles.length === 0) {
    return <p style={{ color: "#64748b", fontSize: "0.8rem", margin: "0.5rem 0" }}>Waiting for candle data…</p>;
  }

  return (
    <div style={{ width: "100%" }}>
      <div ref={priceRef} style={{ width: "100%", height: 180, borderRadius: "8px 8px 0 0", overflow: "hidden" }} />
      <div ref={rsiRef} style={{ width: "100%", height: 70, borderRadius: "0 0 8px 8px", overflow: "hidden", borderTop: "1px solid rgba(148,163,184,0.1)" }} />
    </div>
  );
}
