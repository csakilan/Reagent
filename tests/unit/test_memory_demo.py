"""WF1 memory demo (fixtures/memory/scenario.yaml) as a test.

Runs `python -m runtime.engine.memory_demo` against the fakes and the in-memory
recorder: no database, no credits. Episodic, semantic and procedural memory
each get exact expectations; see the scenario file for what is proposed and why
each proposal must be kept or dropped.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

pytest.importorskip("langchain")
pytest.importorskip("langgraph")

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Checks allowed to FAIL without failing this test. Was run B's five checks
#: until runner.adecide_approval curated rejected/expired runs; keep it empty.
KNOWN_GAP: set[str] = set()


def test_memory_demo_scenario():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([sys.executable, "-m", "runtime.engine.memory_demo"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    fails = set(re.findall(r"^  FAIL  (.+?)(?:  \(.*\))?$", p.stdout, re.M))
    assert "passed" in p.stdout, p.stdout[-3000:] + p.stderr[-3000:]
    assert fails <= KNOWN_GAP, f"unexpected failures {sorted(fails - KNOWN_GAP)}\n{p.stdout}"
    if not fails:
        assert p.returncode == 0, p.stdout
