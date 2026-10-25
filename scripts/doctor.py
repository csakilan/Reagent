#!/usr/bin/env python3
"""Does this laptop have what it needs? Run this FIRST, on every machine.

The team is 1 macOS and 3 Windows machines, so every check here is
cross-platform and every install hint is picked for the OS you are on.

    python scripts/doctor.py          # check
    python scripts/doctor.py --fix    # check, and install the Python deps

Exit code 0 means you can start building. Exit 1 means something required is
missing, and the output tells you the exact command to run.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys

WIN = platform.system() == "Windows"
MAC = platform.system() == "Darwin"

C = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
     "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "cyan": "\033[36m"}
if WIN and not os.environ.get("WT_SESSION"):
    os.system("")  # turn on ANSI in older Windows consoles
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}

OK, WARN, BAD = f"{C['green']}  ok {C['reset']}", f"{C['yellow']}warn{C['reset']}", f"{C['red']} BAD{C['reset']}"

results: list[tuple[str, str, str, str]] = []  # state, name, found, hint


def record(state: str, name: str, found: str = "", hint: str = "") -> None:
    results.append((state, name, found, hint))


def run(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        p = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=25)
        return (p.stdout + p.stderr).strip()
    except (subprocess.TimeoutExpired, OSError):
        return None


def install_hint(mac: str, win: str) -> str:
    return mac if MAC else win


def ver_tuple(s: str) -> tuple[int, ...]:
    import re
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", s or "")
    return tuple(int(g) for g in m.groups(default="0")) if m else (0,)


# ---------------------------------------------------------------- required

def check_python() -> None:
    v = sys.version_info
    found = f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"
    if (v.major, v.minor) >= (3, 11):
        record(OK, "Python >= 3.11", found)
    else:
        record(BAD, "Python >= 3.11", found,
               install_hint("brew install python@3.12",
                            "winget install Python.Python.3.12   (then reopen the terminal)"))


def check_git() -> None:
    out = run(["git", "--version"])
    if not out:
        record(BAD, "git", "missing",
               install_hint("xcode-select --install", "winget install Git.Git"))
        return
    record(OK, "git", out.replace("git version ", ""))
    # Windows-specific git config that bites this project
    if WIN:
        lp = (run(["git", "config", "--get", "core.longpaths"]) or "").strip()
        if lp != "true":
            record(WARN, "git core.longpaths", lp or "unset",
                   "git config --global core.longpaths true   "
                   "(node_modules paths exceed Windows' 260-char limit)")
        else:
            record(OK, "git core.longpaths", "true")
        crlf = (run(["git", "config", "--get", "core.autocrlf"]) or "").strip()
        if crlf.lower() == "true":
            record(WARN, "git core.autocrlf", "true",
                   "git config --global core.autocrlf input   "
                   "(.gitattributes pins eol=lf; autocrlf=true fights it)")


def check_node() -> None:
    out = run(["node", "--version"])
    if not out:
        record(BAD, "Node >= 20", "missing",
               install_hint("brew install node", "winget install OpenJS.NodeJS.LTS"))
        return
    if ver_tuple(out)[0] >= 20:
        record(OK, "Node >= 20", out)
    else:
        record(BAD, "Node >= 20", out,
               install_hint("brew upgrade node", "winget upgrade OpenJS.NodeJS.LTS"))
    if not shutil.which("npm"):
        record(BAD, "npm", "missing", "ships with Node; reinstall Node")
    else:
        record(OK, "npm", (run(["npm", "--version"]) or "?").splitlines()[0])


def check_pyyaml() -> None:
    try:
        import yaml  # noqa: F401
        record(OK, "pyyaml", "importable")
    except ImportError:
        record(BAD, "pyyaml", "missing",
               f"{os.path.basename(sys.executable)} -m pip install pyyaml jsonschema")


def check_jsonschema() -> None:
    try:
        import jsonschema  # noqa: F401
        record(OK, "jsonschema", "importable")
    except ImportError:
        record(BAD, "jsonschema", "missing",
               f"{os.path.basename(sys.executable)} -m pip install jsonschema")


# ---------------------------------------------------------------- optional

def check_docker() -> None:
    out = run(["docker", "--version"])
    if not out:
        record(WARN, "Docker (optional)", "missing",
               "NOT NEEDED if Tiger Cloud works (H4 primary path). Only install if "
               "the cloud DB fails and you need local Postgres."
               + ("  On Windows this needs WSL2 and may want a reboot, so leave it "
                  "unless you must: winget install Docker.DockerDesktop" if WIN else
                  "  brew install --cask docker"))
        return
    info = run(["docker", "info"])
    running = bool(info) and "Cannot connect" not in info and "error during connect" not in info
    record(OK if running else WARN, "Docker (optional)",
           out.replace("Docker version ", "") + ("" if running else "  installed but not running"),
           "" if running else "start Docker Desktop if you need the local Postgres fallback")


def check_psql() -> None:
    out = run(["psql", "--version"])
    record(OK if out else WARN, "psql (optional)", out or "missing",
           "" if out else install_hint(
               "brew install libpq && brew link --force libpq",
               "winget install PostgreSQL.psql   (handy for poking the DB; not required)"))


def check_net() -> None:
    for host, port, label in (("openrouter.ai", 443, "openrouter.ai"),
                              ("api.github.com", 443, "api.github.com"),
                              ("tigerdata.com", 443, "tigerdata.com")):
        try:
            socket.create_connection((host, port), timeout=6).close()
            record(OK, f"network: {label}", "reachable")
        except OSError as e:
            record(WARN, f"network: {label}", f"unreachable ({type(e).__name__})",
                   "conference Wi-Fi. Try a phone hotspot before you debug anything else.")


def check_env() -> None:
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    root = root.stdout.strip() or "."
    if os.path.exists(os.path.join(root, ".env")):
        record(OK, ".env", "present")
    else:
        record(WARN, ".env", "missing",
               "copy .env.example to .env and fill in OPENROUTER_API_KEY and DATABASE_URL")


def check_ports() -> None:
    for port, who in ((8000, "runtime"), (3000, "dashboard"),
                      (7101, "github wrapper"), (7102, "slack wrapper")):
        s = socket.socket()
        s.settimeout(1)
        taken = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if taken:
            record(WARN, f"port {port} ({who})", "already in use",
                   "something else is listening; stop it or change the port")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="pip install the missing Python deps")
    args = ap.parse_args()

    print(f"\n{C['bold']}ReAgent laptop check{C['reset']}  "
          f"{platform.system()} {platform.release()}  {platform.machine()}\n")

    if args.fix:
        print(f"{C['dim']}installing python deps...{C['reset']}")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                        "pyyaml", "jsonschema"], check=False)
        print()

    for fn in (check_python, check_git, check_node, check_pyyaml, check_jsonschema,
               check_docker, check_psql, check_env, check_ports, check_net):
        try:
            fn()
        except Exception as e:  # a broken check must never block the team
            record(WARN, fn.__name__, f"check itself failed: {e}")

    width = max(len(n) for _, n, _, _ in results) + 2
    for state, name, found, hint in results:
        print(f"  [{state}] {name:<{width}} {C['dim']}{found}{C['reset']}")
        if hint:
            print(f"         {C['cyan']}-> {hint}{C['reset']}")

    bad = [r for r in results if r[0] == BAD]
    warn = [r for r in results if r[0] == WARN]
    print()
    if bad:
        print(f"{C['red']}{C['bold']}{len(bad)} required thing(s) missing.{C['reset']} "
              f"Fix those, then re-run this.\n")
        return 1
    print(f"{C['green']}{C['bold']}Ready to build.{C['reset']}"
          + (f"  {len(warn)} warning(s) above, none blocking." if warn else "") + "\n")
    print(f"  Next:  python scripts/handoff.py status\n"
          f"         read CLAUDE.md, then docs/agents/<your workflow>.md\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
