"""Render parameter values into strategy scripts using safe templating."""
from __future__ import annotations

import re
from typing import Any

from app.strategy.sandbox import validate_user_script


def render_parameters_into_script(base_script: str, parameters: dict[str, Any]) -> str:
    """Substitute parameter values into the template using {{param_name}} delimiters.

    Args:
        base_script: The strategy script with {{param_name}} placeholders.
        parameters: A dict of parameter names to values.

    Returns:
        The script with all {{param_name}} placeholders replaced by their values.

    Raises:
        ValueError: If a placeholder in the script has no corresponding parameter value.
    """
    result = base_script
    placeholders = re.findall(r"\{\{(\w+)\}\}", base_script)

    for placeholder in placeholders:
        if placeholder not in parameters:
            raise ValueError(f"Parameter '{placeholder}' is required but not provided")

        value = parameters[placeholder]
        replacement = _format_value_for_python(value)
        result = result.replace(f"{{{{{placeholder}}}}}", str(replacement))

    return result


def _format_value_for_python(value: Any) -> str:
    """Format a Python value for insertion into script text.

    Args:
        value: The value to format (int, float, bool, str, etc.).

    Returns:
        A string representation safe for Python code.
        
    Note:
        String values are returned as-is (unquoted). It's the responsibility of the
        template to include quotes around {{string_param}} placeholders where needed.
    """
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        # Return unquoted; template is responsible for surrounding quotes if needed
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(f"Unsupported parameter type: {type(value)}")


def validate_rendered_script(script: str) -> None:
    """Validate a rendered script through the existing sandbox validator.

    Args:
        script: The script to validate.

    Raises:
        ValueError: If the script is invalid.
    """
    try:
        validate_user_script(script)
    except ValueError:
        raise
