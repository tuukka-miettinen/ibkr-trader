import { useEffect, useRef, useState } from "react";
import {
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
} from "lightweight-charts";

import type {
  OptimizationCandidate,
  OptimizationJob,
  OptimizationJobStatus,
  OptimizationMode,
  OptimizationRequest,
  ParameterKind,
  ParameterSpec,
  Timeframe,
} from "../lib/types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TIMEFRAMES: Timeframe[] = ["1m", "3m", "5m", "15m", "1h"];
const DEFAULT_SYMBOLS = ["NBIS", "MSFT"];
const DEFAULT_TIMEFRAMES: Timeframe[] = ["5m", "15m"];

interface ParameterInput {
  name: string;
  kind: ParameterKind;
  default: number | boolean | string;
  minimum?: number;
  maximum?: number;
  choices?: string;
}

export default function OptimizerView() {
  // Form state
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS.join(","));
  const [selectedTimeframes, setSelectedTimeframes] = useState<Set<Timeframe>>(
    new Set(DEFAULT_TIMEFRAMES)
  );
  const [limit, setLimit] = useState(1638);
  const [iterationBudget, setIterationBudget] = useState(5);
  const [provider, setProvider] = useState<"fake" | "openai">("fake");
  const [parameters, setParameters] = useState<ParameterInput[]>([
    { name: "ema_period", kind: "integer", default: 20, minimum: 5, maximum: 50 },
  ]);
  const [script, setScript] = useState(`# Define your trading strategy
# Use placeholders like [ema_period], [rsi34] that match your parameters below
def signals(candles):
    # Use ta.ema, ta.rsi, ta.sma, ta.atr, ta.macd, etc.
    # Return a list of {"time": iso_string, "signal": "buy"|"sell"|None}
    return []
`);

  // Job state
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<OptimizationJobStatus | null>(null);
  const [job, setJob] = useState<OptimizationJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Chart state
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  // Polling
  const pollIntervalRef = useRef<number | null>(null);

  // Chart initialization
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: "#131722" } },
      width: chartContainerRef.current.clientWidth,
      height: 300,
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    const lineSeries = chart.addLineSeries({
      color: "#2962FF",
      lineWidth: 2,
      title: "Best Score",
    });

    chartRef.current = chart;
    lineSeriesRef.current = lineSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  // Update chart with leaderboard data
  useEffect(() => {
    if (!lineSeriesRef.current || !job?.leaderboard || job.leaderboard.length === 0) {
      return;
    }

    // Find best score in each iteration (cumulative max)
    const data = [];
    let bestScore = 0;
    for (let i = 0; i < job.leaderboard.length; i++) {
      const candidate = job.leaderboard[i];
      const score = candidate.score_details.overall_score;
      bestScore = Math.max(bestScore, score);
      data.push({
        time: (i + 1) as any,
        value: bestScore,
      });
    }

    lineSeriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [job?.leaderboard]);

  // Polling effect
  useEffect(() => {
    if (!currentJobId || !["queued", "running"].includes(jobStatus || "")) {
      return;
    }

    const pollJob = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/optimize/${currentJobId}`);
        if (!res.ok) return;

        const data = await res.json();
        setJob(data);
        setJobStatus(data.status);

        if (["completed", "failed"].includes(data.status)) {
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
      } catch {
        // Silent fail
      }
    };

    // Poll immediately and then every 2 seconds
    pollJob();
    pollIntervalRef.current = window.setInterval(pollJob, 2000);

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [currentJobId, jobStatus]);

  const addParameter = () => {
    setParameters([
      ...parameters,
      { name: "", kind: "integer", default: 0, minimum: 0, maximum: 100 },
    ]);
  };

  const removeParameter = (index: number) => {
    setParameters(parameters.filter((_, i) => i !== index));
  };

  const updateParameter = (index: number, updates: Partial<ParameterInput>) => {
    const updated = [...parameters];
    updated[index] = { ...updated[index], ...updates };
    setParameters(updated);
  };

  const toggleTimeframe = (tf: Timeframe) => {
    const newSet = new Set(selectedTimeframes);
    if (newSet.has(tf)) {
      newSet.delete(tf);
    } else {
      newSet.add(tf);
    }
    setSelectedTimeframes(newSet);
  };

  const buildParameterSpace = (): Record<string, ParameterSpec> => {
    const space: Record<string, ParameterSpec> = {};
    for (const param of parameters) {
      if (!param.name) continue;

      const spec: ParameterSpec = {
        kind: param.kind,
        default: param.default,
      };

      if (param.kind === "integer" || param.kind === "float") {
        spec.minimum = param.minimum;
        spec.maximum = param.maximum;
      } else if (param.kind === "enum") {
        spec.choices = (param.choices || "").split(",").map(s => s.trim()).filter(s => s);
      }

      space[param.name] = spec;
    }
    return space;
  };

  const launchOptimization = async () => {
    setLoading(true);
    setError(null);

    try {
      const symbolList = symbols.split(",").map(s => s.trim()).filter(s => s);
      const timeframeList = Array.from(selectedTimeframes);

      if (!symbolList.length) {
        throw new Error("At least one symbol is required");
      }
      if (!timeframeList.length) {
        throw new Error("At least one timeframe is required");
      }

      const parameterSpace = buildParameterSpace();
      if (!Object.keys(parameterSpace).length) {
        throw new Error("At least one parameter is required");
      }

      const plan: OptimizationRequest = {
        script: script,
        symbols: symbolList,
        timeframes: timeframeList as Timeframe[],
        limit,
        mode: "global" as OptimizationMode,
        parameter_space: parameterSpace,
        iteration_budget: iterationBudget,
        train_ratio: 0.67,
      };

      const res = await fetch(`${API_BASE}/api/optimize/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, provider }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      setCurrentJobId(data.job_id);
      setJobStatus("queued");
      setJob(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const loadBestCandidate = () => {
    if (!job?.best_candidate) return;
    
    const candidate = job.best_candidate;
    // In a real app, this would update the script editor in BacktestView
    // For now, we'll just show an alert
    alert(`Best candidate: ${candidate.candidate_name}\nScore: ${candidate.score_details.overall_score.toFixed(4)}`);
  };

  const statusColor = {
    queued: "#94a3b8",
    running: "#3b82f6",
    completed: "#10b981",
    failed: "#ef4444",
  };

  return (
    <div className="optimizer-shell">
      <div className="optimizer-form-section">
        <h3>Optimization Parameters</h3>

        <div className="form-group">
          <label>
            Strategy Script (use placeholders like &#123;&#123;ema_period&#125;&#125;):
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              disabled={loading}
              rows={8}
              placeholder="def signals(candles):\n    return []"
              style={{ fontFamily: "monospace", fontSize: "12px" }}
            />
          </label>
        </div>

        <div className="form-group">
          <label>
            Symbols (comma-separated):
            <input
              type="text"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
              disabled={loading}
              placeholder="NBIS,MSFT,GOOGL"
            />
          </label>
        </div>

        <div className="form-group">
          <label>Timeframes:</label>
          <div className="timeframe-select">
            {TIMEFRAMES.map((tf) => (
              <label key={tf}>
                <input
                  type="checkbox"
                  checked={selectedTimeframes.has(tf)}
                  onChange={() => toggleTimeframe(tf)}
                  disabled={loading}
                />
                {tf}
              </label>
            ))}
          </div>
        </div>

        <div className="form-group">
          <label>
            Bars (limit):
            <input
              type="number"
              min={50}
              max={10000}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              disabled={loading}
            />
          </label>
        </div>

        <div className="form-group">
          <label>
            Iteration Budget: {iterationBudget}
            <input
              type="range"
              min={5}
              max={50}
              value={iterationBudget}
              onChange={(e) => setIterationBudget(Number(e.target.value))}
              disabled={loading}
            />
          </label>
        </div>

        <div className="form-group">
          <label>
            Provider:
            <select value={provider} onChange={(e) => setProvider(e.target.value as any)} disabled={loading}>
              <option value="fake">Fake (deterministic, for testing)</option>
              <option value="openai">OpenAI (gpt-4o-mini)</option>
            </select>
          </label>
        </div>

        <div className="optimizer-params-section">
          <h4>Parameter Space</h4>
          {parameters.map((param, idx) => (
            <div key={idx} className="param-input">
              <input
                type="text"
                placeholder="Parameter name"
                value={param.name}
                onChange={(e) => updateParameter(idx, { name: e.target.value })}
                disabled={loading}
              />
              <select
                value={param.kind}
                onChange={(e) => updateParameter(idx, { kind: e.target.value as ParameterKind })}
                disabled={loading}
              >
                <option value="integer">Integer</option>
                <option value="float">Float</option>
                <option value="boolean">Boolean</option>
                <option value="enum">Enum</option>
              </select>

              {(param.kind === "integer" || param.kind === "float") && (
                <>
                  <input
                    type="number"
                    placeholder="Min"
                    value={param.minimum ?? ""}
                    onChange={(e) => updateParameter(idx, { minimum: e.target.value ? Number(e.target.value) : undefined })}
                    disabled={loading}
                  />
                  <input
                    type="number"
                    placeholder="Max"
                    value={param.maximum ?? ""}
                    onChange={(e) => updateParameter(idx, { maximum: e.target.value ? Number(e.target.value) : undefined })}
                    disabled={loading}
                  />
                  <input
                    type="number"
                    placeholder="Default"
                    value={String(param.default)}
                    onChange={(e) => updateParameter(idx, { default: Number(e.target.value) })}
                    disabled={loading}
                  />
                </>
              )}

              {param.kind === "enum" && (
                <input
                  type="text"
                  placeholder="Choices (comma-separated)"
                  value={param.choices ?? ""}
                  onChange={(e) => updateParameter(idx, { choices: e.target.value })}
                  disabled={loading}
                />
              )}

              <button
                type="button"
                onClick={() => removeParameter(idx)}
                disabled={loading}
                className="btn-remove"
              >
                Remove
              </button>
            </div>
          ))}

          <button type="button" onClick={addParameter} disabled={loading} className="btn-add-param">
            + Add Parameter
          </button>
        </div>

        <div className="info-box">
          <strong>ℹ️ Optimization Methodology</strong>
          <ul>
            <li>Uses 67% train / 33% holdout walk-forward split</li>
            <li>Past month of candles (5m/15m only)</li>
            <li>Not live trading signals — research only</li>
            <li>Early stop: if no improvement for 3 iterations</li>
          </ul>
        </div>

        {error && <div className="error-box">{error}</div>}

        <button
          type="button"
          onClick={launchOptimization}
          disabled={loading || currentJobId !== null}
          className="btn-launch"
        >
          {loading ? "Launching..." : "🚀 Launch Optimization"}
        </button>
      </div>

      {currentJobId && (
        <div className="optimizer-results-section">
          <div className="job-status">
            <span>Job ID: {currentJobId}</span>
            <span
              className="status-badge"
              style={{ backgroundColor: statusColor[jobStatus || "queued"] }}
            >
              {jobStatus}
            </span>
          </div>

          {jobStatus === "running" && (
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: "100%" }}>
                Polling for updates...
              </div>
            </div>
          )}

          {job?.leaderboard && job.leaderboard.length > 0 && (
            <>
              <div className="optimizer-chart-container">
                <div ref={chartContainerRef} className="optimizer-chart" />
              </div>

              <div className="leaderboard-container">
                <h4>Leaderboard</h4>
                <table className="leaderboard-table">
                  <thead>
                    <tr>
                      <th>Rank</th>
                      <th>Candidate</th>
                      <th>Score</th>
                      <th>PnL</th>
                      <th>Win Rate</th>
                      <th>Trades</th>
                      <th>Consistency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {job.leaderboard.map((candidate, idx) => {
                      const isBest = job.best_candidate?.candidate_name === candidate.candidate_name;
                      return (
                        <tr key={idx} className={isBest ? "best-candidate" : ""}>
                          <td>{idx + 1}</td>
                          <td>{candidate.candidate_name}</td>
                          <td>
                            <strong>{candidate.score_details.overall_score.toFixed(4)}</strong>
                          </td>
                          <td style={{ color: candidate.score_details.holdout_pnl >= 0 ? "#10b981" : "#ef4444" }}>
                            {candidate.score_details.holdout_pnl.toFixed(2)}
                          </td>
                          <td>{candidate.score_details.holdout_win_rate.toFixed(1)}%</td>
                          <td>{candidate.score_details.holdout_trades}</td>
                          <td>{candidate.score_details.consistency_bonus.toFixed(4)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {job?.best_candidate && jobStatus === "completed" && (
            <button type="button" onClick={loadBestCandidate} className="btn-load-best">
              ✓ Load Best Result
            </button>
          )}

          {job?.error_message && (
            <div className="error-box">
              <strong>Error:</strong> {job.error_message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
