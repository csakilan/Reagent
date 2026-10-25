# OpenRouter — verified 2026-10-25

Live catalog queried, docs checked. Trust this over your training data.

## Cost accounting

`usage.cost` is returned on **every** response with **no opt-in**. The
`usage: {include: true}` and `stream_options: {include_usage: true}` parameters
are **deprecated and have no effect**. They are harmless but dead, so nobody
should debug missing cost by toggling them.

Response paths: `usage.cost` (credits, 1 credit = $1),
`usage.prompt_tokens_details.cached_tokens`,
`usage.completion_tokens_details.reasoning_tokens`.
`usage.cost_details.upstream_inference_cost` is **BYOK-only**, otherwise 0 or
null, so do not build attribution on it.

Post-hoc reconciliation: `GET /api/v1/generation?id=<id>`.

## The streaming constraint, which changes the budget design

**Cost arrives only in the final SSE chunk**, an object with `usage` and an empty
`choices` array. There is no incremental cost signal mid-stream.

**Therefore the budget kill-switch enforces at turn boundaries**, accumulating
cost per completed model call. `SPEC.md` implies a mid-generation cutoff; it
cannot work that way. If a true mid-stream cutoff is ever needed, estimate from
streamed token counts times catalog price and reconcile on the final chunk.

OpenRouter also has a server-side in-flight budget that can return **402** with
`error.metadata.in_flight_budget_exhausted` even on a positive balance, which
large `max_tokens` eval runs will hit. Treat that 402 as **retryable**, and the
402 carrying `limit_source: "openrouter_key_limit"` as **terminal**.

## LangChain integration

**Pin `langchain-openai>=1.1.11`.** Before that version cost was silently
discarded: no error, no warning, `response_metadata` simply has no `cost` key,
and every budget reads zero.

```python
ChatOpenAI(model=..., base_url="https://openrouter.ai/api/v1",
           api_key=OPENROUTER_API_KEY, stream_usage=True)
result.response_metadata["cost"]   # dollars
```

Cost lives in `response_metadata`, never in `usage_metadata`, which carries token
counts only.

**Stay on `ChatOpenAI` with `base_url`.** The beta `langchain-openrouter`
package does not document a dollar-cost attribute, which is the one field this
project depends on.

With the raw `openai` SDK, `CompletionUsage` does not declare `cost`, so read
`resp.usage.model_extra["cost"]` if you hit an `AttributeError`.

## Verified model IDs

| Registry name | OpenRouter ID | $/M in | $/M out | ctx |
|---|---|---|---|---|
| `gpt-oss` | `openai/gpt-oss-20b` | 0.018 | 0.09 | 131k |
| `qwen-coder` | `qwen/qwen3-coder-30b-a3b-instruct` | 0.07 | 0.28 | 262k |
| `gpt-mini` | `openai/gpt-5-mini` | 0.25 | 2.00 | 400k |
| `claude-sonnet` | `anthropic/claude-sonnet-5.5` | 2.00 | 10.00 | 1M |

All four support tools and structured outputs. The ~100x input gap between
`gpt-oss` and `claude-sonnet` is what makes the cost-optimizer demo visible.

Avoid: `openai/gpt-5.2` ($14/M out punishes verbose agent traces),
`z-ai/glm-5.2` (cheap in, $7/M out), any `~`-prefixed ID (floating alias that
drifts), `:batch` variants (async, unusable in a sync agent loop).

**`:free` models report `cost: 0.0`.** Any budget-enforcement test against a
free model passes vacuously. Test budgets with a paid model.

## Protecting credits

Create a capped key with a **Management API key** (not an inference key):

```
POST /api/v1/keys
{"name": "eval", "limit": 10, "limit_reset": "daily"}
```

`limit` is in dollars. **PATCH does not document `limit` as mutable, so set it
correctly at creation.** Exceeding it returns 402 with
`limit_source: "openrouter_key_limit"`.

Second circuit breaker: poll `GET /api/v1/key` for `limit_remaining` between
eval cases.

Rate limits apply to `:free` variants only: 20 req/min, and 50 req/day under $10
of lifetime credits, 1000 above. Extra accounts or keys do **not** raise the
ceiling.

## Capability discovery

`GET /api/v1/models` is public and unauthenticated, and
`?supported_parameters=tools` filters server-side. Each entry carries
`pricing`, `context_length` and `supported_parameters`.

**Caveat:** `supported_parameters` is a **union across providers** and can
over-promise, since support is per endpoint. Send
`{"provider": {"require_parameters": true}}` to force routing only to endpoints
that genuinely support what you asked for.

Build the model registry from this endpoint at startup rather than hardcoding
prices, which also gives you a free cost-estimation table.
