"""Tests for parameter rendering functionality."""
from pytest import raises

from app.strategy.parameter_renderer import (
    render_parameters_into_script,
    validate_rendered_script,
)


def test_render_simple_parameters() -> None:
    script = "x = {{value1}}\ny = {{value2}}"
    params = {"value1": 10, "value2": 20}
    result = render_parameters_into_script(script, params)
    assert "x = 10" in result
    assert "y = 20" in result


def test_render_boolean_parameters() -> None:
    script = "use_filter = {{enable_filter}}"
    params = {"enable_filter": True}
    result = render_parameters_into_script(script, params)
    assert "use_filter = True" in result


def test_render_string_parameters() -> None:
    script = 'strategy_name = "{{name}}"'
    params = {"name": "pullback_reversal"}
    result = render_parameters_into_script(script, params)
    assert 'strategy_name = "pullback_reversal"' in result


def test_render_float_parameters() -> None:
    script = "threshold = {{alpha}}"
    params = {"alpha": 0.618}
    result = render_parameters_into_script(script, params)
    assert "threshold = 0.618" in result


def test_render_missing_parameter() -> None:
    script = "x = {{missing_param}}"
    params = {"other_param": 10}
    with raises(ValueError, match="missing_param"):
        render_parameters_into_script(script, params)


def test_render_strategy_script_with_parameters() -> None:
    base_script = """\
def signals(candles):
    ema_period = {{ema_fast}}
    rsi_period = {{rsi_length}}
    use_vwap = {{use_vwap_filter}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
        })
    return results
"""
    params = {
        "ema_fast": 20,
        "rsi_length": 14,
        "use_vwap_filter": True,
    }
    result = render_parameters_into_script(base_script, params)
    assert "ema_period = 20" in result
    assert "rsi_period = 14" in result
    assert "use_vwap = True" in result


def test_validate_rendered_script() -> None:
    script = """\
def signals(candles):
    results = []
    for bar in candles:
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
"""
    # Should not raise
    validate_rendered_script(script)


def test_validate_rendered_script_invalid() -> None:
    script = "def signals(:\n    pass\n"
    with raises(ValueError, match="Syntax error"):
        validate_rendered_script(script)
