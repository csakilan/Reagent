# You're on: the dashboard

You build the only part a judge looks at directly. You are also the one person
who is **never blocked**, because every screen has test data to build against
from the first minute.

## Setup · 5 minutes · Windows

Open **PowerShell** and run these first. They prevent two problems that each cost
an hour to diagnose at 4 AM, and the second one matters most to you because
`node_modules` paths are long:

```
git config --global core.autocrlf input
git config --global core.longpaths true
```

Then:

```
git clone https://github.com/hanhg/Reagent.git
cd Reagent
python scripts\doctor.py --fix
git checkout -b wf/dashboard origin/wf/dashboard
```

If `python` is not found, try `py -3` instead. You need **Node 20 or newer**, and
`doctor.py` will tell you if not. Fix anything marked **BAD**; warnings are fine.
You do **not** need Docker and you do **not** need the database.

Copy `.env.example` to `.env`. You can ignore nearly all of it; you only need
`NEXT_PUBLIC_API_URL`.

## Then start your agent

```
claude
```

Paste this in:

> You are the WF3 dashboard agent on branch `wf/dashboard` of this repo. Read
> `docs/agents/WF3-dashboard.md` and follow it exactly. Section 10 of `CLAUDE.md`
> is a binding design specification, not a suggestion: colour encodes provenance
> and verdict and nothing else, and the banned list is absolute. Generate your
> typed API client from `contracts/openapi.yaml` rather than hand-writing fetch
> calls. You are never blocked: build every screen against the files in
> `fixtures/`, and swap to live calls as `python scripts/handoff.py status` shows
> each handoff turning ready. Build screens 1 through 4 before anything else,
> starting with the resolved view, which is the signature screen and demo step 3.

## Your own jobs, away from the agent

None at all. Start now and do not wait for anybody.

## What you must announce

Nothing. You are the last consumer in the chain, so no other workflow waits on
you. What you should do instead is **check the board every half hour** and swap
fixtures for live calls as handoffs turn ready:

```
python scripts\handoff.py status
```

## The design is already decided

This has to read as an internal enterprise tool rather than a generated template,
so the palette and the type are already chosen, and Tailwind's default palette
has been deleted from the config so it cannot be reached for by accident. Your
agent has the full list, but here it is so you can hold it to account.

**Banned.** Gradients, drop shadows, glassmorphism, radial orbs, dot grids,
purple and black, neon, pastels, pure white backgrounds, a coloured stripe down
the left edge of cards, three-feature-card rows, bento grids, fake terminal
windows, checkmark bullet lists, Inter, Geist, Space Grotesk, Lucide icons,
emoji, hover animation on everything, animated arrows, invented testimonials.

**Required.** Skeleton loaders on every screen that fetches, a `/privacy` page
stating what the platform logs and the 30-day trace retention, and a real LICENSE
and SECURITY.md.

**The single idea.** If something is coloured, it is telling you which policy
layer a value came from, or whether an action was permitted. There is no
decorative colour anywhere in the app. That one rule is what stops this looking
generated.

Your signature screen is the resolved view, which is `git blame` for policy: the
merged configuration in monospace, with a narrow gutter showing which layer each
value came from. Tables over cards throughout, because this data is dense and
tabular and deserves to be treated that way.

## One thing that will bite you

**Next.js dev mode leaks database connections on hot reload.** Use a single
module-scope client, never one per request. It is the fastest way to exhaust the
connection pool and take the whole team's database down at 06:00.

## If you're stuck

`python scripts\handoff.py status`. Stay inside `dashboard/`. If an API response
looks wrong, say so in the group chat rather than reaching into `runtime/` to fix
it, and keep building against the fixture meanwhile.
