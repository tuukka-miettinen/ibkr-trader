"""Tests for LLM response parsing and validation."""
import pytest

from app.api.optimize import ParameterKind, ParameterSpec
from app.llm.response_parser import parse_llm_response


def test_parse_valid_json_response() -> None:
    """Test parsing valid JSON response."""
    parameter_space = {
        "ema_period": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '{"ema_period": 25}'
    
    result = parse_llm_response(response, parameter_space)
    
    assert result["ema_period"] == 25


def test_parse_json_with_markdown_block() -> None:
    """Test parsing JSON wrapped in markdown code block."""
    parameter_space = {
        "threshold": ParameterSpec(
            kind=ParameterKind.FLOAT,
            default=0.5,
            minimum=0.0,
            maximum=1.0,
        ),
    }
    
    response = '```json\n{"threshold": 0.75}\n```'
    
    result = parse_llm_response(response, parameter_space)
    
    assert result["threshold"] == 0.75


def test_parse_rejects_missing_required_param() -> None:
    """Test parsing rejects response missing required parameter."""
    parameter_space = {
        "required_param": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '{"other_param": 10}'
    
    with pytest.raises(ValueError, match="Missing required parameter"):
        parse_llm_response(response, parameter_space)


def test_parse_rejects_out_of_bounds_integer() -> None:
    """Test parsing rejects integer out of bounds."""
    parameter_space = {
        "period": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '{"period": 100}'
    
    with pytest.raises(ValueError, match="above maximum"):
        parse_llm_response(response, parameter_space)


def test_parse_rejects_out_of_bounds_float() -> None:
    """Test parsing rejects float out of bounds."""
    parameter_space = {
        "threshold": ParameterSpec(
            kind=ParameterKind.FLOAT,
            default=0.5,
            minimum=0.0,
            maximum=1.0,
        ),
    }
    
    response = '{"threshold": 1.5}'
    
    with pytest.raises(ValueError, match="above maximum"):
        parse_llm_response(response, parameter_space)


def test_parse_coerces_integer_from_float() -> None:
    """Test parsing coerces float to integer when needed."""
    parameter_space = {
        "count": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=10,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '{"count": 25.7}'
    
    result = parse_llm_response(response, parameter_space)
    
    assert result["count"] == 25
    assert isinstance(result["count"], int)


def test_parse_boolean_from_string() -> None:
    """Test parsing boolean from string values."""
    parameter_space = {
        "enabled": ParameterSpec(
            kind=ParameterKind.BOOLEAN,
            default=False,
        ),
    }
    
    # Test "true" string
    result = parse_llm_response('{"enabled": "true"}', parameter_space)
    assert result["enabled"] is True
    
    # Test "false" string
    result = parse_llm_response('{"enabled": "false"}', parameter_space)
    assert result["enabled"] is False
    
    # Test native boolean
    result = parse_llm_response('{"enabled": true}', parameter_space)
    assert result["enabled"] is True


def test_parse_rejects_invalid_enum_choice() -> None:
    """Test parsing rejects enum value not in choices."""
    parameter_space = {
        "strategy": ParameterSpec(
            kind=ParameterKind.ENUM,
            default="buy_only",
            choices=["buy_only", "sell_only", "both"],
        ),
    }
    
    response = '{"strategy": "invalid_choice"}'
    
    with pytest.raises(ValueError, match="not in allowed choices"):
        parse_llm_response(response, parameter_space)


def test_parse_accepts_valid_enum_choice() -> None:
    """Test parsing accepts valid enum choice."""
    parameter_space = {
        "strategy": ParameterSpec(
            kind=ParameterKind.ENUM,
            default="buy_only",
            choices=["buy_only", "sell_only", "both"],
        ),
    }
    
    response = '{"strategy": "sell_only"}'
    
    result = parse_llm_response(response, parameter_space)
    
    assert result["strategy"] == "sell_only"


def test_parse_rejects_invalid_json() -> None:
    """Test parsing rejects malformed JSON."""
    parameter_space = {
        "param": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '{"param": 25, invalid}'  # Missing quote
    
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_llm_response(response, parameter_space)


def test_parse_rejects_non_object_json() -> None:
    """Test parsing rejects JSON that's not an object."""
    parameter_space = {
        "param": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '[1, 2, 3]'  # Array instead of object
    
    # The extractor looks for {...} specifically, so arrays will be rejected as "No JSON found"
    with pytest.raises(ValueError, match="No JSON found|must be a JSON object"):
        parse_llm_response(response, parameter_space)


def test_parse_ignores_extra_parameters() -> None:
    """Test parsing ignores extra parameters not in schema."""
    parameter_space = {
        "period": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    response = '{"period": 25, "extra_param": 999, "another": true}'
    
    result = parse_llm_response(response, parameter_space)
    
    assert result == {"period": 25}
    assert "extra_param" not in result


def test_parse_multiple_parameters() -> None:
    """Test parsing multiple parameters together."""
    parameter_space = {
        "int_param": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
        "bool_param": ParameterSpec(
            kind=ParameterKind.BOOLEAN,
            default=True,
        ),
        "enum_param": ParameterSpec(
            kind=ParameterKind.ENUM,
            default="option_a",
            choices=["option_a", "option_b"],
        ),
    }
    
    response = '{"int_param": 30, "bool_param": false, "enum_param": "option_b"}'
    
    result = parse_llm_response(response, parameter_space)
    
    assert result["int_param"] == 30
    assert result["bool_param"] is False
    assert result["enum_param"] == "option_b"
