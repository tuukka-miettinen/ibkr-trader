export type Timeframe = "5s" | "1m" | "3m" | "5m" | "15m" | "1h";

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

export type Algorithm = {
  id: string;
  name: string;
  version: number;
  description: string | null;
  script?: string;
  is_favorite?: boolean;
  created_at: string | null;
};

export type BacktestRunSummary = {
  id: string;
  algorithm_id: string;
  algorithm_name: string;
  algorithm_version: number;
  symbol: string;
  mode: string;
  lookback_days: number | null;
  num_trades: number;
  total_pnl: number;
  total_pnl_pct: number;
  win_rate: number;
  final_balance: number;
  created_at: string | null;
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

// ============================================================================
// Optimizer Types
// ============================================================================

export type OptimizationMode = "global" | "sector";

export type ParameterKind = "integer" | "float" | "boolean" | "enum";

export type ParameterSpec = {
  kind: ParameterKind;
  default: number | boolean | string;
  minimum?: number;
  maximum?: number;
  step?: number;
  choices?: string[];
  description?: string;
  allow_sector_override?: boolean;
};

export type OptimizationRequest = {
  script: string;
  symbols: string[];
  timeframes: Timeframe[];
  limit: number;
  mode: OptimizationMode;
  parameter_space: Record<string, ParameterSpec>;
  iteration_budget: number;
  train_ratio: number;
  sector_map?: Record<string, string>;
};

export type CandidateScore = {
  candidate_name: string;
  overall_score: number;
  pnl_component: number;
  win_rate_component: number;
  trade_count_component: number;
  consistency_bonus: number;
  holdout_pnl: number;
  holdout_win_rate: number;
  holdout_trades: number;
  train_pnl: number;
  train_trades: number;
  justification: string;
};

export type OptimizationCandidate = {
  candidate_name: string;
  parameters: Record<string, number | boolean | string>;
  rendered_script: string;
  score_details: CandidateScore;
};

export type OptimizationJobStatus = "queued" | "running" | "completed" | "failed";

export type OptimizationJob = {
  job_id: string;
  status: OptimizationJobStatus;
  plan: OptimizationRequest;
  leaderboard: OptimizationCandidate[];
  best_candidate: OptimizationCandidate | null;
  iterations_completed: number;
  early_stop_reason: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type OptimizationJobListItem = {
  job_id: string;
  status: OptimizationJobStatus;
  provider: string;
  created_at: string;
  completed_at: string | null;
  best_score: number | null;
  best_candidate_name: string | null;
};

export type SocketMessage = SnapshotMessage | CandleUpdateMessage | StatusMessage | ErrorMessage;

// ============================================================================
// Live Paper-Trading Types
// ============================================================================

export type LiveSessionStatus = "created" | "running" | "stopped" | "error";

export type LiveSession = {
  id: string;
  name: string;
  status: LiveSessionStatus;
  order_type: "market" | "limit";
  position_size: number;
  max_entries: number;
  max_daily_loss: number;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  stopped_at: string | null;
  is_running: boolean;
};

export type LiveSessionSymbol = {
  id: string;
  symbol: string;
  algorithm_id: string;
  allocated_capital: number;
  position_size: number;
  max_entries: number;
  current_shares: number;
  current_cost: number;
  cash_remaining: number;
  realized_pnl: number;
  unrealized_pnl: number;
  daily_realized_pnl: number;
  last_price: number | null;
  portfolio_value?: number;
  avg_price?: number;
  tick_count?: number;
  last_tick_time?: string | null;
  position_entries?: { time: string; price: number; shares: number; cost: number }[];
};

export type LiveTrade = {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  shares: number;
  price: number;
  cost: number;
  pnl: number | null;
  pnl_pct: number | null;
  ibkr_order_id: number | null;
  status: "pending" | "filled" | "cancelled" | "error";
  created_at: string | null;
};

export type LiveWsEvent =
  | { type: "snapshot"; session_id: string; symbols: Record<string, LiveSessionSymbol>; total_pnl: number; total_value: number }
  | { type: "tick"; symbol: string; time: string; price: number; volume: number; position_shares: number; unrealized_pnl: number; realized_pnl: number; cash: number; portfolio_value: number; tick_count: number }
  | { type: "candle"; symbol: string; candle: { time: string; open: number; high: number; low: number; close: number; volume: number } }
  | { type: "trade"; symbol: string; side: "buy" | "sell"; shares: number; price: number; cost?: number; proceeds?: number; pnl?: number; pnl_pct?: number; time: string; cash_remaining: number }
  | { type: "status"; status: string; message: string; symbols?: string[] }
  | { type: "error"; message: string; symbol?: string }
  | { type: "heartbeat" };
