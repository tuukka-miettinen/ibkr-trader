import { useEffect, useRef } from "react";
import { ColorType, CrosshairMode, LineStyle, createChart, type IChartApi, type ISeriesApi, type LogicalRange, type UTCTimestamp } from "lightweight-charts";

import type { Candle, Timeframe, TimelineEvent } from "../lib/types";

const POSITIVE_COLOR = "#15803d";
const NEGATIVE_COLOR = "#b91c1c";
const NEUTRAL_COLOR = "#555555";

type Props = {
  candles: Candle[];
  events: TimelineEvent[];
  timeframe: Timeframe;
};

type LinePoint = {
  time: UTCTimestamp;
  value: number;
};

type HistogramPoint = {
  time: UTCTimestamp;
  value: number;
  color: string;
};

function toTimestamp(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function buildIntradayVwap(candles: Candle[]): LinePoint[] {
  let cumulativeTypicalPriceVolume = 0;
  let cumulativeVolume = 0;
  let currentSession = "";

  return candles.flatMap((candle) => {
    const session = candle.time.slice(0, 10);
    if (session !== currentSession) {
      currentSession = session;
      cumulativeTypicalPriceVolume = 0;
      cumulativeVolume = 0;
    }

    cumulativeVolume += candle.volume;
    if (cumulativeVolume === 0) {
      return [];
    }

    const typicalPrice = (candle.high + candle.low + candle.close) / 3;
    cumulativeTypicalPriceVolume += typicalPrice * candle.volume;

    return [{
      time: toTimestamp(candle.time),
      value: Number((cumulativeTypicalPriceVolume / cumulativeVolume).toFixed(4)),
    }];
  });
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
    result[index] = emaValue;
  }

  return result;
}

function buildMomentumMacd(candles: Candle[]) {
  const closes = candles.map((candle) => candle.close);
  const fastEma = buildEma(closes, 3);
  const slowEma = buildEma(closes, 10);
  const macdValues = closes.map((_, index) => (
    fastEma[index] !== null && slowEma[index] !== null
      ? Number((fastEma[index]! - slowEma[index]!).toFixed(4))
      : null
  ));

  const signalSeed = macdValues.filter((value): value is number => value !== null);
  const signalValues: Array<number | null> = Array.from({ length: candles.length }, () => null);
  if (signalSeed.length >= 16) {
    const firstMacdIndex = macdValues.findIndex((value) => value !== null);
    let signalValue = signalSeed.slice(0, 16).reduce((sum, value) => sum + value, 0) / 16;
    const signalStartIndex = firstMacdIndex + 15;
    signalValues[signalStartIndex] = signalValue;

    const smoothing = 2 / (16 + 1);
    for (let index = signalStartIndex + 1; index < macdValues.length; index += 1) {
      const macdValue = macdValues[index];
      if (macdValue === null) {
        continue;
      }
      signalValue = macdValue * smoothing + signalValue * (1 - smoothing);
      signalValues[index] = signalValue;
    }
  }

  const macdLine: LinePoint[] = [];
  const signalLine: LinePoint[] = [];
  const histogram: HistogramPoint[] = [];

  candles.forEach((candle, index) => {
    const time = toTimestamp(candle.time);
    const macdValue = macdValues[index];
    const signalValue = signalValues[index];

    if (macdValue !== null) {
      macdLine.push({ time, value: macdValue });
    }
    if (signalValue !== null) {
      signalLine.push({ time, value: Number(signalValue.toFixed(4)) });
    }
    if (macdValue !== null && signalValue !== null) {
      const histogramValue = Number((macdValue - signalValue).toFixed(4));
      histogram.push({
        time,
        value: histogramValue,
        color: histogramValue >= 0 ? "rgba(21, 128, 61, 0.78)" : "rgba(185, 28, 28, 0.78)",
      });
    }
  });

  return { macdLine, signalLine, histogram };
}

function buildRsi(candles: Candle[], period = 14): LinePoint[] {
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
  const firstTime = toTimestamp(candles[period].time);
  const firstValue = averageLoss === 0 ? 100 : 100 - (100 / (1 + averageGain / averageLoss));
  points.push({ time: firstTime, value: Number(firstValue.toFixed(2)) });

  for (let index = period + 1; index < closes.length; index += 1) {
    const delta = closes[index] - closes[index - 1];
    const gain = Math.max(delta, 0);
    const loss = Math.max(-delta, 0);
    averageGain = ((averageGain * (period - 1)) + gain) / period;
    averageLoss = ((averageLoss * (period - 1)) + loss) / period;
    const relativeStrength = averageLoss === 0 ? Number.POSITIVE_INFINITY : averageGain / averageLoss;
    const rsi = averageLoss === 0 ? 100 : 100 - (100 / (1 + relativeStrength));
    points.push({ time: toTimestamp(candles[index].time), value: Number(rsi.toFixed(2)) });
  }

  return points;
}

