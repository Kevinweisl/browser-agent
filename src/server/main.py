"""FastAPI demo server for browser-agent.

Run locally:
    uvicorn src.server.main:app --host 0.0.0.0 --port 8000
or:
    python -m src.server.main

Routes:
    POST /api/run            — submit NL task, returns {task_id}
    GET  /api/status/{id}    — poll status + trajectory_so_far
    GET  /api/result/{id}    — full TaskResult JSON when done
    GET  /api/eval-summary   — summary of evals/browser-tasks/last_run.json
    GET  /healthz            — liveness probe
    GET  /                   — serve ui/index.html
    GET  /static/*           — serve ui/{app.js, styles.css}

The demo is single-tenant on purpose — see `tasks.py` for rationale.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Make `workers.browser` importable when run via `python -m src.server.main`
# (matches the same path-bootstrap used by evals/browser-tasks/runner.py).
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# .env passthrough — uvicorn will not load it for us.
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from server import tasks as task_registry  # noqa: E402

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="browser-agent demo", version="0.1.0")

_UI_DIR = _ROOT / "ui"
_EVAL_RESULT_PATH = _ROOT / "evals" / "browser-tasks" / "last_run.json"


# ── Request / response models ────────────────────────────────────────────────


class RunRequest(BaseModel):
    """Subset of TaskInput the UI exposes — secrets not accepted from the web."""

    task: str = Field(min_length=1, max_length=2000)
    starting_url: str | None = None
    max_steps: int = Field(default=25, ge=1, le=50)
    max_seconds: int = Field(default=180, ge=10, le=300)


class RunResponse(BaseModel):
    task_id: str
    status: str


class StatusResponse(BaseModel):
    task_id: str
    status: str
    trajectory_so_far: list[dict[str, Any]]
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: float
    finished_at: float | None = None


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/run", response_model=RunResponse)
async def api_run(req: RunRequest) -> RunResponse:
    if task_registry.is_busy():
        # 429 is the right status — we're rate-limiting, not refusing the
        # request shape. The UI displays this as a friendly "demo is busy".
        raise HTTPException(
            status_code=429,
            detail="demo is single-concurrency and another task is in flight; try again in a moment",
        )
    # Log the NL task for audit; do not log secrets (we don't accept any).
    log.info("new task: %r starting_url=%r", req.task[:200], req.starting_url)
    entry = task_registry.submit(req.model_dump())
    return RunResponse(task_id=entry.id, status=entry.status)


@app.get("/api/status/{task_id}", response_model=StatusResponse)
async def api_status(task_id: str) -> StatusResponse:
    entry = task_registry.get(task_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return StatusResponse(
        task_id=entry.id,
        status=entry.status,
        trajectory_so_far=entry.trajectory_so_far,
        result=entry.result,
        error=entry.error,
        started_at=entry.started_at,
        finished_at=entry.finished_at,
    )


@app.get("/api/result/{task_id}")
async def api_result(task_id: str) -> dict[str, Any]:
    entry = task_registry.get(task_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if entry.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"task is in status={entry.status}; result not yet available",
        )
    assert entry.result is not None
    return entry.result


@app.get("/api/eval-summary")
async def api_eval_summary() -> dict[str, Any]:
    """Summary of the held-out eval run, used by the UI's eval banner."""
    if not _EVAL_RESULT_PATH.exists():
        return {"available": False, "message": "no eval result on disk"}
    try:
        data = json.loads(_EVAL_RESULT_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("eval summary load failed: %s", exc)
        return {"available": False, "message": f"failed to read: {exc}"}

    summary = data.get("summary", {})
    return {
        "available": True,
        "n": summary.get("n", 0),
        "n_ok": summary.get("n_ok", 0),
        "by_pack": summary.get("by_pack", {}),
        "fail_reason_histogram": summary.get("fail_reason_histogram", {}),
        "path": str(_EVAL_RESULT_PATH.relative_to(_ROOT)),
    }


# ── UI static + index ────────────────────────────────────────────────────────


@app.get("/")
async def index() -> FileResponse:
    index_path = _UI_DIR / "index.html"
    if not index_path.exists():
        return JSONResponse(  # type: ignore[return-value]
            status_code=500,
            content={"error": f"ui/index.html missing at {index_path}"},
        )
    return FileResponse(index_path, media_type="text/html")


# Mount static for app.js / styles.css. Done at module level so uvicorn picks
# it up; if the directory does not exist we skip silently to keep the process
# bootable in degraded states.
if _UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=_UI_DIR), name="static")


# ── Entry point for `python -m src.server.main` ──────────────────────────────


def main() -> None:
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
