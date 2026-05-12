from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import OptimizationJob
from app.models.market_data import Timeframe
from app.services.backtest import DEFAULT_SCRIPT, normalize_symbols, validate_strategy_script
from app.services.optimizer import evaluate_candidate

router = APIRouter(prefix="/api/optimize", tags=["optimize"])


class OptimizationMode(StrEnum):
    GLOBAL = "global"
    SECTOR = "sector"


class ParameterKind(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ENUM = "enum"


class ParameterSpec(BaseModel):
    kind: ParameterKind
    default: int | float | bool | str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: list[str] = Field(default_factory=list)
    description: str = ""
    allow_sector_override: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> "ParameterSpec":
        if self.kind in {ParameterKind.INTEGER, ParameterKind.FLOAT}:
            if self.minimum is None or self.maximum is None:
                raise ValueError("numeric parameters require minimum and maximum")
            if self.minimum > self.maximum:
                raise ValueError("minimum must be less than or equal to maximum")
            if self.step is not None and self.step <= 0:
                raise ValueError("step must be positive")
        if self.kind == ParameterKind.ENUM and not self.choices:
            raise ValueError("enum parameters require at least one choice")
        if self.kind != ParameterKind.ENUM and self.choices:
            raise ValueError("choices are only valid for enum parameters")
        return self


class OptimizationRequest(BaseModel):
    script: str = DEFAULT_SCRIPT
    symbols: list[str]
    timeframes: list[Timeframe] = Field(default_factory=lambda: [Timeframe.FIVE_MINUTES, Timeframe.FIFTEEN_MINUTES])
    limit: int = Field(default=1638, ge=50, le=10000)
    mode: OptimizationMode = OptimizationMode.GLOBAL
    parameter_space: dict[str, ParameterSpec]
    iteration_budget: int = Field(default=12, ge=1, le=100)
    train_ratio: float = Field(default=0.67, gt=0.5, lt=0.95)
    sector_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_request(self) -> "OptimizationRequest":
        normalized_symbols = normalize_symbols(self.symbols)
        if not normalized_symbols:
            raise ValueError("at least one symbol is required")
        self.symbols = normalized_symbols

        if not self.parameter_space:
            raise ValueError("parameter_space must define at least one parameter")

        if self.mode == OptimizationMode.SECTOR:
            missing = [symbol for symbol in self.symbols if symbol not in self.sector_map]
            if missing:
                raise ValueError(f"sector_map is missing symbols: {', '.join(missing)}")

        return self


class OptimizationPlanResponse(BaseModel):
    status: str
    mode: OptimizationMode
    symbols: list[str]
    timeframes: list[Timeframe]
    limit: int
    iteration_budget: int
    train_ratio: float
    parameter_names: list[str]
    sector_count: int
    notes: list[str]


@router.post("/plan", response_model=OptimizationPlanResponse)
def build_optimization_plan(body: OptimizationRequest) -> OptimizationPlanResponse:
    try:
        validate_strategy_script(body.script)
    except HTTPException:
        raise

    notes = [
        "This endpoint validates optimization input only. No LLM calls or iterative backtests run yet.",
        "Use parameter_space to constrain the search to bounded values inside a fixed strategy template.",
    ]
    if body.mode == OptimizationMode.SECTOR:
        notes.append("Sector mode expects one sector label per symbol and should only vary approved parameters by sector.")

    return OptimizationPlanResponse(
        status="accepted",
        mode=body.mode,
        symbols=body.symbols,
        timeframes=body.timeframes,
        limit=body.limit,
        iteration_budget=body.iteration_budget,
        train_ratio=body.train_ratio,
        parameter_names=sorted(body.parameter_space),
        sector_count=len(set(body.sector_map.values())),
        notes=notes,
    )


class EvaluateRequest(BaseModel):
    plan: OptimizationRequest
    parameters: dict[str, Any]
    candidate_name: str = "candidate"


class CandidateScore(BaseModel):
    candidate_name: str
    overall_score: float
    pnl_component: float
    win_rate_component: float
    trade_count_component: float
    consistency_bonus: float
    holdout_pnl: float
    holdout_win_rate: float
    holdout_trades: int
    train_pnl: float
    train_trades: int
    justification: str


class EvaluateResponse(BaseModel):
    candidate_name: str
    parameters: dict[str, Any]
    rendered_script: str
    train_summary: dict[str, Any]
    holdout_summary: dict[str, Any]
    score_details: CandidateScore


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate_parameters(body: EvaluateRequest) -> dict[str, Any]:
    try:
        result = evaluate_candidate(body.plan, body.parameters, body.candidate_name)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(exc)}") from exc


