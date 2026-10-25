# ReAgent

Build enterprise AI agents by extending, not rewriting.

An agent is a declarative YAML file with a `.agent` extension. It holds no code
and no secrets, so it is safe to read, diff and share. What an agent *does*
lives in its file; what it is *allowed* to do lives in two layers above it, an
**enterprise** layer and a **team** layer. On every run the runtime re-reads all
three from the database and merges them, and a lower layer may only ever
**tighten** what the layers above allow. Allowlists intersect, denylists union,
the lowest numeric limit wins, safety flags cannot be switched off, and prompts
append in order. A file that tries to widen a rule is rejected with an error
naming the field and the layer that blocked it.

Self-hosted. Any Postgres, any model provider.

---

## Setup

Requires Python 3.11+ and, for the dashboard, Node 20+.

```bash
git clone https://github.com/hanhg/Reagent.git
cd Reagent
cp .env.example .env          # fill in DATABASE_URL and OPENROUTER_API_KEY
python scripts/envs.py
```

### Why that creates two virtualenvs

This is deliberate and it cannot be collapsed into one. The runtime is an MCP
**client** and the connectors are MCP **servers**, and they need incompatible
versions of the `mcp` package:

| | needs | because |
|---|---|---|
| `runtime/` | `mcp` 1.x | `langchain-mcp-adapters==0.3.2` pins `mcp<2.0.0` |
| `connectors/` | `mcp` 2.x | uses `from mcp.server import MCPServer`, which is the v2 API |

`pip install -r` of both files fails with `ResolutionImpossible`, and no pin
fixes it: `langchain-mcp-adapters` 0.3.1 drops the upper bound but then fails to
import against `mcp` 2.x. They are separate processes that talk over HTTP, and
the v2 server serves both protocol eras routed by the `MCP-Protocol-Version`
header, so the split is harmless. `tests/contract/test_two_environments.py`
guards it, and `python scripts/envs.py --verify` checks it.

```
.venv           connectors.  mcp 2.x, no langchain.    Runs the wrappers.
.venv-runtime   runtime.     mcp 1.x + langchain.      Runs the API, CLI, tests.
```

## Running it

Apply the schema, then start the wrappers and the runtime from their own
environments. On Windows substitute `.venv\Scripts\python.exe`.

```bash
# 1. schema (once)
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f contracts/db/001_init.sql

# 2. the three MCP wrappers, from the connectors environment
python scripts/wrappers.py                     # 7100 memory, 7101 slack, 7102 github

# 3. the runtime, from the runtime environment, in another terminal
.venv-runtime/bin/python -m uvicorn runtime.api.runs:app --loop asyncio:SelectorEventLoop --port 8000

# 4. the dashboard
cd dashboard && npm ci && npm run dev          # 3000
```

## Tests

```bash
.venv-runtime/bin/python -m pytest tests -q
```

270 passing. The connector contract tests need the wrappers running
(`python scripts/wrappers.py`); they skip cleanly rather than fail when a
wrapper is down, so a skipped seam means "not up yet" and a failed one means
"both sides exist and disagree".

| Suite | Covers |
|---|---|
| `tests/unit/test_merge_rules.py` | 62 tests, one per row of the tighten-only merge table |
| `tests/unit/test_runtime_guardrails.py` | redaction, permission denial, budget, steps, timeout |
| `tests/unit/test_evals.py` | code checks, judge, Wilson intervals |
| `tests/unit/test_publish.py` | the seven-stage pipeline and its human gate |
| `tests/contract/` | the seams between the four workstreams |

## Layout

```
contracts/      frozen shapes shared across the codebase: resolved spec, DDL,
                OpenAPI, run events, connector action names, error codes
runtime/        resolver, engine, guardrails, evals, publish pipeline, db
connectors/     MCP wrappers for github, slack and memory
dashboard/      Next.js app
cli/            reagent validate | run | eval | publish | fork
spec/           SPEC.md, JSON Schema, 19 example documents, validator
docs/research/  external facts verified against live docs and real servers
docs/decisions/ FROZEN.md: spec ambiguities, each with one answer
```

`spec/SPEC.md` is the full specification. `docs/decisions/FROZEN.md` overrides
it where they conflict, and says why in each case.

## Not in this version

Data classification and model routing by sensitivity, signed `.agent` files,
SSO, Postgres row-level security, sandboxed execution, and self-hosted
inference. `SPEC.md` §16 has the reasoning.
