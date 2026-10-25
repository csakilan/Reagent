# You: data and integrations, plus coordination

You have the MacBook, so you take the role whose dependencies are all browser
work. Tiger signup, the GitHub App and the tunnel are not separate from this
workflow, they **are** this workflow's dependency list, and the Tiger CLI and
ngrok are least painful on macOS.

**You are first and three people are blocked on you twice:** once for the
database (H4, 03:30) and once for the secrets (H7, 03:45).

---

## 1. Hand out three files, right now · 2 minutes

| Send | To | Their first job |
|---|---|---|
| `LAPTOP-resolver.md` | the one happiest writing careful logic with tests | the tighten-only merge, **H1 by 05:00** |
| `LAPTOP-runtime.md` | the one who has used LangChain, or will read docs fast | run an agent from a resolved spec, **H2 by 06:30** |
| `LAPTOP-dashboard.md` | the one who can run a Next.js app and has an eye for UI | the resolved view |

All three need **Claude Code**, which needs a Pro or Max subscription. The
claude.ai web app cannot run an agent inside a repo. If one of them only has the
web app, say so now rather than at 04:00, and we restructure.

Resolver is the critical path and the least interruptible, so give it to whoever
can sit still for two hours.

---

## 2. Tiger Cloud · do this before anything else

**With three Windows laptops, this is a dependency and not a preference.** The
Docker fallback gives each person a local Postgres for their own unit work, but
nothing shared, so integration would have no common database. Ten minutes of
your attention now is worth an hour later.

Use the MLH link, which gives **$1,000 of credits for 30 days**:
<https://mlh.link/tigerdata-quickstart>

- **Use a personal Gmail.** Email confirmation is mandatory, and a university or
  corporate filter will stop you cold.
- Create a **standard** service, not a free one. The credits pay for it. A free
  service caps connections at 25 with no pooler, which the research named as the
  most likely on-stage failure, and goes read-only at about 750 MB.
- **The password is shown once**, in a config file it offers to download. Save it
  immediately; there is no way to see it again.
- The connection string must end in `?sslmode=require`.

Or from the terminal, which is faster:
```
curl -fsSL https://cli.tigerdata.com | sh
tiger auth login
tiger service create --name reagent --region us-east-1
tiger db uri
```

Then:
```sql
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;   -- brings pgvector with it
CREATE EXTENSION IF NOT EXISTS pg_textsearch;         -- needs PG 17+
```
Check with `SELECT extname, extversion FROM pg_extension` rather than trusting a
docs page; the published version numbers lag the changelog.

## 3. OpenRouter key, with a hard cap

Eval runs are 5 runs per case across many cases, so an uncapped key can drain
real money while nobody is watching.

1. Sign up at openrouter.ai and add a little credit.
2. Create a **Management** key in the dashboard. It is a different thing from an
   inference key and cannot itself make model calls.
3. Create the inference key **with the limit already set**, because the limit may
   not be editable afterwards:

```
curl https://openrouter.ai/api/v1/keys \
  -H "Authorization: Bearer <MANAGEMENT_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"name":"reagent","limit":15,"limit_reset":"daily"}'
```

## 4. Send the secrets · H7, due 03:45

**The step everyone forgets, and three agents are waiting on it.**

Fill in `.env` from `.env.example` and send the completed file to all three
laptops privately. Signal, a DM, AirDrop. Never a commit, never the shared repo,
never a public channel. Have each of them run `python scripts/doctor.py` and
confirm the `.env` check is green.

Then:
```
python scripts/handoff.py ready H7 --yes
```

If you are running late, `DATABASE_URL` and `OPENROUTER_API_KEY` alone unblock
two of the three. The GitHub values can follow, since only the runtime needs
them and only once H3 lands.

## 5. GitHub App and the demo repo · by 04:30

- A test org, and a repo called `infra`.
- A pull request containing a planted AWS key. **Use a fake or already-revoked
  key.** Then tell the runtime person the exact string, because it goes into the
  redaction exact-match list alongside the regex patterns. A regex miss that puts
  a key on screen during judging loses the round, so it belongs in two
  independent layers.
- A GitHub App with the narrowest scopes covering read a PR, comment on a PR, and
  read contents. Install it on the test repo. Save the app id, bot token and
  webhook secret.
- `ngrok http 8000`, and save the public URL.

Slack is **third in the cut order**. GitHub alone carries the demo. Do not spend
04:00 on Slack OAuth.

---

## 6. Your own branch and agent

Do this between browser jobs; the agent works while you click.

