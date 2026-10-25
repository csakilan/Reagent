# Handoff files

Four people, four laptops: one macOS and three Windows.

| File | Goes to | Their first milestone |
|---|---|---|
| `YOU-data-and-coordination.md` | the MacBook | database up (**H4, 03:30**) and secrets sent (**H7, 03:45**) |
| `LAPTOP-resolver.md` | Windows 1 | the tighten-only merge (**H1, 05:00**) |
| `LAPTOP-runtime.md` | Windows 2 | an agent running from a resolved spec (**H2, 06:30**) |
| `LAPTOP-dashboard.md` | Windows 3 | the resolved view, then the trace viewer |

The MacBook takes data and integrations because the browser work (Tiger signup,
the GitHub App, the tunnel) is not separate from that role, it **is** that
role's dependency list, and the Tiger CLI and ngrok are least painful there.
It also keeps the secret distribution with the person who creates the secrets.

Resolver is the critical path and the least interruptible of the three, so it
should go to whoever can sit still for two hours.

Each file is self-contained: setup commands, the one prompt to paste into Claude
Code, the human jobs their agent cannot do, and what they must announce when
they finish something.

**Everyone needs Claude Code**, which requires a Pro or Max subscription. The
claude.ai web app cannot run an agent inside a repository. If someone only has
the web app, say so at the start rather than at 04:00.

## With three Windows laptops, Tiger Cloud is a dependency

The Docker fallback gives each person a local Postgres for their own unit work,
but nothing shared, so integration would have no common database. Tiger signup
is therefore worth ten minutes of real attention at the start rather than an
hour of improvisation at 04:00.

## The deeper material

It lives in the repo, and every agent loads it automatically.

- `CLAUDE.md` — the whole context, loaded on every session without being asked
- `docs/decisions/FROZEN.md` — 25 spec ambiguities, each with one answer
- `docs/research/` — external facts verified against live docs and real servers
- `docs/agents/WF*.md` — the detailed brief each agent follows
- `docs/BUILD_ORDER.md` — the schedule, the cut order, the demo corrections

## The two stops

Nobody has to remember these, because the tooling says them.

**Waiting on someone.** `python scripts/handoff.py wait H1` answers with one of
three things: ready, so use the real thing; pending, so here is the fake to build
against instead; or past deadline, so stop waiting and keep the fake for good.
Nobody polls and nobody sits idle.

**Testing two workflows together.** The MacBook runs
`python scripts/handoff.py lock integ/resolver+runtime --owner resolver`, which
freezes those two. They keep committing to their own branches, and CI gives them
a red check with the reason on it if they try to merge to `main`.
