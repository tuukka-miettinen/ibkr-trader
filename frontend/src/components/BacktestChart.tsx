import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";

export type PricePoint = { t: string; p: number; v: number };
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
};

function toTs(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function tradingDate(isoTime: string): string {
  const d = new Date(isoTime);
  if (d.getUTCHours() < 8) {
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return d.toISOString().slice(0, 10);
}

export default function BacktestChart({ priceData, trades, selectedDate }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || priceData.length === 0) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 300,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
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
      rightPriceScale: {
        borderColor: "rgba(148,163,184,0.15)",
      },
    });
    chartRef.current = chart;

    // Collect trade timestamps that fall on this day so we can inject
    // them into the price series.  lightweight-charts silently drops
    // markers whose timestamp doesn't exist in the series data.
    const tradePoints: Map<number, number> = new Map(); // ts → price
    for (const trade of trades) {
      for (const entry of trade.entries) {
        if (entry.time.slice(0, 10) === selectedDate || tradingDate(entry.time) === selectedDate) {
          tradePoints.set(toTs(entry.time) as number, entry.price);
        }
      }
      if (trade.exit_time.slice(0, 10) === selectedDate || tradingDate(trade.exit_time) === selectedDate) {
        tradePoints.set(toTs(trade.exit_time) as number, trade.exit_price);
      }
    }

    // Build merged price + VWAP arrays, injecting trade-time points
    const priceMap = new Map<number, { p: number; v: number }>();
    for (const pt of priceData) {
      priceMap.set(toTs(pt.t) as number, { p: pt.p, v: pt.v });
    }

    // Add trade points that are missing from the sampled series
    for (const [ts, price] of tradePoints) {
      if (!priceMap.has(ts)) {
        // Find nearest VWAP value for interpolation
        let nearestVwap = price;
        let bestDist = Infinity;
        for (const pt of priceData) {
          const d = Math.abs((toTs(pt.t) as number) - ts);
          if (d < bestDist) {
            bestDist = d;
            nearestVwap = pt.v;
          }
        }
        priceMap.set(ts, { p: price, v: nearestVwap });
      }
    }

    // Sort by time
    const mergedTimes = [...priceMap.keys()].sort((a, b) => a - b);
    const mergedPrice = mergedTimes.map((ts) => ({ time: ts as UTCTimestamp, value: priceMap.get(ts)!.p }));
    const mergedVwap = mergedTimes.map((ts) => ({ time: ts as UTCTimestamp, value: priceMap.get(ts)!.v }));

    // Price line
    const priceSeries = chart.addLineSeries({
      color: "#38bdf8",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "Price",
    });
    priceSeries.setData(mergedPrice);

    // VWAP line
    const vwapSeries = chart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 1,
      lineStyle: 2, // Dashed
      priceLineVisible: false,
      lastValueVisible: false,
      title: "VWAP",
    });
    vwapSeries.setData(mergedVwap);

    // Buy / Sell markers on the price series
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
            time: toTs(entry.time),
            position: "belowBar",
            color: "#10b981",
            shape: "arrowUp",
            text: `B $${entry.price.toFixed(2)}`,
          });
        }
      }
      if (trade.exit_time.slice(0, 10) === selectedDate || tradingDate(trade.exit_time) === selectedDate) {
        markers.push({
          time: toTs(trade.exit_time),
          position: "aboveBar",
          color: "#ef4444",
          shape: "arrowDown",
          text: `S $${trade.exit_price.toFixed(2)}`,
        });
      }
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    if (markers.length > 0) {
      priceSeries.setMarkers(markers);
    }

    chart.timeScale().fitContent();

    const observer = new ResizeObserver((entries) => {
      for (const e of entries) {
        chart.applyOptions({ width: e.contentRect.width });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [priceData, trades, selectedDate]);

  if (priceData.length === 0) return null;

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: 300, borderRadius: "8px", overflow: "hidden" }}
    />
  );
}
