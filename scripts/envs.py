#!/usr/bin/env python3
"""Build the two Python environments this project needs, and prove the split.

WHY TWO. The runtime is an MCP *client* and the connectors are MCP *servers*,
and they need incompatible versions of the `mcp` package:

  runtime/engine/binding.py   uses langchain_mcp_adapters.MultiServerMCPClient
                              langchain-mcp-adapters==0.3.2 pins mcp<2.0.0
  connectors/common.py        uses `from mcp.server import MCPServer`
                              which is the mcp v2 API, needs mcp>=2.0

`pip install -r` of both files fails with ResolutionImpossible. There is no
version pin that fixes it: langchain-mcp-adapters 0.3.1 drops the upper bound
but then fails to import against mcp 2.x (RequestContext moved), so the cap in
0.3.2 is honest.

This is fine, because they are separate processes that talk over HTTP, and the
v2 server serves both protocol eras routed by the MCP-Protocol-Version header.
Verified on 2026-10-25: an mcp 1.30.0 client completed discovery AND an
authenticated tool call against an mcp 2.3.0 wrapper, returning real rows.

  .venv           connectors. mcp 2.3.0, no langchain.   Runs the wrappers.
  .venv-runtime   runtime. mcp 1.30.0 + langchain 1.4.7. Runs the API, CLI, tests.

    python scripts/envs.py            # create/update both, then verify
    python scripts/envs.py --verify   # check only, for CI
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIN = os.name == "nt"

ENVS = {
    ".venv": {
        "label": "connectors (MCP servers)",
        "reqs": ["connectors/requirements.txt"],
        "extra": ["pytest>=8"],
        "expect": {"mcp": "2."},
        "forbid": ["langchain-mcp-adapters"],
        "runs": "the three wrappers on ports 7100-7102",
    },
    ".venv-runtime": {
        "label": "runtime (MCP client)",
        "reqs": ["runtime/engine/requirements.txt"],
        "extra": ["pytest>=8", "pytest-asyncio", "typer", "psycopg[binary]>=3.2"],
        "expect": {"mcp": "1.", "langchain": "1.4.7", "langchain-mcp-adapters": "0.3.2"},
        "forbid": [],
        "runs": "the FastAPI app, the CLI, and the whole test suite",
    },
}


def py(venv: pathlib.Path) -> pathlib.Path:
    return venv / ("Scripts/python.exe" if WIN else "bin/python")


def run(cmd: list[str], **kw) -> int:
    return subprocess.run(cmd, cwd=ROOT, **kw).returncode


def build(name: str, spec: dict) -> None:
    venv = ROOT / name
    if not py(venv).exists():
        print(f"  creating {name} ...")
        if run([sys.executable, "-m", "venv", str(venv)]):
            sys.exit(f"could not create {name}")
    exe = str(py(venv))
    run([exe, "-m", "pip", "install", "-q", "--upgrade", "pip"],
        stdout=subprocess.DEVNULL)
    args = [exe, "-m", "pip", "install", "-q"]
    for r in spec["reqs"]:
        args += ["-r", r]
    args += spec["extra"]
    print(f"  installing {spec['label']} ...")
    if run(args):
        sys.exit(f"install failed for {name}. Run without -q to see why.")


def versions(venv: pathlib.Path, pkgs: list[str]) -> dict[str, str | None]:
    code = (
        "import importlib.metadata as m, json, sys\n"
        "out = {}\n"
        f"for p in {pkgs!r}:\n"
        "    try: out[p] = m.version(p)\n"
        "    except Exception: out[p] = None\n"
        "print(json.dumps(out))\n"
    )
    r = subprocess.run([str(py(venv)), "-c", code], capture_output=True, text=True)
    if r.returncode:
        return {p: None for p in pkgs}
    import json
    return json.loads(r.stdout)


def verify() -> int:
    ok = True
    for name, spec in ENVS.items():
        venv = ROOT / name
        print(f"\n{name}  ({spec['label']})")
        if not py(venv).exists():
            print(f"  MISSING. Run: python scripts/envs.py")
            ok = False
            continue
        want = list(spec["expect"]) + spec["forbid"]
        got = versions(venv, want)
        for pkg, prefix in spec["expect"].items():
            v = got.get(pkg)
            if v is None:
                print(f"  {pkg:<26} MISSING, expected {prefix}*")
                ok = False
            elif not v.startswith(prefix):
                print(f"  {pkg:<26} {v}  WRONG, expected {prefix}*")
                ok = False
            else:
                print(f"  {pkg:<26} {v}")
        for pkg in spec["forbid"]:
            if got.get(pkg):
                print(f"  {pkg:<26} {got[pkg]}  SHOULD NOT BE HERE: it would drag mcp back to 1.x "
                      f"and the wrappers would stop importing")
                ok = False
        print(f"  -> runs {spec['runs']}")

    print()
    if not ok:
        print("Environments are not as expected. Run: python scripts/envs.py\n")
        return 1
    print("Both environments correct. The version split is intentional; see the")
    print("docstring in this file for why.\n")
    print("  start the wrappers :  python scripts/wrappers.py")
    print("  run the test suite :  " +
          (".venv-runtime\\Scripts\\python.exe" if WIN else ".venv-runtime/bin/python") +
          " -m pytest tests -q")
    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="check only, do not install")
    args = ap.parse_args()
    if not args.verify:
        print("Building two environments. They CANNOT be merged; see this file's docstring.\n")
        for name, spec in ENVS.items():
            build(name, spec)
    return verify()


if __name__ == "__main__":
    sys.exit(main())
