#!/usr/bin/env python3
"""ReAgent integration handoff board.

The board lives in .handoffs.yaml on `main` and only on `main`. Every command
here reads origin/main rather than the local checkout, so four agents on four
branches always see the same truth.

Writes go straight to main using git plumbing: a blob, a temp index, a tree and
a commit-tree, pushed to refs/heads/main. That never touches your working tree,
your index or your current branch, and a concurrent update makes the push fail
rather than clobber, so we re-read and retry.

Usage
  handoff.py status                         show the whole board
  handoff.py wait  <ID>                     am I unblocked? exit 0 ready, 1 pending, 2 past deadline
  handoff.py ready <ID>                     mark produced, push to main, print who is unblocked
  handoff.py lock   <branch> --owner X [--reason "..."]
  handoff.py unlock <branch>
  handoff.py check-lock <wf/name>           for CI: exit 1 if this workflow is frozen
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import tempfile

try:
    import yaml
except ImportError:
    sys.exit("pyyaml missing. Run: python3 -m pip install pyyaml  (or scripts/bootstrap.sh)")

BOARD = ".handoffs.yaml"
REMOTE = "origin"
MAIN = "main"

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "cyan": "\033[36m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}


def git(*args: str, check: bool = True, stdin: str | None = None, env: dict | None = None) -> str:
    e = {**os.environ, **(env or {})}
    p = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False,
        input=stdin, env=e,
    )
    if check and p.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{p.stderr.strip()}")
    return p.stdout


def repo_root() -> str:
    return git("rev-parse", "--show-toplevel").strip()


def _on_branch(name: str) -> bool:
    return git("rev-parse", "--abbrev-ref", "HEAD", check=False).strip() == name


def fetch() -> None:
    p = subprocess.run(
        ["git", "fetch", REMOTE, MAIN, "--quiet"], capture_output=True, text=True,
    )
    if p.returncode != 0:
        print(f"{C['yellow']}warning: could not reach {REMOTE} ({p.stderr.strip()[:80]}). "
              f"Reading the last fetched state.{C['reset']}", file=sys.stderr)


def read_board(offline: bool = False) -> dict:
    if not offline:
        fetch()
    raw = git("show", f"{REMOTE}/{MAIN}:{BOARD}", check=False)
    if not raw.strip():
        local = os.path.join(repo_root(), BOARD)
        if os.path.exists(local):
            print(f"{C['yellow']}note: {BOARD} not on {REMOTE}/{MAIN} yet, reading local copy.{C['reset']}",
                  file=sys.stderr)
            raw = open(local).read()
        else:
            sys.exit(f"{BOARD} not found on {REMOTE}/{MAIN} or locally.")
    return yaml.safe_load(raw)


def write_board(raw_text: str, message: str) -> None:
    """Commit raw_text as .handoffs.yaml directly onto main, with retry."""
    root = repo_root()
    for attempt in range(5):
        fetch()
        parent = git("rev-parse", f"{REMOTE}/{MAIN}").strip()
        blob = git("hash-object", "-w", "--stdin", stdin=raw_text).strip()
        with tempfile.TemporaryDirectory() as tmp:
            idx = os.path.join(tmp, "index")
            env = {"GIT_INDEX_FILE": idx}
            git("read-tree", parent, env=env)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{BOARD}", env=env)
            tree = git("write-tree", env=env).strip()
        commit = git("commit-tree", tree, "-p", parent, "-m", message).strip()
        p = subprocess.run(
            ["git", "push", REMOTE, f"{commit}:refs/heads/{MAIN}"],
            capture_output=True, text=True, cwd=root,
        )
        if p.returncode == 0:
            print(f"{C['green']}pushed to {MAIN}{C['reset']}  {commit[:9]}  {message}")
            # This wrote straight to the remote without touching your checkout,
            # which is the point: it works from any branch. The side effect is
            # that a LOCAL main is now behind, so a later `git push origin main`
            # from this clone is rejected as non-fast-forward. Harmless, and the
            # fix is `git pull --rebase origin main`.
            if _on_branch(MAIN):
                print(f"  {C['dim']}you are on {MAIN} locally: run "
                      f"`git pull --rebase origin {MAIN}` before your next push"
                      f"{C['reset']}")
            return
        if "non-fast-forward" in p.stderr or "fetch first" in p.stderr or "rejected" in p.stderr:
            print(f"{C['dim']}main moved, retrying ({attempt + 1}/5){C['reset']}", file=sys.stderr)
            continue
        sys.exit(f"push failed:\n{p.stderr.strip()}")
    sys.exit("could not push after 5 attempts. Someone else is pushing to main; try again.")


def set_scalar(raw: str, section_id: str, key: str, value: str) -> str:
    """Rewrite `key: ...` inside the block whose `- id: <section_id>` or
    `- branch: <section_id>` header we find. Line-based on purpose: it preserves
    every comment in the file, which a yaml round-trip would destroy."""
    lines = raw.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s in (f"- id: {section_id}", f"- branch: {section_id}"):
            start = i
            break
    if start is None:
        sys.exit(f"no entry '{section_id}' in {BOARD}")
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("- id:") or s.startswith("- branch:"):
            break
        if s.startswith(f"{key}:"):
            indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
            lines[j] = f'{indent}{key}: "{value}"\n' if value != "" else f'{indent}{key}: ""\n'
            return "".join(lines)
    sys.exit(f"entry '{section_id}' has no '{key}:' line to update")


def parse_deadline(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def fmt_left(deadline: str | None) -> str:
    d = parse_deadline(deadline) if deadline else None
    if d is None:
        return ""
    delta = d - now()
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        return f"{C['red']}{-mins}m OVERDUE{C['reset']}"
    col = C["red"] if mins < 30 else C["yellow"] if mins < 90 else C["dim"]
    return f"{col}{mins // 60}h{mins % 60:02d}m left{C['reset']}"


def cmd_status(args) -> int:
    b = read_board(args.offline)
    sch = b.get("schedule", {})
    print(f"\n{C['bold']}ReAgent handoff board{C['reset']}   "
          f"freeze {sch.get('code_freeze', '?')[11:16]}  submit {sch.get('final_submission', '?')[11:16]}  "
          f"now {now():%H:%M}\n")
    hdr = 'WAITING ON IT'
    print(f"  {'ID':<4}{'STATUS':<10}{'PRODUCER':<14}{hdr:<36}DEADLINE")
    print(f"  {'-' * 92}")
    for h in b["handoffs"]:
        st = h.get("status", "pending")
        col = C["green"] if st == "ready" else C["yellow"]
        cons = ", ".join(c.replace("wf/", "") for c in h.get("consumers", []))
        print(f"  {h['id']:<4}{col}{st:<10}{C['reset']}{h['producer'].replace('wf/', ''):<14}"
              f"{cons:<36}{h.get('deadline', '')[11:16]}  {fmt_left(h.get('deadline'))}")
    held = [l for l in b.get("locks", []) if l.get("status") == "held"]
    if held:
        print(f"\n  {C['red']}{C['bold']}INTEGRATION IN PROGRESS{C['reset']}")
        for l in held:
            print(f"    {l['branch']}  owner {l.get('owner') or '?'}  "
                  f"freezes {', '.join(l['freezes'])}\n      reason: {l.get('reason') or '-'}")
    else:
        print(f"\n  {C['dim']}no integration locks held{C['reset']}")
    print()
    return 0


def cmd_wait(args) -> int:
    b = read_board(args.offline)
    h = next((x for x in b["handoffs"] if x["id"] == args.id), None)
    if h is None:
        sys.exit(f"unknown handoff {args.id}")
    if h.get("status") == "ready":
        print(f"{C['green']}{C['bold']}{args.id} READY{C['reset']}  {h['title']}")
        print(f"  Stop waiting and use the real thing. Verify with: {h['verify']}")
        return 0
    d = parse_deadline(h.get("deadline", ""))
    overdue = d is not None and now() > d
    head = f"{C['red']}PAST DEADLINE{C['reset']}" if overdue else f"{C['yellow']}PENDING{C['reset']}"
    print(f"{C['bold']}{args.id} {head}{C['reset']}  {h['title']}")
    print(f"  produced by : {h['producer']}")
    print(f"  deadline    : {h.get('deadline', '?')}  {fmt_left(h.get('deadline'))}")
    print(f"\n  {C['bold']}What you do instead:{C['reset']}\n    {h.get('fallback', '(none recorded)')}\n")
    if overdue:
        print(f"  {C['red']}Deadline passed. Do not keep waiting. Adopt the fallback permanently,"
              f"\n  note it in your brief, and tell the team.{C['reset']}\n")
        return 2
    return 1


def cmd_ready(args) -> int:
    raw = git("show", f"{REMOTE}/{MAIN}:{BOARD}", check=False) or open(
        os.path.join(repo_root(), BOARD)).read()
    b = yaml.safe_load(raw)
    h = next((x for x in b["handoffs"] if x["id"] == args.id), None)
    if h is None:
        sys.exit(f"unknown handoff {args.id}")
    if h.get("status") == "ready":
        print(f"{C['dim']}{args.id} already ready.{C['reset']}")
        return 0
    print(f"{C['bold']}Releasing {args.id}{C['reset']}  {h['title']}")
    print(f"  Did you run this and see it pass?   {C['cyan']}{h['verify']}{C['reset']}")
    if not args.yes:
        if input("  Type the handoff id to confirm: ").strip() != args.id:
            return sys.exit("aborted.")
    fetch()
    raw = git("show", f"{REMOTE}/{MAIN}:{BOARD}")
    new = set_scalar(raw, args.id, "status", "ready")
    write_board(new, f"handoff: {args.id} ready ({h['title']})")
    print(f"\n{C['green']}{C['bold']}UNBLOCKED:{C['reset']}")
    for c in h.get("consumers", []):
        print(f"  {c}  -> tell that agent to re-run: python scripts/handoff.py wait {args.id}")
    print()
    return 0


def cmd_lock(args) -> int:
    fetch()
    raw = git("show", f"{REMOTE}/{MAIN}:{BOARD}")
    b = yaml.safe_load(raw)
    l = next((x for x in b.get("locks", []) if x["branch"] == args.branch), None)
    if l is None:
        sys.exit(f"unknown lock branch {args.branch}. Known: "
                 + ", ".join(x['branch'] for x in b.get('locks', [])))
    if l.get("status") == "held":
        sys.exit(f"{args.branch} already held by {l.get('owner') or '?'}: {l.get('reason') or '-'}")
    raw = set_scalar(raw, args.branch, "status", "held")
    raw = set_scalar(raw, args.branch, "owner", args.owner)
    raw = set_scalar(raw, args.branch, "reason", args.reason or f"integration testing {', '.join(l['tests'])}")
    write_board(raw, f"lock: {args.branch} held by {args.owner}")
    print(f"\n{C['red']}{C['bold']}FROZEN until you unlock:{C['reset']}")
    for w in l["freezes"]:
        print(f"  {w}  (pushes still work; CI will fail and say why)")
    print(f"\n  Release with: python scripts/handoff.py unlock {args.branch}\n")
    return 0


def cmd_unlock(args) -> int:
    fetch()
    raw = git("show", f"{REMOTE}/{MAIN}:{BOARD}")
    for key, val in (("status", "open"), ("owner", ""), ("reason", "")):
        raw = set_scalar(raw, args.branch, key, val)
    write_board(raw, f"unlock: {args.branch} released")
    print(f"{C['green']}{args.branch} released. Frozen workflows may merge again.{C['reset']}")
    return 0


def cmd_check_lock(args) -> int:
    b = read_board(args.offline)
    for l in b.get("locks", []):
        if l.get("status") == "held" and args.workflow in l.get("freezes", []):
            print(f"{C['red']}{C['bold']}BLOCKED{C['reset']}  {args.workflow} is frozen.")
            print(f"  Integration test running on {C['bold']}{l['branch']}{C['reset']} "
                  f"(owner {l.get('owner') or '?'})")
            print(f"  Reason: {l.get('reason') or '-'}")
            print(f"  Covers: {', '.join(l.get('tests', []))}")
            print(f"\n  Keep committing to your own branch. Do not merge to main until "
                  f"{l.get('owner') or 'the owner'} runs:\n    python scripts/handoff.py unlock {l['branch']}")
            return 1
    print(f"{C['green']}{args.workflow} is not frozen.{C['reset']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="handoff.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--offline", action="store_true", help="skip git fetch")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show the board").set_defaults(fn=cmd_status)
    sub.add_parser("board", help="alias for status").set_defaults(fn=cmd_status)

    w = sub.add_parser("wait", help="check whether a handoff is ready")
    w.add_argument("id")
    w.set_defaults(fn=cmd_wait)

    r = sub.add_parser("ready", help="release a handoff you produced")
    r.add_argument("id")
    r.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    r.set_defaults(fn=cmd_ready)

    lk = sub.add_parser("lock", help="freeze workflows for an integration test")
    lk.add_argument("branch")
    lk.add_argument("--owner", required=True)
    lk.add_argument("--reason", default="")
    lk.set_defaults(fn=cmd_lock)

    ul = sub.add_parser("unlock", help="release an integration lock")
    ul.add_argument("branch")
    ul.set_defaults(fn=cmd_unlock)

    cl = sub.add_parser("check-lock", help="CI: is this workflow frozen?")
    cl.add_argument("workflow")
    cl.set_defaults(fn=cmd_check_lock)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
