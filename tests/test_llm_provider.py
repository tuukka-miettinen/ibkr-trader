"""Tests for LLM provider abstraction."""
import pytest

from app.api.optimize import OptimizationMode, ParameterKind, ParameterSpec
from app.llm.provider import FakeLLMProvider, OpenAIProvider


@pytest.mark.asyncio
async def test_fake_llm_provider_increments_integers() -> None:
    """Test FakeLLMProvider increments integer parameters."""
    provider = FakeLLMProvider()
    
    parameter_space = {
        "ema_period": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=20,
            minimum=5,
            maximum=50,
        ),
    }
    
    proposal = await provider.propose_parameters(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        best_so_far=None,
        iteration=0,
        iteration_history=[],
    )
    
    assert proposal["ema_period"] == 21  # 20 + 1


@pytest.mark.asyncio
async def test_fake_llm_provider_flips_booleans() -> None:
    """Test FakeLLMProvider flips boolean parameters."""
    provider = FakeLLMProvider()
    
    parameter_space = {
        "use_filter": ParameterSpec(
            kind=ParameterKind.BOOLEAN,
            default=True,
        ),
    }
    
    proposal = await provider.propose_parameters(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        best_so_far=None,
        iteration=0,
        iteration_history=[],
    )
    
    assert proposal["use_filter"] is False


@pytest.mark.asyncio
async def test_fake_llm_provider_respects_bounds() -> None:
    """Test FakeLLMProvider respects parameter bounds."""
    provider = FakeLLMProvider()
    
    parameter_space = {
        "threshold": ParameterSpec(
            kind=ParameterKind.INTEGER,
            default=49,
            minimum=5,
            maximum=50,  # At upper bound
        ),
    }
    
    proposal = await provider.propose_parameters(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        best_so_far=None,
        iteration=0,
        iteration_history=[],
    )
    
    # Should clamp to maximum instead of going to 50
    assert proposal["threshold"] == 50


@pytest.mark.asyncio
async def test_fake_llm_provider_cycles_enums() -> None:
    """Test FakeLLMProvider cycles through enum choices."""
    provider = FakeLLMProvider()
    
    parameter_space = {
        "strategy": ParameterSpec(
            kind=ParameterKind.ENUM,
            default="buy_only",
            choices=["buy_only", "sell_only", "both"],
        ),
    }
    
    proposal = await provider.propose_parameters(
        parameter_space=parameter_space,
        mode=OptimizationMode.GLOBAL,
        best_so_far=None,
        iteration=0,
        iteration_history=[],
    )
    
    # Should cycle to next choice
    assert proposal["strategy"] == "sell_only"


def test_openai_provider_requires_api_key() -> None:
    """Test OpenAIProvider raises error without API key."""
    import os
    
    # Save current env var
    original_key = os.environ.get("OPENAI_API_KEY")
    
    try:
        # Clear env var
        os.environ.pop("OPENAI_API_KEY", None)
        
        with pytest.raises(ValueError, match="API key not provided"):
            OpenAIProvider()
    finally:
        # Restore env var
        if original_key:
            os.environ["OPENAI_API_KEY"] = original_key
