# WF4 — Data and integrations

**Branch:** `wf/data` · **Owner:** D · **MVP rows:** 7, 9, 10

You are first and you are the unblocker. Three workflows are waiting on your
database, and one is waiting on your GitHub wrapper. You also own the two
Tiger Data features that win the second track.

## Read, in this order

1. `CLAUDE.md` (already loaded)
2. `docs/research/tigerdata.md` — **do this first, it decides your whole setup**
3. `docs/research/mcp.md` — **the SDK renamed its main class; your training data is wrong**
4. `contracts/db/001_init.sql` — already written and tested. Apply it, do not rewrite it.
5. `contracts/connector_actions.json` — the exact action and wire names
6. `spec/SPEC.md` §3, §9, §13

## You own

```
contracts/db/          migrations ONLY (002_ onward; 001 is frozen)
connectors/github/     MCP wrapper
connectors/slack/      MCP wrapper
connectors/memory/     built-in memory wrapper
runtime/db/            connection, repositories, analytics queries
fixtures/seed/         the seed script and demo data
```

**Do not touch** `runtime/engine/`, `runtime/resolver/`, `dashboard/`, or
anything in `contracts/` except adding a new numbered migration.

## Task 1 is the most urgent thing on this team · due 03:30

**MLH gives $1,000 in Tiger credits for 30 days** via
<https://mlh.link/tigerdata-quickstart>. **Create a STANDARD service, not a free
one.** A free service caps `max_connections` at **25 with no pooler**, which the
research flags as the most likely on-stage failure, and goes **read-only** at
~750 MB. Credits remove both.

```
curl -fsSL https://cli.tigerdata.com | sh
tiger auth login
tiger service create --name reagent --region us-east-1
tiger db uri
```

**Email confirmation is mandatory**, so use a personal address, not a university
one that might filter it. **The password is shown once**, in the config file
downloaded at creation. Put it in `.env` immediately.

Then:

```sql
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;   -- brings pgvector
CREATE EXTENSION IF NOT EXISTS pg_textsearch;         -- needs PG 17+
```

Verify with `SELECT extname, extversion FROM pg_extension` rather than trusting
a docs page. Apply the migration:

```
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f contracts/db/001_init.sql
```

It applies clean; it was tested against PostgreSQL 17.11 with pgvector 0.8.7 and
TimescaleDB 2.31.0, 18 tables and 2 hypertables, with six negative constraint
tests. **If it errors, that is a real finding: report it, do not patch 001.**

Three things about it to know: `runs` and `run_events` are hypertables with
**composite primary keys**, so **nothing can foreign-key to `runs(id)`** and the
child tables deliberately carry no FK. `agent_versions.embedding` is a plain
column you fill from the app, not generated. And set small pools everywhere
(`pool_size=2, max_overflow=1, pool_pre_ping=True`) regardless of the pooler.

**RELEASE: H4** — all four workflows are waiting on this.
```
pytest tests/contract/test_db_schema.py -q
python scripts/handoff.py ready H4
```

## Task 2 — GitHub App and the planted-secret repo · due 04:30

Someone is already on this in a browser. It needs a human: app creation, OAuth,
and a public URL for webhooks.

- A test org and repo, with a PR containing a planted AWS key. **Use a
  syntactically valid but revoked or fake key**, and add it to the redaction
  exact-match list so a regex miss cannot put it on camera.
- A GitHub App with the narrowest scopes that cover `pulls.read`,
  `pulls.comment`, `contents.read`.
- A tunnel (ngrok or Cloudflare) for the webhook.
- Put every value in `.env` against the names in `.env.example`.

## Task 3 — MCP wrappers · due 07:00

**Your training data is wrong about this SDK.** In `mcp` v2, `FastMCP` was
renamed **`MCPServer`** and `mcp.server.fastmcp` raises `ModuleNotFoundError`.
Pin `mcp>=2.3.0,<3`. Read `docs/research/mcp.md` first.

**Tool names cannot contain dots.** `pulls.comment` is illegal; the wire name is
`pulls_comment`. **Read every name from `contracts/connector_actions.json`** and
do not recompute the transform. 18 of our 30 actions differ.

Ports come from the registry YAML and nowhere else: **memory 7100, slack 7101,
github 7102**, platform 8000. Expose **only** the actions listed in the
connector file, never the whole API. Credentials are injected at the wrapper and
never reach the model. Validate `Origin` and return 403 on a bad one.

**RELEASE: H3** by 07:00. If you miss it, `wf/runtime` stays on fakes
permanently and demo step 6 uses the video. That decision is already made; do
not renegotiate it at 07:00.

## Task 4 — Seed data · due 05:30

Realistic enough that the marketplace does not look empty: the enterprise row,
3 teams, 4 connectors, 4 models, 6+ agents with versions, and seeded run history
spread over time so the analytics page has a shape.

Meta-agents and `secret-scanner` seed as `status: 'published'` with
`origin: 'platform'` (FROZEN D8). They do **not** go through the publish
pipeline, which is what stops the pipeline from needing itself.

## Task 5 — Track points, if time allows · due 09:00

These are the two Tiger-only features worth real points, both free-tier
available and both cheap:

1. **Hybrid search**: `pg_textsearch` BM25 plus `pgvectorscale` diskann, fused
   with Reciprocal Rank Fusion in one SQL query. Tiger publishes a copy-paste
   tutorial, so roughly an hour, and it maps directly onto `SPEC.md`'s
   outcome-ranked agent search. No other Postgres host has these two extensions.
2. **Zero-copy fork as an agent sandbox**: `tiger service fork`, 30 to 90
   seconds. Let an agent fork the database, break it, and throw it away. One
   command, very strong narrative. Rehearse it; it is not instant.

Also swap the `USING hnsw` indexes in 001 for `USING diskann` in a `002_`
migration. One line, Tiger-only index, worth a sentence in the pitch.

## Checkpoints

| Time | Expected |
|---|---|
| 03:30 | **H4 released.** All four workflows are blocked until you do. |
| 04:30 | GitHub app, test repo with planted key, tunnel |
| 05:30 | seed script loads |
| 07:00 | **H3 released** or the fallback taken |
| 09:00 | analytics queries, and track features if time allows |

## Verify

```
psql "$DATABASE_URL" -c "select count(*) from information_schema.tables where table_schema='public'"
pytest tests/contract/test_db_schema.py tests/contract/test_connector_actions.py -q
python fixtures/seed/seed.py --check
```