function buildLevelLine(candles: Candle[], level: number): LinePoint[] {
  return candles.map((candle) => ({ time: toTimestamp(candle.time), value: level }));
}

export default function CandlestickChart({ candles, events, timeframe }: Props) {
  const priceContainerRef = useRef<HTMLDivElement | null>(null);
  const macdContainerRef = useRef<HTMLDivElement | null>(null);
  const rsiContainerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const macdChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const signalSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const histogramSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiUpperBandRef = useRef<ISeriesApi<"Line"> | null>(null);
  const rsiLowerBandRef = useRef<ISeriesApi<"Line"> | null>(null);
  const indicatorRangeSyncReadyRef = useRef(false);
  // Tracks the first candle's timestamp of the last snapshot so we can
  // distinguish a full dataset replacement from a single live-tick update.
  const snapshotAnchorRef = useRef<string>("");

  useEffect(() => {
    if (!priceContainerRef.current || !macdContainerRef.current || !rsiContainerRef.current) {
      return undefined;
    }

    const chart = createChart(priceContainerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#000000",
      },
      grid: {
        vertLines: { color: "rgba(0, 0, 0, 0.12)" },
        horzLines: { color: "rgba(0, 0, 0, 0.12)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: "rgba(0, 0, 0, 0.32)",
      },
      timeScale: {
        borderColor: "rgba(0, 0, 0, 0.32)",
        timeVisible: true,
      },
    });

    const macdChart = createChart(macdContainerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#000000",
      },
      grid: {
        vertLines: { color: "rgba(0, 0, 0, 0.08)" },
        horzLines: { color: "rgba(0, 0, 0, 0.12)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: "rgba(0, 0, 0, 0.24)",
        scaleMargins: { top: 0.14, bottom: 0.14 },
      },
      timeScale: {
        borderColor: "rgba(0, 0, 0, 0.32)",
        timeVisible: true,
        visible: false,
      },
      handleScroll: false,
      handleScale: false,
    });

    const rsiChart = createChart(rsiContainerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#000000",
      },
      grid: {
        vertLines: { color: "rgba(0, 0, 0, 0.08)" },
        horzLines: { color: "rgba(0, 0, 0, 0.12)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: "rgba(0, 0, 0, 0.24)",
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "rgba(0, 0, 0, 0.32)",
        timeVisible: true,
        visible: false,
      },
      handleScroll: false,
      handleScale: false,
    });

    const series = chart.addCandlestickSeries({
      upColor: POSITIVE_COLOR,
      downColor: NEGATIVE_COLOR,
      borderUpColor: POSITIVE_COLOR,
      borderDownColor: NEGATIVE_COLOR,
      wickUpColor: POSITIVE_COLOR,
      wickDownColor: NEGATIVE_COLOR,
    });
    const vwapSeries = chart.addLineSeries({
      color: NEUTRAL_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "VWAP",
    });
    const histogramSeries = macdChart.addHistogramSeries({
      priceLineVisible: false,
      lastValueVisible: false,
      base: 0,
    });
    const macdSeries = macdChart.addLineSeries({
      color: POSITIVE_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      title: "MACD 3-10",
    });
    const signalSeries = macdChart.addLineSeries({
      color: NEGATIVE_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      title: "Signal 16",
    });
    const rsiSeries = rsiChart.addLineSeries({
      color: NEUTRAL_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      title: "RSI 14",
    });
    const rsiUpperBand = rsiChart.addLineSeries({
      color: "rgba(185, 28, 28, 0.75)",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "70",
    });
    const rsiLowerBand = rsiChart.addLineSeries({
      color: "rgba(21, 128, 61, 0.75)",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      title: "30",
    });

    chartRef.current = chart;
    macdChartRef.current = macdChart;
    rsiChartRef.current = rsiChart;
    seriesRef.current = series;
    vwapSeriesRef.current = vwapSeries;
    histogramSeriesRef.current = histogramSeries;
    macdSeriesRef.current = macdSeries;
    signalSeriesRef.current = signalSeries;
    rsiSeriesRef.current = rsiSeries;
    rsiUpperBandRef.current = rsiUpperBand;
    rsiLowerBandRef.current = rsiLowerBand;

    const syncIndicatorRanges = (range: LogicalRange | null) => {
      if (!range || !indicatorRangeSyncReadyRef.current) {
        return;
      }
      macdChart.timeScale().setVisibleLogicalRange(range);
      rsiChart.timeScale().setVisibleLogicalRange(range);
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(syncIndicatorRanges);

    const resizeObserver = new ResizeObserver(() => {
      chart.timeScale().fitContent();
      syncIndicatorRanges(chart.timeScale().getVisibleLogicalRange());
    });
    resizeObserver.observe(priceContainerRef.current);
    resizeObserver.observe(macdContainerRef.current);
    resizeObserver.observe(rsiContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(syncIndicatorRanges);
      chart.remove();
      macdChart.remove();
      rsiChart.remove();
    };
  }, []);

  useEffect(() => {
    if (
      !seriesRef.current
      || !vwapSeriesRef.current
      || !histogramSeriesRef.current
      || !macdSeriesRef.current
      || !signalSeriesRef.current
      || !rsiSeriesRef.current
      || !rsiUpperBandRef.current
      || !rsiLowerBandRef.current
      || candles.length === 0
    ) {
      return;
    }

    const vwapPoints = buildIntradayVwap(candles);
    const macd = buildMomentumMacd(candles);
    const rsiPoints = buildRsi(candles, 14);
    const rsiUpperBand = buildLevelLine(candles, 70);
    const rsiLowerBand = buildLevelLine(candles, 30);
    const firstCandleTime = candles[0].time;
    const isSnapshot = firstCandleTime !== snapshotAnchorRef.current;
    indicatorRangeSyncReadyRef.current = true;

    if (isSnapshot) {
      snapshotAnchorRef.current = firstCandleTime;
      seriesRef.current.setData(
        candles.map((candle) => ({
          time: toTimestamp(candle.time),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        })),
      );
      seriesRef.current.setMarkers(
        events.map((event) => ({
          time: toTimestamp(event.time),
          position: "aboveBar",
          color: event.event_type === "earnings" ? NEGATIVE_COLOR : POSITIVE_COLOR,
          shape: event.event_type === "earnings" ? "square" : "circle",
          text: event.title,
        })),
      );
      vwapSeriesRef.current.setData(vwapPoints);
      histogramSeriesRef.current.setData(macd.histogram);
      macdSeriesRef.current.setData(macd.macdLine);
      signalSeriesRef.current.setData(macd.signalLine);
      rsiSeriesRef.current.setData(rsiPoints);
      rsiUpperBandRef.current.setData(rsiUpperBand);
      rsiLowerBandRef.current.setData(rsiLowerBand);
      chartRef.current?.timeScale().fitContent();
      const logicalRange = chartRef.current?.timeScale().getVisibleLogicalRange();
      if (logicalRange) {
        macdChartRef.current?.timeScale().setVisibleLogicalRange(logicalRange);
        rsiChartRef.current?.timeScale().setVisibleLogicalRange(logicalRange);
      } else {
        macdChartRef.current?.timeScale().fitContent();
        rsiChartRef.current?.timeScale().fitContent();
      }
    } else {
      // Live tick: update only the last bar — preserves the user's zoom/scroll.
      const last = candles[candles.length - 1];
      seriesRef.current.update({
        time: toTimestamp(last.time),
        open: last.open,
        high: last.high,
        low: last.low,
        close: last.close,
      });
      const lastVwapPoint = vwapPoints[vwapPoints.length - 1];
      if (lastVwapPoint) {
        vwapSeriesRef.current.update(lastVwapPoint);
      }
      const lastHistogramBar = macd.histogram[macd.histogram.length - 1];
      if (lastHistogramBar) {
        histogramSeriesRef.current.update(lastHistogramBar);
      }
      const lastMacdPoint = macd.macdLine[macd.macdLine.length - 1];
      if (lastMacdPoint) {
        macdSeriesRef.current.update(lastMacdPoint);
      }
      const lastSignalPoint = macd.signalLine[macd.signalLine.length - 1];
      if (lastSignalPoint) {
        signalSeriesRef.current.update(lastSignalPoint);
      }
      const lastRsiPoint = rsiPoints[rsiPoints.length - 1];
      if (lastRsiPoint) {
        rsiSeriesRef.current.update(lastRsiPoint);
      }
      const lastUpperBandPoint = rsiUpperBand[rsiUpperBand.length - 1];
      if (lastUpperBandPoint) {
        rsiUpperBandRef.current.update(lastUpperBandPoint);
      }
      const lastLowerBandPoint = rsiLowerBand[rsiLowerBand.length - 1];
      if (lastLowerBandPoint) {
        rsiLowerBandRef.current.update(lastLowerBandPoint);
      }
      const logicalRange = chartRef.current?.timeScale().getVisibleLogicalRange();
      if (logicalRange) {
        macdChartRef.current?.timeScale().setVisibleLogicalRange(logicalRange);
        rsiChartRef.current?.timeScale().setVisibleLogicalRange(logicalRange);
      }
    }
  }, [candles, events, timeframe]);

  return (
    <div className="chart-stack">
      <div className="chart-surface" ref={priceContainerRef} />
      <div className="indicator-surface-wrap">
        <div className="indicator-header">
          <strong>Momentum MACD</strong>
          <span>Linda Raschke 3-10-16</span>
        </div>
        <div className="indicator-surface" ref={macdContainerRef} />
      </div>
      <div className="indicator-surface-wrap">
        <div className="indicator-header">
          <strong>RSI</strong>
          <span>RSI 14 with 30/70 bands</span>
        </div>
        <div className="indicator-surface" ref={rsiContainerRef} />
      </div>
    </div>
  );
}
