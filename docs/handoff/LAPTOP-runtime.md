# You're on: the runtime

You build the thing that actually runs an agent. Everything else on this team
either hands you a spec or displays what you recorded.

## Setup

**Do `docs/handoff/SETUP.md` first.** It installs Git, Python and
Claude Code in the right order, and covers the Windows traps: the Microsoft Store
Python stub, the line-ending problem, and the path-length limit.

Come back here once `python scripts/doctor.py` says **Ready to build** and you
are on your branch:

```
git checkout -b wf/runtime origin/wf/runtime
```

## Then start your agent

```
claude
```

Paste this in:

> You are the WF1 runtime agent on branch `wf/runtime` of this repo. Read
> `docs/agents/WF1-runtime.md` and follow it exactly. Before you write any
> middleware, read `docs/research/langchain.md`: two assumptions in the spec were
> refuted against the actual LangChain source. There is no `before_tool` hook,
> and `langchain-mcp-adapters` cannot filter a server down to a subset of its
> tools, so permission enforcement has to live in a `wrap_tool_call` middleware.
> Read `docs/research/openrouter.md` before building the budget limit, because
> cost only arrives in the final streaming chunk. You are waiting on handoff H1
> for a real resolver: run `python scripts/handoff.py wait H1` and build against
> the fake it names rather than sitting idle. Start with task 1.

## Your own jobs, away from the agent

**Around 05:00, ask for the exact planted-secret string** from the demo pull
request. It goes into your redaction exact-match list alongside the regex
patterns. A regex miss that puts an AWS key on screen during judging loses the
round, so this belongs in two independent layers rather than one.

Otherwise nothing. Start now.

## What you must announce

```
python -m pytest tests/contract/test_run_lifecycle.py -q
python scripts\handoff.py ready H2          # due 06:30 - TWO agents are waiting

python -m pytest tests/contract/test_approval_pause_resume.py -q
python scripts\handoff.py ready H6          # due 08:00 - dashboard is waiting
```

H2 unblocks the eval engine and the dashboard's trace viewer, so release it the
moment it passes rather than at the end of your next task. Paste the "unblocked"
list into the group chat.

## Three things that will cost you an hour if you learn them late

**There is no `before_tool` hook.** It does not exist. The gate that fires after
the model has asked for tools but before any of them run is `after_model` with
`can_jump_to=["end"]`. Code written against `before_tool` silently never runs,
which is worse than an error.

**Pin `langchain-openai>=1.1.11`.** Below that version the cost field is silently
dropped, with no error and no warning, so every budget reads zero everywhere and
your kill-switch never fires.

**Cost arrives only at the end of a stream**, so the budget halts at a turn
boundary by accumulating per completed model call. There is no mid-generation
signal to abort on. Also, `:free` models report a cost of exactly `0.0`, so
testing your budget against one passes for entirely the wrong reason. Use a paid
model for that test.

## If you're stuck

`python scripts\handoff.py status`. If H1 is still pending, run
`python scripts\handoff.py wait H1` and it tells you what to build against
instead. Do not poll in a loop and do not sit idle. Stay inside the directories
your brief lists.
