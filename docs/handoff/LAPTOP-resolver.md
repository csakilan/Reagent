# You're on: the resolver, evals and the publish pipeline

**You are the critical path.** Two other agents cannot start their real work
until your resolver produces output. That is handoff **H1, due 05:00**. You are
also building the part the whole pitch rests on, so you want the two most
uninterrupted hours of anyone on the team.

## Setup

**Do `docs/handoff/SETUP.md` first.** It installs Git, Python and
Claude Code in the right order, and covers the Windows traps: the Microsoft Store
Python stub, the line-ending problem, and the path-length limit.

Come back here once `python scripts/doctor.py` says **Ready to build** and you
are on your branch:

```
git checkout -b wf/resolver origin/wf/resolver
```

## Then start your agent

```
claude
```

Paste this in:

> You are the WF2 resolver agent on branch `wf/resolver` of this repo. Read
> `docs/agents/WF2-resolver.md` and follow it exactly, then read all 25 entries
> in `docs/decisions/FROZEN.md`, because most of them govern your code and they
> override SPEC.md wherever they conflict. Build the resolver before the eval
> engine: `wf/runtime` and `wf/dashboard` are both blocked on handoff H1 until
> you release it. Be careful with `spec/tools/check_examples.py`, because it
> prints ALL PASS on a config with enterprise-locked redaction switched off, so
> treat it as a useful sketch and not as an oracle. Write one unit test per row
> of the merge table in SPEC.md section 1. Run
> `python scripts/handoff.py status` first, then start on task 1.

## Your own jobs, away from the agent

Essentially none, which is exactly why you can start this minute. The resolver
needs no database, no API key and no GitHub app.

Around 06:00 your eval engine will want a real runtime to execute cases against.
Until then it grades recorded traces from `fixtures/`, so you are not blocked
then either.

## What you must announce

The moment each passes, run the command. **Telling people is part of the job.**

```
python -m pytest tests/unit/test_merge_rules.py -q
python -m pytest tests/contract/test_resolver_output.py -q
python scripts\handoff.py ready H1          # due 05:00 - TWO agents are waiting

python -m pytest tests/contract/test_eval_report.py -q
python scripts\handoff.py ready H5          # due 07:30 - dashboard is waiting
```

Each prints exactly who it just unblocked. Paste that list into the group chat so
they know to re-check.

## The one thing to get right

Every merge rule in `SPEC.md` section 1 gets its own unit test. The rules are
literally a table, so there is no excuse for skipping any, and the demo turns on
two of them visibly: adding a GitHub merge action must be rejected while naming
the team that never granted it, and a 2300-second timeout must be rejected while
naming the enterprise limit. The exact wording of both errors is already fixed in
`contracts/errors.py` and goes on screen word for word.

## A warning about your starting point

The validator already in the repo looks finished. It was tested against a
deliberately broken config with enterprise-locked redaction disabled, a memory
bucket redeclared as world-writable, and a widened repository scope. **It printed
ALL PASS.** Its `locked` check is a bare `pass` statement, its allowlist merge is
last-layer-wins instead of a true intersection, and it never reads
`guardrails.deny` at all. `FROZEN.md` lists each of these with the rule to
implement instead.

## If you're stuck

`python scripts\handoff.py status` shows the whole board. Stay inside the
directories your brief lists. If a file under `contracts/` looks wrong, say so in
the group chat rather than editing it: three other agents build against it, and
CI will refuse the change anyway.
