#!/usr/bin/env python3
"""Start the three MCP wrappers from the connectors environment.

They MUST run from .venv, which has mcp 2.x. Starting them from the runtime
environment fails on `from mcp.server import MCPServer`, because that API does
not exist in mcp 1.x. See scripts/envs.py for why there are two environments.

    python scripts/wrappers.py            # start all three, wait for Ctrl-C
    python scripts/wrappers.py --check    # are they already up?
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIN = os.name == "nt"
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if WIN else "bin/python")


def ports() -> dict[str, int]:
    data = json.loads((ROOT / "contracts" / "connector_actions.json").read_text())
    return {name: int(p) for p, name in data["ports"].items()
            if name in ("memory", "slack", "github")}


def is_up(port: int) -> bool:
    s = socket.socket()
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    p = ports()
    if args.check:
        bad = 0
        for name, port in sorted(p.items(), key=lambda kv: kv[1]):
            up = is_up(port)
            print(f"  {port}  {name:<8} {'up' if up else 'DOWN'}")
            bad += not up
        return 1 if bad else 0

    if not PY.exists():
        sys.exit("No .venv. Run: python scripts/envs.py")

    procs = []
    for name, port in sorted(p.items(), key=lambda kv: kv[1]):
        if is_up(port):
            print(f"  {port}  {name:<8} already up, leaving it")
            continue
        procs.append((name, port, subprocess.Popen(
            [str(PY), "-m", f"connectors.{name}.server"], cwd=ROOT)))

    for _ in range(40):
        if all(is_up(port) for port in p.values()):
            break
        time.sleep(0.5)

    print()
    for name, port in sorted(p.items(), key=lambda kv: kv[1]):
        print(f"  {port}  {name:<8} {'up' if is_up(port) else 'FAILED'}")
    if not all(is_up(port) for port in p.values()):
        print("\nSomething did not start. Check the output above.")
        return 1

    print("\nAll wrappers up. Run the runtime from the OTHER environment:")
    print("  " + (".venv-runtime\\Scripts\\python.exe" if WIN else ".venv-runtime/bin/python")
          + " -m pytest tests -q")
    print("\nCtrl-C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        for name, port, proc in procs:
            proc.terminate()
        print("stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
