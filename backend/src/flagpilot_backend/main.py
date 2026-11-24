"""FastAPI entry point."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import get_settings
from .flagsmith_admin import FlagsmithError
from .github_client import GitHubError
from .models import ExperimentPlan, Run
from .pipeline import Pipeline, TransitionError
from .store import RunStore

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = Pipeline(RunStore(settings.data_dir), settings)
    yield
    await app.state.pipeline.aclose()


app = FastAPI(title="FlagPilot", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


def pipeline() -> Pipeline:
    return app.state.pipeline


def _guard(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, f"Run {exc} not found") from exc
    except TransitionError as exc:
        raise HTTPException(409, str(exc)) from exc


# --- setup status ------------------------------------------------------------


@app.get("/api/status")
async def status() -> dict[str, Any]:
    """What's configured, so the UI can walk a new user through setup."""
    out: dict[str, Any] = {
        "model": settings.llm_model,
        "llm": bool(settings.llm_api_key),
        "defaults": {
            "repo": settings.default_repo,
            "base_branch": settings.default_base_branch,
            "app_path": settings.default_app_path,
        },
    }
    p = pipeline()
    try:
        if not settings.flagsmith_admin_key or not settings.flagsmith_environment_key:
            raise ValueError("FLAGSMITH_ADMIN_KEY and FLAGSMITH_ENVIRONMENT_KEY are required.")
        env = await p.flagsmith.get_environment()
        warehouses = await p.flagsmith.list_warehouse_connections()
        out["flagsmith"] = {
            "ok": True,
            "environment": env.get("name"),
            "project_id": p.flagsmith.project_id,
            # "pending_connection" is normal until the first event arrives.
            "warehouse_status": warehouses[0].get("status", "unknown") if warehouses else "missing",
            "warehouses": [{"type": w.get("warehouse_type"), "status": w.get("status")} for w in warehouses],
        }
    except (FlagsmithError, ValueError) as exc:
        out["flagsmith"] = {"ok": False, "error": str(exc)}
    try:
        if not settings.github_token:
            raise ValueError("GITHUB_TOKEN is required.")
        out["github"] = {"ok": True, "login": await p.github.whoami()}
    except (GitHubError, ValueError) as exc:
        out["github"] = {"ok": False, "error": str(exc)}
    return out


# --- runs -----------------------------------------------------------------------


class CreateRun(BaseModel):
    goal: str = Field(min_length=10, max_length=4000)
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    base_branch: str = "main"
    app_path: str = ""


class SimulateBody(BaseModel):
    visitors: int = Field(default=600, ge=50, le=5000)
    rates: dict[str, float]


class ShipBody(BaseModel):
    winner: str


@app.get("/api/runs")
def list_runs() -> list[Run]:
    return pipeline().store.list()


@app.post("/api/runs", status_code=201)
async def create_run(body: CreateRun) -> Run:
    return pipeline().create_run(goal=body.goal, repo=body.repo, base_branch=body.base_branch, app_path=body.app_path)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> Run:
    run = pipeline().store.get(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@app.post("/api/runs/{run_id}/approve-plan")
async def approve_plan(run_id: str, plan: ExperimentPlan | None = None) -> Run:
    return _guard(pipeline().approve_plan, run_id, plan)


@app.post("/api/runs/{run_id}/reject")
async def reject(run_id: str) -> Run:
    return _guard(pipeline().reject, run_id)


@app.post("/api/runs/{run_id}/launch")
async def launch(run_id: str, force: bool = False) -> Run:
    try:
        return await pipeline().launch(run_id, force=force)
    except KeyError as exc:
        raise HTTPException(404, "Run not found") from exc
    except TransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except FlagsmithError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/runs/{run_id}/simulate")
async def simulate(run_id: str, body: SimulateBody) -> Run:
    return _guard(pipeline().simulate, run_id, visitors=body.visitors, rates=body.rates)


@app.post("/api/runs/{run_id}/analyse")
async def analyse(run_id: str) -> Run:
    return _guard(pipeline().analyse, run_id)


@app.post("/api/runs/{run_id}/ship")
async def ship(run_id: str, body: ShipBody) -> Run:
    return _guard(pipeline().ship, run_id, winner=body.winner)


def run() -> None:
    import uvicorn

    uvicorn.run("flagpilot_backend.main:app", host="127.0.0.1", port=8000, reload=True)
