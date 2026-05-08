export type Timeframe = "1m" | "5m" | "15m" | "1h";

export type Candle = {
  symbol: string;
  timeframe: Timeframe;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type TimelineEvent = {
  id: string;
  symbol: string;
  event_type: "earnings" | "dividend" | "split";
  time: string;
  title: string;
  summary: string;
  details: Record<string, string | number | null>;
};

export type SnapshotMessage = {
  type: "snapshot";
  symbol: string;
  timeframe: Timeframe;
  candles: Candle[];
  events: TimelineEvent[];
};

export type CandleUpdateMessage = {
  type: "candle_update";
  symbol: string;
  timeframe: Timeframe;
  candle: Candle;
};

export type StatusMessage = {
  type: "status";
  status: string;
  message: string;
  symbol?: string;
  timeframe?: Timeframe;
};

export type ErrorMessage = {
  type: "error";
  message: string;
};

export type SocketMessage = SnapshotMessage | CandleUpdateMessage | StatusMessage | ErrorMessage;
