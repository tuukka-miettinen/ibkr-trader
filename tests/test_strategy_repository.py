from __future__ import annotations

import pytest

from app.db.strategies import StrategyRepository


@pytest.mark.asyncio
async def test_save_algorithm_reuses_existing_version_for_identical_script(test_db_session):
    repo = StrategyRepository()
    script = 'STRATEGY_NAME = "Same"\n\n' 'def on_tick(state):\n    return {"signal": None}\n'

    first = await repo.save_algorithm(test_db_session, "Same", script)
    second = await repo.save_algorithm(test_db_session, "Same", script)

    assert second.id == first.id
    assert second.version == 1
    assert second.script_hash == first.script_hash


@pytest.mark.asyncio
async def test_save_algorithm_normalizes_line_endings_before_hashing(test_db_session):
    repo = StrategyRepository()
    unix_script = 'STRATEGY_NAME = "Same"\n\ndef on_tick(state):\n    return {"signal": None}\n'
    windows_script = unix_script.replace("\n", "\r\n")

    first = await repo.save_algorithm(test_db_session, "Same", unix_script)
    second = await repo.save_algorithm(test_db_session, "Same", windows_script)

    assert second.id == first.id
    assert second.version == 1


@pytest.mark.asyncio
async def test_save_algorithm_ignores_whitespace_and_comments(test_db_session):
    repo = StrategyRepository()
    compact_script = 'STRATEGY_NAME = "Same"\n\ndef on_tick(state):\n    return {"signal": None}\n'
    spaced_script = 'STRATEGY_NAME = "Same"\n\n# cosmetic comment\ndef on_tick(  state  ):\n\n    return {  "signal"  :  None  }  # same behavior\n'

    first = await repo.save_algorithm(test_db_session, "Same", compact_script)
    second = await repo.save_algorithm(test_db_session, "Same", spaced_script)

    assert second.id == first.id
    assert second.version == 1


@pytest.mark.asyncio
async def test_save_algorithm_increments_version_when_script_changes(test_db_session):
    repo = StrategyRepository()
    v1_script = 'STRATEGY_NAME = "Same"\n\ndef on_tick(state):\n    return {"signal": None}\n'
    v2_script = 'STRATEGY_NAME = "Same"\n\ndef on_tick(state):\n    return {"signal": "buy"}\n'

    first = await repo.save_algorithm(test_db_session, "Same", v1_script)
    second = await repo.save_algorithm(test_db_session, "Same", v2_script)

    assert second.id != first.id
    assert first.version == 1
    assert second.version == 2
    assert second.script_hash != first.script_hash
