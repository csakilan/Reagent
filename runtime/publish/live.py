"""Background evals for the publish pipeline, so a demo never waits on a blocked request.

Two things happen off the request thread, on ONE worker (the eval sandbox is
serialised anyway, and FIFO order means a pipeline's evals queue behind its own
pre-runs and reuse them):

  - pre-run: when a case is approved or edited in review, and for the inherited
    cases as soon as review opens, the case runs once in the background. Its
    result is cached by case hash (runtime.evals.engine._CACHE), so stage 5 reuses
    it. An edit after approval changes the hash, and the new case runs again.
  - continue: stage 5-7 run in the background. Each case's result is recorded as
    it finishes, and the dashboard polls GET /v0/publish-runs/{id} to show them.

Progress lives in this process only (LIVE), never in the store: the store is
written by request threads too, and a worker saving a stale copy of the run would
overwrite a reviewer's decision. A restart loses progress, not results.
"""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from runtime.evals import run_evals
from runtime.resolver import load_catalog

from . import pipeline

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="publish-evals")
_GUARD = threading.Lock()
LIVE: dict[int, dict[str, Any]] = {}


def _state(run_id: int) -> dict[str, Any]:
    with _GUARD:
        return LIVE.setdefault(run_id, {"prerun": {}, "evals": None})


def _set_prerun(run_id: int, case_id: str, **fields: Any) -> None:
    st = _state(run_id)
    with _GUARD:
        st["prerun"][case_id] = {**st["prerun"].get(case_id, {}), **fields}


def _prerun_job(run_id: int, case_id: str, case_hash: str, store: Any) -> None:
    run = store.get(run_id)
    c = next((x for x in run["cases"] if x["case_id"] == case_id), None)
    current = _state(run_id)["prerun"].get(case_id, {})
    if run["status"] != "running" or c is None or current.get("case_hash") != case_hash:
        return                                   # abandoned, or superseded by a later edit
    _set_prerun(run_id, case_id, status="running")
    draft = copy.deepcopy(run["draft"])
    inherited = c["origin"] == "inherited"
    draft["spec"].setdefault("evaluation", {})["cases"] = [] if inherited else [c["_case"]]
    try:
        rep = run_evals(run["agent"], pipeline._env_runs(), draft=draft, catalog=load_catalog(),
                        include_inherited=inherited, case_ids=[case_id], cache=True)
        one = rep["cases"][0] if rep["cases"] else None
        if one is None:
            _set_prerun(run_id, case_id, status="error", error=rep.get("error") or "case did not run")
        else:
            _set_prerun(run_id, case_id, status="passed" if one["passes"] == one["runs"] else "failed",
                        result=one)
    except Exception as e:                       # a pre-run is an optimisation: never fatal
        _set_prerun(run_id, case_id, status="error", error=f"{type(e).__name__}: {e}"[:300])


def prerun(run: dict[str, Any], case_ids: list[str] | None = None, *, store: Any = None) -> None:
    """Queue background runs for approved/edited (and inherited) cases not yet queued
    at their current hash."""
    store = store or pipeline.STORE
    for c in run["cases"]:
        wanted = (c["origin"] == "inherited" or c["decision"] in ("approved", "edited"))
        if not wanted or (case_ids is not None and c["case_id"] not in case_ids):
            continue
        if _state(run["id"])["prerun"].get(c["case_id"], {}).get("case_hash") == c["case_hash"]:
            continue
        _set_prerun(run["id"], c["case_id"], status="queued", case_hash=c["case_hash"],
                    result=None, error=None)
        _WORKER.submit(_prerun_job, run["id"], c["case_id"], c["case_hash"], store)


def _continue_job(run_id: int, store: Any) -> None:
    st = _state(run_id)

    def on_case(entry: dict[str, Any], done: int, total: int) -> None:
        with _GUARD:
            st["evals"]["cases"].append(entry)
            st["evals"].update(done=done, total=total)

    try:
        pipeline.proceed(run_id, store=store, on_case=on_case)
    except Exception as e:
        with _GUARD:
            st["evals"]["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        with _GUARD:
            st["evals"]["status"] = "done"


def continue_in_background(run_id: int, *, store: Any = None) -> None:
    """Run stages 5-7 on the worker. The caller has already checked the reviews,
    so a refusal reaches the author synchronously (see check_ready)."""
    store = store or pipeline.STORE
    st = _state(run_id)
    with _GUARD:
        if st["evals"] and st["evals"]["status"] != "done":
            return                               # already running: a double click
        st["evals"] = {"status": "running", "cases": [], "done": 0, "total": None, "error": None}
    _WORKER.submit(_continue_job, run_id, store)


def snapshot(run_id: int) -> dict[str, Any] | None:
    with _GUARD:
        st = LIVE.get(run_id)
        return copy.deepcopy(st) if st else None


def wait_idle(timeout: float = 600) -> None:
    """Block until everything queued so far has run (FIFO worker). For tests and scripts."""
    _WORKER.submit(lambda: None).result(timeout=timeout)
