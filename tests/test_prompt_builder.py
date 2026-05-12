"""Tests for prompt building."""
import json

from app.api.optimize import OptimizationMode, ParameterKind, ParameterSpec
from app.llm.prompt_builder import build_optimization_prompt


def test_prompt_builder_describes_parameters() -> None:
    """Test that prompt describes parameter bounds."""
    parameter_space = {
        "ema_period": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
            description="EMA period in bars",
        ),
    }
    
    prompt = build_optimization_prompt(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        iteration=0,
        best_so_far=None,
        iteration_history=[],
    )
    
    assert "ema_period" in prompt
    assert "integer" in prompt
    assert "5" in prompt
    assert "50" in prompt
    assert "EMA period in bars" in prompt


def test_prompt_builder_includes_json_constraint() -> None:
    """Test that prompt includes JSON-only constraint."""
    parameter_space = {
        "param": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    prompt = build_optimization_prompt(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        iteration=0,
        best_so_far=None,
        iteration_history=[],
    )
    
    assert "JSON" in prompt
    assert "must respond ONLY with" in prompt.upper() or "json" in prompt.lower()


def test_prompt_builder_shows_best_so_far() -> None:
    """Test that prompt shows current best candidate."""
    parameter_space = {
        "threshold": ParameterSpec(
            kind=ParameterKind.FLOAT,
            default=0.5,
            minimum=0.0,
            maximum=1.0,
        ),
    }
    
    best = {
        "parameters": {"threshold": 0.65},
        "score_details": {
            "overall_score": 0.75,
            "holdout_pnl": 150.0,
            "holdout_win_rate": 55.5,
            "holdout_trades": 25,
        },
    }
    
    prompt = build_optimization_prompt(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        iteration=1,
        best_so_far=best,
        iteration_history=[],
    )
    
    assert "0.75" in prompt or "0.75" in str(best["score_details"]["overall_score"])
    assert "0.65" in prompt or "0.65" in str(best["parameters"]["threshold"])


def test_prompt_builder_includes_iteration_history() -> None:
    """Test that prompt includes recent iteration results."""
    parameter_space = {
        "period": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    history = [
        {
            "parameters": {"period": 21},
            "score_details": {"overall_score": 0.65},
        },
        {
            "parameters": {"period": 22},
            "score_details": {"overall_score": 0.67},
        },
    ]
    
    prompt = build_optimization_prompt(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        iteration=2,
        best_so_far=history[1],
        iteration_history=history,
    )
    
    # Should show recent iteration info
    assert "Iteration" in prompt or "iteration" in prompt.lower()


def test_prompt_builder_example_json_is_valid() -> None:
    """Test that example JSON in prompt is valid."""
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
    
    prompt = build_optimization_prompt(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        iteration=0,
        best_so_far=None,
        iteration_history=[],
    )
    
    # Extract JSON example from prompt
    import re
    json_match = re.search(r"\{[\s\S]*\}", prompt)
    assert json_match, "No JSON object found in prompt"
    
    json_str = json_match.group(0)
    try:
        parsed = json.loads(json_str)
        assert "int_param" in parsed
        assert "bool_param" in parsed
        assert "enum_param" in parsed
    except json.JSONDecodeError:
        pytest.fail(f"Example JSON in prompt is not valid: {json_str}")
