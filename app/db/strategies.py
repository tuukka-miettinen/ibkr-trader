"""Repository for strategy algorithms and backtest runs."""
from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BacktestRun, StrategyAlgorithm


class StrategyRepository:
    async def save_algorithm(
        self,
        session: AsyncSession,
        name: str,
        script: str,
        description: str | None = None,
    ) -> StrategyAlgorithm:
        # Find the latest version for this name
        stmt = (
            select(func.max(StrategyAlgorithm.version))
            .where(StrategyAlgorithm.name == name)
        )
        result = await session.execute(stmt)
        max_version = result.scalar()
        next_version = (max_version or 0) + 1

        algo = StrategyAlgorithm(
            name=name,
            version=next_version,
            script=script,
            description=description,
        )
        session.add(algo)
        await session.commit()
        await session.refresh(algo)
        return algo

    async def get_algorithm(
        self,
        session: AsyncSession,
        algorithm_id: str,
    ) -> StrategyAlgorithm | None:
        stmt = select(StrategyAlgorithm).where(StrategyAlgorithm.id == algorithm_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_algorithms(
        self,
        session: AsyncSession,
    ) -> list[StrategyAlgorithm]:
        stmt = (
            select(StrategyAlgorithm)
            .order_by(StrategyAlgorithm.name, StrategyAlgorithm.version.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def set_favorite(
        self,
        session: AsyncSession,
        algorithm_id: str,
        is_favorite: bool,
    ) -> StrategyAlgorithm | None:
        algo = await self.get_algorithm(session, algorithm_id)
        if algo is None:
            return None
        algo.is_favorite = is_favorite
        await session.commit()
        await session.refresh(algo)
        return algo

    async def list_favorites(
        self,
        session: AsyncSession,
    ) -> list[StrategyAlgorithm]:
        stmt = (
            select(StrategyAlgorithm)
            .where(StrategyAlgorithm.is_favorite == True)
            .order_by(StrategyAlgorithm.name, StrategyAlgorithm.version.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def save_run(
        self,
        session: AsyncSession,
        algorithm_id: str,
        symbol: str,
        config: dict,
        result_data: dict,
        mode: str = "tick",
        lookback_days: int | None = None,
    ) -> BacktestRun:
        summary = result_data.get("summary", {})
        run = BacktestRun(
            algorithm_id=algorithm_id,
            symbol=symbol.upper(),
            mode=mode,
            lookback_days=lookback_days,
            config_json=config,
            result_json=result_data,
            num_trades=summary.get("num_trades", 0),
            total_pnl=summary.get("total_pnl", 0.0),
            total_pnl_pct=summary.get("total_pnl_pct", 0.0),
            win_rate=summary.get("win_rate", 0.0),
            final_balance=summary.get("final_balance", 0.0),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run

    async def list_runs(
        self,
        session: AsyncSession,
        algorithm_id: str | None = None,
        symbol: str | None = None,
    ) -> list[dict]:
        stmt = (
            select(BacktestRun, StrategyAlgorithm.name, StrategyAlgorithm.version)
            .join(StrategyAlgorithm, BacktestRun.algorithm_id == StrategyAlgorithm.id)
            .order_by(BacktestRun.created_at.desc())
        )
        if algorithm_id is not None:
            stmt = stmt.where(BacktestRun.algorithm_id == algorithm_id)
        if symbol is not None:
            stmt = stmt.where(BacktestRun.symbol == symbol.upper())

        result = await session.execute(stmt)
        rows = []
        for run, algo_name, algo_version in result.all():
            rows.append({
                "id": run.id,
                "algorithm_id": run.algorithm_id,
                "algorithm_name": algo_name,
                "algorithm_version": algo_version,
                "symbol": run.symbol,
                "mode": run.mode,
                "lookback_days": run.lookback_days,
                "num_trades": run.num_trades,
                "total_pnl": run.total_pnl,
                "total_pnl_pct": run.total_pnl_pct,
                "win_rate": run.win_rate,
                "final_balance": run.final_balance,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            })
        return rows

    async def get_run(
        self,
        session: AsyncSession,
        run_id: str,
    ) -> BacktestRun | None:
        stmt = select(BacktestRun).where(BacktestRun.id == run_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