class BatchEvaluateRequest(BaseModel):
    plan: OptimizationRequest
    candidates: list[dict[str, Any]] = Field(description="List of parameter dicts to evaluate")


class BatchEvaluateResponse(BaseModel):
    leaderboard: list[dict[str, Any]] = Field(description="Sorted list of evaluated candidates")
    best_candidate: dict[str, Any] = Field(description="Top-scoring candidate")
    notes: list[str] = Field(description="Evaluation notes")


@router.post("/batch-evaluate", response_model=BatchEvaluateResponse)
def batch_evaluate_parameters(body: BatchEvaluateRequest) -> dict[str, Any]:
    try:
        results = []
        for i, parameters in enumerate(body.candidates):
            candidate_name = f"candidate_{i}"
            result = evaluate_candidate(body.plan, parameters, candidate_name)
            results.append(result)

        # Sort by overall_score descending
        leaderboard = sorted(results, key=lambda r: r["score_details"]["overall_score"], reverse=True)
        best = leaderboard[0] if leaderboard else None

        notes = [f"Evaluated {len(leaderboard)} candidates", "Results sorted by overall_score (descending)"]

        return {
            "leaderboard": leaderboard,
            "best_candidate": best,
            "notes": notes,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch evaluation failed: {str(exc)}") from exc


# ============================================================================
# Job Tracking Models and Endpoints (Phase 3)
# ============================================================================


class OptimizationJobStatus(StrEnum):
    """Status of an optimization job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StartOptimizationRequest(BaseModel):
    """Request to start a new optimization job."""

    plan: OptimizationRequest
    provider: str = Field(default="fake", description="LLM provider: 'fake' or 'openai'")


class StartOptimizationResponse(BaseModel):
    """Response when starting a new optimization job."""

    job_id: str = Field(description="Unique job identifier")
    status: OptimizationJobStatus = Field(default=OptimizationJobStatus.QUEUED)
    message: str


class GetOptimizationJobResponse(BaseModel):
    """Response when retrieving optimization job status."""

    job_id: str
    status: OptimizationJobStatus
    plan: OptimizationRequest
    leaderboard: list[dict[str, Any]] = Field(default_factory=list, description="Sorted candidates")
    best_candidate: dict[str, Any] | None = None
    iterations_completed: int = 0
    early_stop_reason: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


# In-process job store (replaced by database in production, but kept for fallback)
_optimization_jobs: dict[str, dict[str, Any]] = {}


@router.post("/start", response_model=StartOptimizationResponse)
async def start_optimization(body: StartOptimizationRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Start a new optimization job.

    Creates an optimization job record in the database and spawns a background task to run the loop.
    Returns a job_id immediately without waiting for results.
    """
    from app.llm.provider import FakeLLMProvider, OpenAIProvider

    # Validate the plan
    try:
        validate_strategy_script(body.plan.script)
    except HTTPException:
        raise

    # Create job in database
    job_id = str(uuid4())
    now = datetime.now()
    
    db_job = OptimizationJob(
        id=job_id,
        job_id=job_id,
        status="queued",
        provider=body.provider.lower(),
        plan_json=body.plan.model_dump(),
        created_at=now,
    )
    
    db.add(db_job)
    await db.commit()
    await db.refresh(db_job)

    # Spawn background task
    provider_name = body.provider.lower()
    if provider_name == "fake":
        llm_provider = FakeLLMProvider()
    elif provider_name == "openai":
        llm_provider = OpenAIProvider()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown LLM provider: {provider_name}")

    async def run_loop() -> None:
        """Run the optimization loop in the background."""
        try:
            from app.db.database import get_db_context
            from app.services.optimization_loop import run_optimization_loop

            # Update job status to running
            async with get_db_context() as session:
                stmt = select(OptimizationJob).where(OptimizationJob.job_id == job_id)
                result = await session.execute(stmt)
                job_record = result.scalar_one()
                job_record.status = "running"
                job_record.started_at = datetime.now()
                await session.commit()

            def on_status_update(update: dict[str, Any]) -> None:
                """Persist loop progress to database."""
                # Note: This runs in the same async context as run_optimization_loop
                # but we'll update the DB after the loop completes for simplicity

            result = await run_optimization_loop(body.plan, llm_provider, on_status_update)

            # Update job record with final results
            async with get_db_context() as session:
                stmt = select(OptimizationJob).where(OptimizationJob.job_id == job_id)
                result_obj = await session.execute(stmt)
                job_record = result_obj.scalar_one()
                
                job_record.leaderboard_json = result.get("leaderboard", [])
                job_record.best_candidate_json = result.get("best_candidate")
                job_record.status = "completed"
                job_record.completed_at = datetime.now()
                
                await session.commit()

        except Exception as exc:
            # Update job with error
            try:
                from app.db.database import get_db_context

                async with get_db_context() as session:
                    stmt = select(OptimizationJob).where(OptimizationJob.job_id == job_id)
                    result_obj = await session.execute(stmt)
                    job_record = result_obj.scalar_one_or_none()
                    if job_record:
                        job_record.status = "failed"
                        job_record.error_message = str(exc)
                        job_record.completed_at = datetime.now()
                        await session.commit()
            except Exception:
                pass  # Log silently if we can't update DB

    asyncio.create_task(run_loop())

    return {
        "job_id": job_id,
        "status": OptimizationJobStatus.QUEUED,
        "message": f"Optimization job {job_id} queued. Use GET /api/optimize/{job_id} to poll status.",
    }


class OptimizationJobListItem(BaseModel):
    """Item in the list of all optimization jobs."""
    job_id: str
    status: str
    provider: str
    created_at: datetime
    completed_at: datetime | None = None
    best_score: float | None = None
    best_candidate_name: str | None = None


@router.get("/jobs", response_model=list[OptimizationJobListItem])
async def list_optimization_jobs(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """List all optimization jobs, most recent first."""
    stmt = select(OptimizationJob).order_by(OptimizationJob.created_at.desc())
    result = await db.execute(stmt)
    job_records = result.scalars().all()
    
    items = []
    for job_record in job_records:
        best_score = None
        best_name = None
        if job_record.best_candidate_json:
            best_score = job_record.best_candidate_json.get("score_details", {}).get("overall_score")
            best_name = job_record.best_candidate_json.get("candidate_name")
        
        items.append({
            "job_id": job_record.job_id,
            "status": job_record.status,
            "provider": job_record.provider,
            "created_at": job_record.created_at,
            "completed_at": job_record.completed_at,
            "best_score": best_score,
            "best_candidate_name": best_name,
        })
    
    return items


@router.get("/{job_id}", response_model=GetOptimizationJobResponse)
async def get_optimization_job(job_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Get the status and results of an optimization job."""
    stmt = select(OptimizationJob).where(OptimizationJob.job_id == job_id)
    result = await db.execute(stmt)
    job_record = result.scalar_one_or_none()
    
    if not job_record:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Deserialize plan from JSON
    plan_data = job_record.plan_json
    plan = OptimizationRequest(**plan_data) if plan_data else None

    return {
        "job_id": job_record.job_id,
        "status": job_record.status,
        "plan": plan,
        "leaderboard": job_record.leaderboard_json or [],
        "best_candidate": job_record.best_candidate_json,
        "iterations_completed": 0,  # Can be computed from leaderboard length
        "early_stop_reason": None,  # Can be stored in DB if needed
        "error_message": job_record.error_message,
        "created_at": job_record.created_at,
        "started_at": job_record.started_at,
        "completed_at": job_record.completed_at,
    }