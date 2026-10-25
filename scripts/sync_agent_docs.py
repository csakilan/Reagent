#!/usr/bin/env python3
"""Keep AGENTS.md in step with CLAUDE.md.

Three laptops run Claude Code, which reads CLAUDE.md. One runs Codex, which
reads AGENTS.md and does not read CLAUDE.md at all. Without this, that laptop's
agent starts with no frozen decisions, no verified research and no design rules,
and spends an hour rediscovering that `before_tool` does not exist.

CLAUDE.md is the source. AGENTS.md is generated. A symlink would be the obvious
answer and is the wrong one: three of the four clones are Windows, where git
checks out a symlink as a text file containing the path unless core.symlinks is
on, so the Codex laptop would read the literal string "CLAUDE.md".

    python scripts/sync_agent_docs.py           # regenerate
    python scripts/sync_agent_docs.py --check    # CI: fail if stale
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "CLAUDE.md"
OUT = ROOT / "AGENTS.md"

# Codex truncates project docs past project_doc_max_bytes (32 KiB by default)
# and does so SILENTLY, so a file that grows past it loses its tail without
# telling anyone. The design rules live near the end of this one.
MAX_BYTES = 32 * 1024

HEADER = """<!-- GENERATED from CLAUDE.md by scripts/sync_agent_docs.py. Do not edit.
     Edit CLAUDE.md and re-run the script. CI fails if the two drift.

     Why both files exist: this team runs three laptops on Claude Code, which
     reads CLAUDE.md, and one on Codex, which reads AGENTS.md. The content is
     identical. When working inside dashboard/, Codex also loads
     dashboard/AGENTS.md, which is closer to the code and therefore carries
     more weight. -->

"""


def render() -> str:
    body = SRC.read_text()
    # Inside AGENTS.md, self-references should name AGENTS.md.
    body = body.replace(
        "CLAUDE.md                  this file",
        "AGENTS.md                  this file (generated from CLAUDE.md)")
    body = body.replace(
        "Shared and owned by nobody: `contracts/` (frozen), `CLAUDE.md`, `.handoffs.yaml`.",
        "Shared and owned by nobody: `contracts/` (frozen), `CLAUDE.md` and its\n"
        "generated twin `AGENTS.md`, and `.handoffs.yaml`.")
    body = body.replace(
        "# ReAgent — read this first, all of it",
        "# ReAgent — read this first, all of it")
    return HEADER + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="CI mode: fail if stale")
    args = ap.parse_args()

    if not SRC.exists():
        sys.exit("CLAUDE.md not found")
    want = render()
    nbytes = len(want.encode())

    if nbytes > MAX_BYTES:
        print(f"WARNING: AGENTS.md would be {nbytes} bytes, over Codex's "
              f"{MAX_BYTES} byte default. Codex truncates SILENTLY, so the tail "
              f"of the file (the cut order and the design rules) would be lost "
              f"on that laptop. Split it or trim CLAUDE.md.", file=sys.stderr)

    have = OUT.read_text() if OUT.exists() else None
    if args.check:
        if have != want:
            print("AGENTS.md is stale. Run: python scripts/sync_agent_docs.py",
                  file=sys.stderr)
            return 1
        print(f"AGENTS.md matches CLAUDE.md ({nbytes} bytes, "
              f"{MAX_BYTES - nbytes} under the Codex limit)")
        return 0

    OUT.write_text(want)
    print(f"wrote AGENTS.md  {nbytes} bytes  "
          f"({MAX_BYTES - nbytes} bytes under Codex's 32 KiB limit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
