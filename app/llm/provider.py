"""LLM provider abstraction for parameter optimization."""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from openai import OpenAI

from app.api.optimize import ParameterSpec, OptimizationMode


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def propose_parameters(
        self,
        parameter_space: dict[str, ParameterSpec],
        mode: OptimizationMode,
        best_so_far: dict[str, Any] | None,
        iteration: int,
        iteration_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Propose the next set of parameters to evaluate.

        Args:
            parameter_space: The parameter definitions from OptimizationRequest.
            mode: The optimization mode (GLOBAL or SECTOR).
            best_so_far: The best candidate so far (with score_details and metrics).
            iteration: Current iteration number (0-indexed).
            iteration_history: List of previous iterations with results.

        Returns:
            A dict of parameter names to values to evaluate next.

        Raises:
            ValueError: If the proposed parameters are invalid.
        """


class FakeLLMProvider(LLMProvider):
    """Deterministic fake LLM provider for testing.

    Increments integer parameters by 1, flips booleans, cycles enums.
    Ensures tests are deterministic without requiring API keys or making real calls.
    """

    async def propose_parameters(
        self,
        parameter_space: dict[str, ParameterSpec],
        mode: OptimizationMode,
        best_so_far: dict[str, Any] | None,
        iteration: int,
        iteration_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate deterministic parameter proposals."""
        proposal = {}

        for param_name, spec in parameter_space.items():
            if spec.kind == "integer":
                # Increment from default or best_so_far value
                current = best_so_far.get(param_name, spec.default) if best_so_far else spec.default
                next_val = current + 1
                # Clamp to bounds
                if spec.minimum is not None:
                    next_val = max(next_val, spec.minimum)
                if spec.maximum is not None:
                    next_val = min(next_val, spec.maximum)
                proposal[param_name] = int(next_val)

            elif spec.kind == "float":
                current = best_so_far.get(param_name, spec.default) if best_so_far else spec.default
                step = spec.step or 0.1
                next_val = current + step
                if spec.minimum is not None:
                    next_val = max(next_val, spec.minimum)
                if spec.maximum is not None:
                    next_val = min(next_val, spec.maximum)
                proposal[param_name] = round(next_val, 4)

            elif spec.kind == "boolean":
                # Flip the current boolean
                current = best_so_far.get(param_name, spec.default) if best_so_far else spec.default
                proposal[param_name] = not current

            elif spec.kind == "enum":
                # Cycle to next choice
                current = best_so_far.get(param_name, spec.default) if best_so_far else spec.default
                idx = spec.choices.index(current) if current in spec.choices else 0
                proposal[param_name] = spec.choices[(idx + 1) % len(spec.choices)]

        return proposal


class OpenAIProvider(LLMProvider):
    """OpenAI provider that calls the OpenAI API.

    Uses gpt-4o-mini by default; falls back to gpt-3.5-turbo if gpt-4o-mini unavailable.
    Requires OPENAI_API_KEY environment variable.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        """Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key. If None, uses OPENAI_API_KEY env var.
            model: Model name (e.g., "gpt-4o-mini", "gpt-3.5-turbo"). Default: "gpt-4o-mini".
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not provided. Set OPENAI_API_KEY environment variable or pass api_key argument."
            )
        self.client = OpenAI(api_key=self.api_key)
        self.model = model or "gpt-4o-mini"

    async def propose_parameters(
        self,
        parameter_space: dict[str, ParameterSpec],
        mode: OptimizationMode,
        best_so_far: dict[str, Any] | None,
        iteration: int,
        iteration_history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Query OpenAI to propose the next parameters."""
        # Import here to avoid circular dependency
        from app.llm.prompt_builder import build_optimization_prompt
        from app.llm.response_parser import parse_llm_response

        prompt = build_optimization_prompt(
            parameter_space=parameter_space,
            mode=mode,
            iteration=iteration,
            best_so_far=best_so_far,
            iteration_history=iteration_history,
        )

        # Call OpenAI API
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert trading strategy parameter optimizer. You propose parameter values only. Your response must be ONLY valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=500,
        )

        raw_response = response.choices[0].message.content or ""

        # Parse and validate response
        proposal = parse_llm_response(raw_response, parameter_space)
        return proposal
