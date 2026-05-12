"""Build constrained LLM prompts for parameter optimization."""
from __future__ import annotations

import json
from typing import Any

from app.api.optimize import ParameterKind, ParameterSpec, OptimizationMode


def build_optimization_prompt(
    parameter_space: dict[str, ParameterSpec],
    mode: OptimizationMode,
    iteration: int,
    best_so_far: dict[str, Any] | None,
    iteration_history: list[dict[str, Any]],
) -> str:
    """Build a constrained LLM prompt for parameter optimization.

    The prompt describes:
    - Parameter bounds and types
    - Current best candidate and its score
    - Recent iteration history
    - Explicit constraint: response must be ONLY JSON

    Args:
        parameter_space: Dict of parameter names to ParameterSpec objects.
        mode: GLOBAL or SECTOR optimization mode.
        iteration: Current iteration number (0-indexed).
        best_so_far: Best candidate so far with metrics (or None if first iteration).
        iteration_history: List of previous iteration results.

    Returns:
        A prompt string for the LLM to respond to.
    """
    lines = []

    lines.append("# Parameter Optimization Task")
    lines.append("")
    lines.append(f"Iteration: {iteration + 1}")
    lines.append(f"Mode: {mode.value}")
    lines.append("")

    # Describe parameter space
    lines.append("## Parameter Space")
    lines.append("")
    for param_name, spec in parameter_space.items():
        desc = _describe_parameter(param_name, spec)
        lines.append(desc)
    lines.append("")

    # Show current best
    if best_so_far:
        lines.append("## Current Best Candidate")
        lines.append("")
        lines.append(f"Parameters: {json.dumps(best_so_far.get('parameters', {}), indent=2)}")
        lines.append("")
        if "score_details" in best_so_far:
            score_details = best_so_far["score_details"]
            lines.append(f"Overall Score: {score_details.get('overall_score', 'N/A')}")
            pnl = score_details.get('holdout_pnl', 'N/A')
            lines.append(f"Holdout PnL: {pnl}")
            win_rate = score_details.get('holdout_win_rate', 'N/A')
            if isinstance(win_rate, (int, float)):
                lines.append(f"Holdout Win Rate: {win_rate:.1f}%")
            else:
                lines.append(f"Holdout Win Rate: {win_rate}%")
            lines.append(f"Holdout Trades: {score_details.get('holdout_trades', 'N/A')}")
        lines.append("")

    # Show recent history
    if iteration_history:
        lines.append("## Recent Iteration History")
        lines.append("")
        for i, result in enumerate(iteration_history[-3:]):  # Show last 3 iterations
            iter_num = max(0, iteration - len(iteration_history) + 1 + i)
            params = result.get("parameters", {})
            score = result.get("score_details", {}).get("overall_score", "N/A")
            lines.append(f"Iteration {iter_num}: Score {score:.4f} → {json.dumps(params)}")
        lines.append("")

    # Final instruction with JSON constraint
    lines.append("## Task")
    lines.append("")
    lines.append(
        "You are a trading strategy parameter optimizer. Your task is to propose new parameter values "
        "to improve the strategy. Consider the parameter bounds, current best performance, and recent history."
    )
    lines.append("")
    lines.append(
        "**IMPORTANT: You must respond ONLY with valid JSON containing the proposed parameters. "
        "No other text, explanation, or markdown. Just the JSON object.**"
    )
    lines.append("")
    lines.append("Example valid response format:")
    lines.append("{")
    first = True
    for param_name, spec in parameter_space.items():
        if not first:
            lines[-1] += ","
        param_example = _example_value(param_name, spec)
        lines.append(f'    "{param_name}": {param_example}')
        first = False
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def _describe_parameter(param_name: str, spec: ParameterSpec) -> str:
    """Describe a single parameter for the LLM."""
    desc_parts = [f"- **{param_name}** ({spec.kind.value})"]

    if spec.kind == ParameterKind.INTEGER:
        if spec.minimum is not None and spec.maximum is not None:
            desc_parts.append(f"Range: {int(spec.minimum)}–{int(spec.maximum)}")
        if spec.step:
            desc_parts.append(f"Step: {spec.step}")

    elif spec.kind == ParameterKind.FLOAT:
        if spec.minimum is not None and spec.maximum is not None:
            desc_parts.append(f"Range: {spec.minimum:.2f}–{spec.maximum:.2f}")
        if spec.step:
            desc_parts.append(f"Step: {spec.step}")

    elif spec.kind == ParameterKind.ENUM:
        desc_parts.append(f"Choices: {', '.join(spec.choices)}")

    desc_parts.append(f"Default: {_example_value(param_name, spec)}")
    if spec.description:
        desc_parts.append(f"Description: {spec.description}")

    return " | ".join(desc_parts)


def _example_value(param_name: str, spec: ParameterSpec) -> str:
    """Return an example/default value for a parameter in JSON-safe format."""
    if spec.kind == ParameterKind.BOOLEAN:
        return "true" if spec.default else "false"
    elif spec.kind == ParameterKind.ENUM:
        return f'"{spec.default}"'
    elif isinstance(spec.default, str):
        return f'"{spec.default}"'
    else:
        return str(spec.default)
