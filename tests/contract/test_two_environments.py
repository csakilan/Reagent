"""The runtime and the connectors need incompatible `mcp` versions.

This is not a bug to be fixed by pinning. It is a property of the stack, and it
is load-bearing, so it gets a test. The failure mode this guards against is
someone helpfully running `pip install -r` of both requirements files, which
either fails outright or drags `mcp` to one side and silently breaks the other.

  runtime/engine/binding.py   MultiServerMCPClient, from langchain-mcp-adapters
                              ==0.3.2, which pins mcp<2.0.0
  connectors/common.py        `from mcp.server import MCPServer`, the mcp v2 API

Checked 2026-10-25: langchain-mcp-adapters 0.3.1 drops the upper bound but then
fails to import against mcp 2.x (`RequestContext` moved out of
`mcp.shared.context`), so the cap in 0.3.2 is honest and there is no pin that
satisfies both.

They talk over HTTP and the v2 server serves both protocol eras, routed by the
MCP-Protocol-Version header. Verified: an mcp 1.30.0 client completed discovery
and an authenticated tool call against an mcp 2.3.0 wrapper, returning real rows
from Tiger Cloud.

Setup is `python scripts/envs.py`.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WIN = os.name == "nt"


def _py(venv: str) -> pathlib.Path:
    return ROOT / venv / ("Scripts/python.exe" if WIN else "bin/python")


def _version(venv: str, pkg: str) -> str | None:
    exe = _py(venv)
    if not exe.exists():
        return None
    r = subprocess.run(
        [str(exe), "-c",
         f"import importlib.metadata as m;print(m.version({pkg!r}))"],
        capture_output=True, text=True)
    return r.stdout.strip() or None


def test_the_conflict_is_real_and_declared():
    """If this ever stops being true, delete the second environment."""
    rt = (ROOT / "runtime" / "engine" / "requirements.txt").read_text()
    co = (ROOT / "connectors" / "requirements.txt").read_text()

    m = re.search(r"^langchain-mcp-adapters==(\S+)", rt, re.M)
    assert m, "runtime must pin langchain-mcp-adapters exactly"
    assert m.group(1) == "0.3.2", (
        f"adapters moved to {m.group(1)}. Re-check whether it still caps mcp<2; "
        f"if not, the two environments can be merged and this test deleted.")

    m2 = re.search(r"^mcp>=(\d+)\.", co, re.M)
    assert m2, "connectors must pin an mcp major"
    assert m2.group(1) == "2", (
        "connectors no longer require mcp 2.x; re-evaluate the split")


def test_connectors_use_the_v2_server_api():
    """`from mcp.server import MCPServer` does not exist in mcp 1.x, which is
    the whole reason the wrappers cannot run in the runtime environment."""
    src = (ROOT / "connectors" / "common.py").read_text()
    assert "from mcp.server import MCPServer" in src


def test_runtime_uses_the_v1_capped_client():
    src = (ROOT / "runtime" / "engine" / "binding.py").read_text()
    assert "langchain_mcp_adapters" in src


@pytest.mark.parametrize("venv,pkg,prefix", [
    (".venv", "mcp", "2."),
    (".venv-runtime", "mcp", "1."),
    (".venv-runtime", "langchain", "1.4"),
])
def test_each_environment_has_the_right_mcp(venv, pkg, prefix):
    v = _version(venv, pkg)
    if v is None:
        pytest.skip(f"{venv} not built here — run python scripts/envs.py")
    assert v.startswith(prefix), (
        f"{venv} has {pkg} {v}, expected {prefix}*. The environments have been "
        f"merged or cross-installed. Run: python scripts/envs.py")


def test_connector_env_has_no_langchain_adapters():
    """The specific accident this guards: installing the runtime's requirements
    into .venv pulls adapters, which drags mcp down to 1.x, and every wrapper
    then fails to import."""
    v = _version(".venv", "langchain-mcp-adapters")
    if not _py(".venv").exists():
        pytest.skip(".venv not built here")
    assert v is None, (
        f".venv has langchain-mcp-adapters {v}, which caps mcp<2 and breaks the "
        f"wrappers. Rebuild with: python scripts/envs.py")


def test_ports_match_the_registry():
    """The wrappers bind from transport.url in the registry, and nothing else."""
    data = json.loads((ROOT / "contracts" / "connector_actions.json").read_text())
    assert data["ports"] == {"7100": "memory", "7101": "slack",
                             "7102": "github", "8000": "platform"}
