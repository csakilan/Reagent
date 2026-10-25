"""ReAgent runtime engine. WF1.

Sync entry points used by the contract tests, the eval engine and the API:

    run_agent(name, input, *, user=None, trigger="http", budget_override=None)
    decide_approval(approval_id, decision="approved", approver=None)
    expire_approval(approval_id)

Each returns a dict with at least run_id and status
(succeeded | failed | denied | awaiting_approval). Async twins are prefixed `a`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from runtime.engine.runner import aexpire_approval, adecide_approval, arun_agent

__all__ = ["run_agent", "decide_approval", "expire_approval",
           "arun_agent", "adecide_approval", "aexpire_approval"]


def _sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside a running loop (e.g. a notebook): run on a worker thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def run_agent(name: str, input: Any, *, user: str | None = None, trigger: str = "http",
              budget_override: dict[str, Any] | None = None) -> dict[str, Any]:
    return _sync(arun_agent(name, input, user=user, trigger=trigger,
                            budget_override=budget_override))


def decide_approval(approval_id: int, decision: str = "approved",
                    approver: str | None = None) -> dict[str, Any]:
    return _sync(adecide_approval(approval_id, decision=decision, approver=approver))


def expire_approval(approval_id: int) -> dict[str, Any]:
    return _sync(aexpire_approval(approval_id))
