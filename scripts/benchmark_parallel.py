#!/usr/bin/env python3
"""Serial relay vs --experimental-parallel: one real request through both
pipelines, real LLM calls, reporting wall-clock/tokens/cost for each.

Usage:
    python scripts/benchmark_parallel.py [MODEL]

MODEL defaults to whatever a bare `Settings()` resolves to (your own
configured default) if not given. Pick something you know can actually
reach a model in your environment — this script does not know or care
which provider you use, but it does need ONE that answers.

Never touches your real ~/.relaycli/config.toml: load_app_config is
patched (per-module — relay.py, core/roster.py, and appconfig.py each
hold an independent reference from their own `from X import Y`, so all
three need patching, not just the original) to return an in-memory
AppConfig for the duration of this process only, routing every model
tier straight to MODEL. planner_model/coder_model/etc. are also set
directly on Settings, since Stage 2's escalation (agent/router.py) tries
further candidates on a failure independent of the tier system, and a
model that isn't reachable in your environment will otherwise silently
escalate to whatever config.toml's "strong" tier happens to name instead
of the one you asked this script to benchmark.

Each side runs in its own fresh temp directory so neither's file writes
affect the other's starting state. A per-call timeout guards against a
hang (observed once during this tool's own development: a request that
never returned and never raised) turning into an indefinite wait.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

CALL_TIMEOUT_S = 180

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from relaycli.config import PermissionMode, Settings, get_settings
from relaycli.config.manager import AppConfig
from relaycli.core.context import ProjectContext
from relaycli.core.llm import LLM
from relaycli.core.permissions import PermissionManager

REQUEST = (
    "Add three independent utility functions to utils.py: is_palindrome(s), "
    "reverse_words(s), and count_vowels(s), each with a one-line docstring. "
    "No tests, no other files."
)


def _settings(model: str) -> Settings:
    return Settings(
        _env_file=None, model=model, permission_mode=PermissionMode.full_auto,
        planner_model=model, coder_model=model, reviewer_model=model,
        explorer_model=model, tester_model=model,
    )


async def run_serial(model: str) -> dict:
    from relaycli.relay import Relay

    with tempfile.TemporaryDirectory() as tmp:
        project = ProjectContext(tmp)
        settings = _settings(model)
        permissions = PermissionManager(settings.permission_mode)
        llm = LLM(settings)
        relay = Relay(settings, project=project, permissions=permissions, llm=llm)
        start = time.perf_counter()
        result = await asyncio.wait_for(
            asyncio.to_thread(relay.run, REQUEST), timeout=CALL_TIMEOUT_S,
        )
        elapsed = time.perf_counter() - start
        return {
            "pipeline": "serial (relay)", "elapsed_s": round(elapsed, 1),
            "tokens": result.usage.total_tokens, "cost_usd": result.usage.cost_usd,
            "cycles": result.cycles, "stopped_reason": result.stopped_reason,
        }


async def run_parallel_bench(model: str) -> dict:
    from relaycli.agent.orchestrator import run_parallel

    with tempfile.TemporaryDirectory() as tmp:
        project = ProjectContext(tmp)
        settings = _settings(model)
        permissions = PermissionManager(settings.permission_mode)
        llm = LLM(settings)
        start = time.perf_counter()
        result = await asyncio.wait_for(
            run_parallel(settings, REQUEST, project=project, permissions=permissions, llm=llm),
            timeout=CALL_TIMEOUT_S,
        )
        elapsed = time.perf_counter() - start
        return {
            "pipeline": "parallel (orchestrator)", "elapsed_s": round(elapsed, 1),
            "tokens": result.budget.spent_tokens_total, "cost_usd": result.budget.spent_usd_total,
            "tasks": len(result.graph.tasks), "all_ok": result.graph.all_ok(),
            "stopped_early": result.stopped_early,
        }


def _print_table(rows: list[dict]) -> None:
    print("\n| pipeline | wall-clock (s) | tokens | cost (USD) |")
    print("|---|---:|---:|---:|")
    for row in rows:
        if row is None:
            continue
        print(f"| {row['pipeline']} | {row['elapsed_s']} | {row['tokens']} | ${row['cost_usd']:.6f} |")


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else get_settings().model
    fake_cfg = AppConfig(path=Path("/nonexistent/benchmark.toml"),
                          tiers={"fast": model, "balanced": model, "strong": model})
    patches = [
        patch("relaycli.relay.load_app_config", lambda *a, **kw: fake_cfg),
        patch("relaycli.core.roster.load_app_config", lambda *a, **kw: fake_cfg),
        patch("relaycli.appconfig.load_app_config", lambda *a, **kw: fake_cfg),
    ]
    for p in patches:
        p.start()

    rows: list[dict | None] = []
    try:
        print(f"model: {model}")
        print(f"request: {REQUEST}\n")
        for label, coro in (("serial", run_serial(model)), ("parallel", run_parallel_bench(model))):
            print(f"--- {label} ---")
            try:
                row = await coro
                print(row)
            except Exception as exc:
                row = None
                print(f"{label} FAILED: {type(exc).__name__}: {exc}")
            rows.append(row)
    finally:
        for p in patches:
            p.stop()

    _print_table(rows)


if __name__ == "__main__":
    asyncio.run(main())
