"""Task 2 browser-agent eval runner.

Loads `tasks.yaml`, runs each task through `workers.browser.handlers.run_task`,
and scores it against the per-task success_criteria + negative_oracle.

Methodology (per research delta §6):
- Negative oracle pass rate is the primary cheap signal.
- success_criteria checks are deterministic (substring or regex).
- Failed-but-not-aborted tasks count as partial successes for trend tracking.

Usage:
    python evals/browser-tasks/runner.py
    python evals/browser-tasks/runner.py --pack finance
    python evals/browser-tasks/runner.py --task fin-001
    python evals/browser-tasks/runner.py --headed   # disable headless for debugging
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from workers.browser.handlers import run_task  # noqa: E402
from workers.browser.schema import TaskInput  # noqa: E402


def _criterion_pass(crit: dict, extracted_text: str, task_text: str) -> bool:
    field = crit.get("field", "extracted")
    value = crit.get("value", "")
    haystack = extracted_text if field == "extracted" else task_text
    if crit.get("type") == "regex":
        return re.search(value, haystack or "") is not None
    return value.lower() in (haystack or "").lower()


def _oracle_violations(post_text: str, oracle: dict) -> list[str]:
    violations: list[str] = []
    text_lower = (post_text or "").lower()
    for must in oracle.get("must_appear_on_final_page", []):
        if must.lower() not in text_lower:
            violations.append(f"missing must_appear: {must!r}")
    for must_not in oracle.get("must_not_appear", []):
        if must_not.lower() in text_lower:
            violations.append(f"present must_not_appear: {must_not!r}")
    return violations


async def run_one(task: dict) -> dict:
    t0 = time.perf_counter()
    task_input = TaskInput(
        task=task["nl_description"],
        starting_url=task.get("starting_url"),
        max_steps=task.get("step_cap", 25),
        max_seconds=task.get("wall_clock_cap_s", 180),
    )
    try:
        result = await run_task(task_input)
    except Exception as exc:  # noqa: BLE001
        return {
            "task_id": task["task_id"],
            "ok": False,
            "fail_reason": "runner_crash",
            "error": f"{type(exc).__name__}: {exc}",
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }

    extracted_text = ""
    if isinstance(result.extracted_content, dict):
        extracted_text = result.extracted_content.get("text", "") or ""
    elif isinstance(result.extracted_content, str):
        extracted_text = result.extracted_content

    last_post_text = ""
    if result.trajectory:
        last_event = result.trajectory[-1]
        if last_event.result.post:
            last_post_text = last_event.result.post.text_excerpt

    success_pass = all(
        _criterion_pass(c, extracted_text, task["nl_description"])
        for c in task.get("success_criteria", [])
    )
    oracle_viols = _oracle_violations(last_post_text, task.get("negative_oracle", {}))

    trajectory_summary = [
        {
            "step": e.step.action_type.value,
            "intent": e.step.target_intent,
            "tier": (e.result.locator_tier.value if e.result.locator_tier else None),
            "success": e.result.success,
            "validation": e.validation.decision.value,
            "reason": e.validation.reason,
            "url": (e.result.post.url if e.result.post else None),
        }
        for e in result.trajectory
    ]

    return {
        "task_id": task["task_id"],
        "pack": task.get("pack"),
        "domain": task.get("domain"),
        "ok": result.ok and success_pass and not oracle_viols,
        "result_ok": result.ok,
        "success_criteria_pass": success_pass,
        "oracle_violations": oracle_viols,
        "fail_reason": result.fail_reason,
        "steps_executed": len(result.trajectory),
        "selector_cache_hits": result.selector_cache_hits,
        "selector_cache_writes": result.selector_cache_writes,
        "duration_ms": result.duration_ms,
        "extracted_excerpt": extracted_text[:300],
        "trajectory_summary": trajectory_summary,
    }


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return {}
    by_pack: dict[str, list[dict]] = {}
    for r in rows:
        by_pack.setdefault(r.get("pack") or "?", []).append(r)
    out = {
        "n": len(rows),
        "n_ok": sum(1 for r in rows if r["ok"]),
        "n_result_ok": sum(1 for r in rows if r.get("result_ok")),
        "n_success_criteria_pass": sum(1 for r in rows if r.get("success_criteria_pass")),
        "fail_reason_histogram": {},
        "by_pack": {},
    }
    for r in rows:
        fr = r.get("fail_reason", "?")
        out["fail_reason_histogram"][fr] = out["fail_reason_histogram"].get(fr, 0) + 1
    for pack, prows in by_pack.items():
        out["by_pack"][pack] = {
            "n": len(prows),
            "n_ok": sum(1 for r in prows if r["ok"]),
            "success_rate": round(sum(1 for r in prows if r["ok"]) / len(prows), 3),
        }
    return out


async def main_async(args) -> int:
    tasks_yaml = yaml.safe_load(args.tasks_file.read_text())
    if args.pack:
        tasks_yaml = [t for t in tasks_yaml if t.get("pack") == args.pack]
    if args.task:
        tasks_yaml = [t for t in tasks_yaml if t["task_id"] == args.task]
    if not tasks_yaml:
        print("no tasks matched filters", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for t in tasks_yaml:
        print(f"\n[{t['task_id']}] {t['nl_description'][:80]!r}", flush=True)
        row = await run_one(t)
        status = "OK" if row["ok"] else "FAIL"
        print(f"  {status} steps={row.get('steps_executed', 0)} "
              f"cache_hits={row.get('selector_cache_hits', 0)} "
              f"duration={row.get('duration_ms', 0)}ms "
              f"oracle_violations={len(row.get('oracle_violations', []))}", flush=True)
        if row.get("oracle_violations"):
            for v in row["oracle_violations"]:
                print(f"    - {v}", flush=True)
        rows.append(row)

    summary = _summary(rows)
    args.out.write_text(json.dumps({"rows": rows, "summary": summary}, indent=2,
                                    default=str))
    print()
    print("=" * 60)
    print(f"Total: {summary['n_ok']}/{summary['n']} passed")
    for pack, ps in summary["by_pack"].items():
        print(f"  {pack}: {ps['n_ok']}/{ps['n']} ({ps['success_rate']:.0%})")
    print(f"Fail reasons: {summary['fail_reason_histogram']}")
    return 0 if summary["n_ok"] == summary["n"] else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks-file", type=Path,
                   default=Path(__file__).parent / "tasks.yaml")
    p.add_argument("--pack", choices=["generic", "finance", "edge_case"], default=None)
    p.add_argument("--task", type=str, default=None,
                   help="Run only this task_id")
    p.add_argument("--out", type=Path,
                   default=Path(__file__).parent / "last_run.json")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
