# Setup · do this first, then open your own file

**About 8 minutes.** You already have git and your agent, so this is shorter
than it looks.

At the end you paste one line into the group chat so the coordinator knows your
laptop is ready without chasing you.

---

## 1. Python 3.11 or newer

Everyone needs this. The tooling that tells you what to do next is written in it.

**Windows**, in PowerShell:
```powershell
winget install Python.Python.3.12
```
**Then close PowerShell and open a new one**, and check:
```powershell
python --version
```

If that opens the Microsoft Store instead of printing a version, you have the
Store placeholder rather than a real Python. Turn it off in **Settings → Apps →
Advanced app settings → App execution aliases**, switch off `python.exe` and
`python3.exe`, then open a new terminal. This is the most common Windows false
start and `doctor.py` detects it explicitly.

If `winget` does not exist, install from
[python.org](https://www.python.org/downloads/windows/) and **tick "Add python.exe
to PATH"** on the first screen.

**macOS:**
```bash
brew install python@3.12
```

## 2. Node 20 or newer · dashboard person only

**Windows:**
```powershell
winget install OpenJS.NodeJS.LTS
```
**macOS:**
```bash
brew install node
```

Skip this entirely if you are on resolver, runtime or data.

## 3. Two git settings, before you clone

```
git config --global core.autocrlf input
git config --global core.longpaths true
```

The first stops Windows rewriting every file's line endings, which otherwise
makes `git status` report the whole repository as modified and costs an hour to
diagnose. The second lets `node_modules` paths exceed Windows' 260-character
limit. Both are harmless on macOS.

## 4. Clone and check

```
git clone https://github.com/hanhg/Reagent.git
cd Reagent
python scripts/doctor.py --fix
```

`--fix` installs the two Python packages the tooling needs, then the check tells
you line by line what is ready.

**Fix everything marked `BAD`.** Each one prints the exact command for your
operating system. Warnings are fine: `psql` and `.env` will both warn until
later and neither blocks you.

If `python` is not found, use `py -3` instead, here and everywhere after.

## 5. Get on your branch

Pick the one line for your role:

```
git checkout -b wf/data      origin/wf/data        # macOS laptop
git checkout -b wf/resolver  origin/wf/resolver    # Claude Code laptop 1
git checkout -b wf/runtime   origin/wf/runtime     # Claude Code laptop 2
git checkout -b wf/dashboard origin/wf/dashboard   # Codex laptop
```

**Never work on `main`.** It holds the shared contracts, and CI will refuse a
push that changes them from a workflow branch.

---

## 6. Report in, then open your file

`doctor.py` ends with a line like:

```
READY Windows py3.12 claude=yes node=no warn=2
```

**Paste that into the group chat.**

Then open your own file and follow it:

| You | Your file |
|---|---|
| macOS laptop | [`YOU-data-and-coordination.md`](YOU-data-and-coordination.md) |
| Claude Code 1 | [`LAPTOP-resolver.md`](LAPTOP-resolver.md) |
| Claude Code 2 | [`LAPTOP-runtime.md`](LAPTOP-runtime.md) |
| Codex | [`LAPTOP-dashboard.md`](LAPTOP-dashboard.md) |

---

## A note on the two different agents

**Three laptops run Claude Code.** It loads `CLAUDE.md` automatically, so your
agent already has the whole picture on its first token. Start it with `claude`.

**One laptop runs Codex**, on the dashboard. Codex does not read `CLAUDE.md` at
all; it reads `AGENTS.md`, which is generated from the same source and kept
identical by CI. It also reads `dashboard/AGENTS.md` when working in that
directory, which holds the design rules and carries more weight because it sits
closer to the code. Start it with `codex`.

Either way the context arrives without anyone pasting it.

---

## When something goes wrong

| What you see | What it is | Fix |
|---|---|---|
| `python` opens the Microsoft Store | The Store stub, not a real Python | Settings → Apps → Advanced app settings → App execution aliases → turn off `python.exe` |
| `git status` shows every file modified | Line endings | `git config --global core.autocrlf input`, then delete the folder and clone again |
| `Filename too long` during `npm install` | Windows path limit | `git config --global core.longpaths true`, then clone again |
| `doctor.py` still says pyyaml missing after `--fix` | pip installed into a different Python | `python -m pip install pyyaml jsonschema`, with the same `python` you ran doctor with |
| `ModuleNotFoundError: No module named 'yaml'` | Same cause as above | As above |
| Everything is green but `handoff.py` errors | You are not inside the repo | `cd Reagent` first |
| `claude` or `codex` not found | Not on PATH yet | Open a brand new terminal |

Anything not on this list: paste the whole error into the group chat rather than
guessing. Someone has probably hit it already.
