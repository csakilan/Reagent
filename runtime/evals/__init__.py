"""The eval engine. Owned by wf/resolver. Report shape: EvalReport in contracts/openapi.yaml.

    from runtime.evals import run_evals
    report = run_evals("pr-reviewer", runs_per_case=5, fake_model=True)

Develop with fake_model=True: five runs per case burns real credits, and
`:free` models report cost 0.0, which makes budget assertions pass vacuously.
"""
from .checks import Trace, run_checks
from .engine import case_hash, collect_cases, gate, run_evals
from .stats import compare, wilson

__all__ = ["run_evals", "compare", "wilson", "case_hash", "collect_cases", "gate",
           "run_checks", "Trace"]
