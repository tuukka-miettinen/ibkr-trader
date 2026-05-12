"""Parse and validate LLM responses for parameter optimization."""
from __future__ import annotations

import json
import re
from typing import Any

from app.api.optimize import ParameterKind, ParameterSpec


def parse_llm_response(raw_response: str, parameter_space: dict[str, ParameterSpec]) -> dict[str, Any]:
    """Parse and validate LLM response to extract parameters.

    Extracts JSON from the response (handling markdown code blocks), validates each
    parameter against its ParameterSpec, and returns a clean dict.

    Args:
        raw_response: The raw text response from the LLM.
        parameter_space: Dict of parameter names to ParameterSpec objects.

    Returns:
        A validated dict of parameter names to values.

    Raises:
        ValueError: If the response is invalid JSON, missing required params, or violates bounds.
    """
    # Extract JSON from response, handling markdown blocks
    json_str = _extract_json(raw_response)

    try:
        proposal = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM response is not valid JSON: {json_str[:100]}...") from exc

    if not isinstance(proposal, dict):
        raise ValueError(f"LLM response must be a JSON object, got {type(proposal).__name__}")

    # Validate each parameter
    validated = {}
    for param_name, spec in parameter_space.items():
        if param_name not in proposal:
            raise ValueError(f"Missing required parameter: {param_name}")

        value = proposal[param_name]
        validated[param_name] = _validate_parameter(param_name, value, spec)

    # Warn if extra parameters provided (but don't reject)
    extra_params = set(proposal.keys()) - set(parameter_space.keys())
    if extra_params:
        # Could log a warning here; for now just ignore them
        pass

    return validated


def _extract_json(text: str) -> str:
    """Extract JSON from text, handling markdown code blocks.

    Args:
        text: The text potentially containing JSON.

    Returns:
        The extracted JSON string.

    Raises:
        ValueError: If no JSON found.
    """
    text = text.strip()

    # Try to find markdown code block first
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()

    # Try to find a raw JSON object {...}
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0).strip()

    raise ValueError(f"No JSON found in LLM response: {text[:100]}...")


def _validate_parameter(param_name: str, value: Any, spec: ParameterSpec) -> Any:
    """Validate a parameter value against its spec.

    Args:
        param_name: The parameter name (for error messages).
        value: The proposed value.
        spec: The ParameterSpec defining constraints.

    Returns:
        The validated value (may be type-coerced).

    Raises:
        ValueError: If validation fails.
    """
    if spec.kind == ParameterKind.INTEGER:
        try:
            int_val = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{param_name}: expected integer, got {type(value).__name__}: {value}") from exc

        if spec.minimum is not None and int_val < spec.minimum:
            raise ValueError(f"{param_name}: value {int_val} is below minimum {spec.minimum}")
        if spec.maximum is not None and int_val > spec.maximum:
            raise ValueError(f"{param_name}: value {int_val} is above maximum {spec.maximum}")

        return int_val

    elif spec.kind == ParameterKind.FLOAT:
        try:
            float_val = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{param_name}: expected float, got {type(value).__name__}: {value}") from exc

        if spec.minimum is not None and float_val < spec.minimum:
            raise ValueError(f"{param_name}: value {float_val} is below minimum {spec.minimum}")
        if spec.maximum is not None and float_val > spec.maximum:
            raise ValueError(f"{param_name}: value {float_val} is above maximum {spec.maximum}")

        return round(float_val, 6)

    elif spec.kind == ParameterKind.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in {"true", "yes", "1"}:
                return True
            if value.lower() in {"false", "no", "0"}:
                return False
        raise ValueError(f"{param_name}: expected boolean, got {type(value).__name__}: {value}")

    elif spec.kind == ParameterKind.ENUM:
        str_val = str(value).strip()
        if str_val not in spec.choices:
            raise ValueError(
                f"{param_name}: value '{str_val}' not in allowed choices: {spec.choices}"
            )
        return str_val

    raise ValueError(f"Unknown parameter kind: {spec.kind}")