```
git clone https://github.com/hanhg/Reagent.git
cd Reagent
python scripts/doctor.py --fix
git checkout -b wf/data origin/wf/data
claude
```

Paste this:

> You are the WF4 data agent on branch `wf/data` of this repo. Read
> `docs/agents/WF4-data.md` and follow it exactly. Read
> `docs/research/tigerdata.md` before touching the database, and
> `docs/research/mcp.md` before writing any MCP wrapper, because the MCP SDK
> renamed its main class and your training data is wrong about it.
> `contracts/db/001_init.sql` is already written and tested against real
> Postgres, so apply it rather than rewriting it; if it errors, tell me instead
> of patching it. I am handling the Tiger signup and the GitHub App myself, so
> skip those and ask me for values when you need them. Start with applying the
> migration and the seed script. Three workflows are blocked on H4 until it is
> released. Run `python scripts/handoff.py status` first.

## 7. What you announce

```
pytest tests/contract/test_db_schema.py -q
python scripts/handoff.py ready H4          # due 03:30 — all three are waiting

# after you have sent .env to everyone
python scripts/handoff.py ready H7          # due 03:45

pytest tests/contract/test_connector_actions.py -q
python scripts/handoff.py ready H3          # due 07:00 — runtime is waiting
```

Each prints who it just unblocked. Paste that into the group chat.

**If H3 slips past 07:00, stop.** The runtime keeps the fake connectors for good
and the live GitHub step is demoed from the video. Already decided; do not try to
rescue it at 07:30.

## 8. Watch the board, not the chat

```
python scripts/handoff.py status
```

Every twenty minutes or so. Two things it catches: a handoff that is ready while
the waiting agent has not been told, and a handoff that is overdue while the
waiting agent is still waiting. In the second case tell them to re-run
`python scripts/handoff.py wait <ID>`, which will instruct them to take the
fallback permanently.

## 9. Integration tests · you run these, not the agents

```
python scripts/handoff.py lock integ/resolver+runtime --owner resolver
git checkout -b integ/resolver+runtime origin/main
git merge origin/wf/resolver origin/wf/runtime
git push -u origin integ/resolver+runtime
```

CI runs the seam tests and prints a status table. The two frozen workflows keep
committing to their own branches but get a red check, with the reason, if they
try to merge to `main`. When you are done:

```
python scripts/handoff.py unlock integ/resolver+runtime
```

Lock `integ/all` from 08:00 and merge everything.

---

## 10. Your schedule

| Time | You |
|---|---|
| now | send the three files, start Tiger signup |
| 03:15 | OpenRouter capped key |
| **03:30** | **H4** migration applied and seeded |
| **03:45** | **H7** `.env` sent to all three laptops |
| 04:30 | GitHub App, planted-key PR, tunnel |
| 05:00 | tell the runtime person the exact planted secret string |
| 06:00 | first integration test, `integ/resolver+runtime` |
| 07:00 | **H3** live GitHub wrapper, or take the fallback |
| 08:00 | lock `integ/all`, merge everything |
| 09:00 | **demo rehearsal, three full runs.** Not optional. |
| 10:00 | record the pitch video |
| **10:30** | **code freeze** |
| 11:30 | submitted |

## 11. Two pitch corrections before 10:00

A judge may know these. Detail in `docs/research/competition.md`.

**Docker's `cagent` is now `docker/docker-agent`, and it has eval tooling.**
`docker agent eval` exists with tool-trajectory and LLM-judge scoring. Saying
"no evals" to a judge who uses it is the most likely credibility hit in the deck.

**Microsoft shipped an Agent Governance Toolkit** in April 2026, MIT, with a real
policy engine, so do not claim open-source agent governance does not exist.

Lead with the two things nobody has: **a three-layer tighten-only merge resolved
on every run, and a publish pipeline where generated tests must be
human-reviewed before they are allowed to run.**

## 12. If something is on fire

| Symptom | Do this |
|---|---|
| Tiger signup stuck on email confirmation | Try a different personal address. There is no shared fallback with three Windows laptops, so this is worth escalating, not working around. |
| An agent says a contract is wrong | It is probably right. Read the reasoning, fix `contracts/` on `main` yourself, tell the other three. |
| A Windows laptop shows the whole tree modified | `git config --global core.autocrlf input`, then re-clone. |
| `too many clients already` | Everyone closes their DB GUI. Check nobody made a *free* Tiger service by mistake. |
| An agent is editing someone else's directories | Stop it. Its brief lists what it owns. |
| Nothing works and it is 09:30 | Stop building. Rehearse what does work. A rough demo beats a broken one. |
