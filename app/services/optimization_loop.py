"""Optimization loop that iterates: propose → evaluate → score → repeat."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.api.optimize import OptimizationRequest
from app.llm.provider import LLMProvider
from app.services.optimizer import evaluate_candidate


async def run_optimization_loop(
    plan: OptimizationRequest,
    llm_provider: LLMProvider,
    on_status_update: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the optimization loop: propose → evaluate → score → repeat.

    Args:
        plan: The OptimizationRequest with strategy script, symbols, parameter space, etc.
        llm_provider: The LLM provider to propose parameters (e.g., FakeLLMProvider or OpenAIProvider).
        on_status_update: Optional callback to receive status updates during loop.

    Returns:
        A dict with:
        - leaderboard: list of all candidates sorted by score (descending)
        - best_candidate: the top-scoring candidate
        - iterations_completed: number of iterations run
        - early_stop_reason: "plateau" if stopped early due to no improvement, None otherwise
    """
    leaderboard = []
    best_candidate = None
    best_score = -1.0
    iterations_with_no_improvement = 0
    iteration_history = []

    for iteration in range(plan.iteration_budget):
        try:
            # Get proposal from LLM
            proposal = await llm_provider.propose_parameters(
                parameter_space=plan.parameter_space,
                mode=plan.mode,
                best_so_far=best_candidate,
                iteration=iteration,
                iteration_history=iteration_history,
            )

            # Evaluate the proposal
            evaluation = evaluate_candidate(plan, proposal, candidate_name=f"candidate_{iteration}")

            # Score the candidate
            score = evaluation["score_details"]["overall_score"]

            # Add to leaderboard
            leaderboard.append(evaluation)
            leaderboard_sorted = sorted(leaderboard, key=lambda c: c["score_details"]["overall_score"], reverse=True)

            # Track if this improved the best
            if score > best_score:
                best_candidate = evaluation
                best_score = score
                iterations_with_no_improvement = 0
            else:
                iterations_with_no_improvement += 1

            # Save to history
            iteration_history.append(evaluation)

            # Send status update
            if on_status_update:
                on_status_update(
                    {
                        "status": "running",
                        "iteration": iteration + 1,
                        "budget": plan.iteration_budget,
                        "leaderboard": leaderboard_sorted,
                        "best_candidate": best_candidate,
                        "best_score": best_score,
                    }
                )

            # Check plateau detection: stop early if no improvement for 3 iterations
            if iterations_with_no_improvement >= 3:
                return {
                    "leaderboard": leaderboard_sorted,
                    "best_candidate": best_candidate,
                    "iterations_completed": iteration + 1,
                    "early_stop_reason": "plateau",
                }

        except Exception as exc:
            # Log error and continue to next iteration
            # In production, might want more sophisticated error handling
            print(f"Iteration {iteration} failed: {exc}")
            continue

    # Loop completed normally (budget exhausted)
    leaderboard_sorted = sorted(leaderboard, key=lambda c: c["score_details"]["overall_score"], reverse=True)
    return {
        "leaderboard": leaderboard_sorted,
        "best_candidate": best_candidate,
        "iterations_completed": plan.iteration_budget,
        "early_stop_reason": None,
    }
