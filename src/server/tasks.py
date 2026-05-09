"""In-memory task registry for the demo web UI.

Why in-memory: the demo is single-instance, single-tenant, and short-lived
(every task <5 min wall clock). Persisting to disk or DB would add operational
surface area for zero user-visible benefit during a recruiter demo.

Concurrency model: a single asyncio.Semaphore(1) gates `run_task`. We are
spawning a real headless Chromium per task; even on Zeabur's smallest tier we
only have one CPU and ~512 MB. Two concurrent Playwright sessions would either
OOM or contend on the event loop and produce flaky timings — neither is a good
look for a demo.

Cleanup: completed entries linger in the registry indefinitely (keyed by uuid4
so collisions are not a concern). The process is restarted on Zeabur deploys
so memory growth is naturally bounded.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from workers.browser.handlers import run_task
from workers.browser.schema import TaskInput

log = logging.getLogger(__name__)

# Single-flight gate. Demo only — see module docstring for rationale.
_RUN_LOCK = asyncio.Semaphore(1)

# Hard wall-clock cap, regardless of what the client requested. Prevents a
# long-running task from blocking the queue forever if the browser hangs.
_MAX_TASK_SECONDS = 300


TaskStatus = Literal["pending", "running", "done", "error"]


@dataclass
class TaskEntry:
    id: str
    status: TaskStatus
    task_input: dict[str, Any]
    started_at: float
    finished_at: float | None = None
    # trajectory_so_far stays empty in this build — see note in `_run` below.
    # Kept on the dataclass so the polling endpoint can render whatever the
    # final TaskResult contains once status flips to "done".
    trajectory_so_far: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    queued_position: int = 0  # informational; 0 = currently running


_REGISTRY: dict[str, TaskEntry] = {}


def get(task_id: str) -> TaskEntry | None:
    return _REGISTRY.get(task_id)


def is_busy() -> bool:
    """True iff there is a task that has not yet reached a terminal state."""
    return any(e.status in ("pending", "running") for e in _REGISTRY.values())


def submit(payload: dict[str, Any]) -> TaskEntry:
    """Validate input, create an entry, and schedule the background coroutine.

    Returns immediately with the entry in `pending` state. The background
    coroutine flips it to `running` once the semaphore is acquired.
    """
    # Validate eagerly so the client gets a 4xx instead of an opaque background
    # error they can only discover via /api/status.
    task_input = TaskInput(**payload)
    # Clamp max_seconds to our hard cap.
    if task_input.max_seconds > _MAX_TASK_SECONDS:
        task_input = task_input.model_copy(update={"max_seconds": _MAX_TASK_SECONDS})

    entry = TaskEntry(
        id=str(uuid.uuid4()),
        status="pending",
        task_input=task_input.model_dump(mode="json"),
        started_at=time.time(),
    )
    _REGISTRY[entry.id] = entry

    asyncio.create_task(_run(entry, task_input))
    return entry


async def _run(entry: TaskEntry, task_input: TaskInput) -> None:
    """Background coroutine: acquire the semaphore, run the task, fill in entry."""
    async with _RUN_LOCK:
        entry.status = "running"
        entry.started_at = time.time()
        try:
            # Note on trajectory_so_far: handlers.run_task does not currently
            # expose an event hook — adding one is a non-trivial cross-cutting
            # change (would touch handlers.py, actor.py, validator.py to thread
            # an `event_hook: Callable | None` through). For the demo we
            # accept a "spinner until done, then full trajectory" UX rather
            # than risk regressions to the eval-verified core. If the demo
            # ends up needing live progress, the right place to add the hook
            # is in handlers.run_task right after `trajectory.append(...)`.
            #
            # asyncio.wait_for enforces our hard cap on top of the run_task
            # internal deadline, in case run_task itself is wedged in
            # Playwright before reaching its loop deadline check.
            result = await asyncio.wait_for(
                run_task(task_input),
                timeout=_MAX_TASK_SECONDS + 30,
            )
            entry.result = result.model_dump(mode="json")
            # Populate trajectory_so_far at the end so the UI's "in-progress"
            # rendering path also works for the terminal state.
            entry.trajectory_so_far = entry.result.get("trajectory", [])
            entry.status = "done"
        except TimeoutError:
            log.error("task %s exceeded hard cap of %ss", entry.id, _MAX_TASK_SECONDS)
            entry.error = f"task exceeded hard cap of {_MAX_TASK_SECONDS}s"
            entry.status = "error"
        except Exception as exc:  # noqa: BLE001
            log.exception("task %s crashed", entry.id)
            entry.error = f"{type(exc).__name__}: {exc}"
            entry.status = "error"
        finally:
            entry.finished_at = time.time()
