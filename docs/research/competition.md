# Competitive landscape — verified 2026-10-25

The feasibility report's competitive table has **three errors a judge may catch**.
Fix the pitch before Sunday morning.

## Corrections to make

**1. "Docker cagent" is the wrong name, and "no evals" is false.**
`docker/cagent` redirects to **`docker/docker-agent`**. It ships in Docker
Desktop 4.63+ as Docker Agent, has ~4.6k stars, and **`docker agent eval`
exists**: it reads an `evals/` directory, replays questions, compares expected
tool calls, and reports tool-trajectory and LLM-judge scores with
`--judge-model` and `--repeat`. Saying "no evals" to a judge who uses it is the
most likely credibility hit in the deck. What still holds: no layering, no
policy engine, no observability. Also drop "secrets in .env files"; keys are
plain env vars and there is no documented .env secrets feature.

**2. Microsoft Agent Governance Toolkit is missing from the table.**
Shipped 2026-04-02, MIT. A stateless policy engine intercepting every agent
action pre-execution, with three policy languages (YAML, Cedar, OPA/Rego) and
SDKs in five languages. This is the strongest open-source governance incumbent
and the single most dangerous thing for a judge to raise. It has no marketplace,
no registry and no evals, but **"open-source agent governance doesn't exist" is
not a claim we can make.**

**3. Agent Format's governance is stronger than the report implies.**
It explicitly specifies strictest-wins composition across an org layer and an
agent layer, and first-class per-tool approval gates. "No public runtime" is
accurate: the repo is schema-only, 5 commits, 4 files, with a hosted playground.
"No team layer, no marketplace, no evals" is accurate. **"No governance merge
semantics" is not** — do not say it. Licensing is Apache-2.0 for code and
CC-BY-4.0 for docs. Traction is ~22 stars, which cuts both ways in a pitch.

## Two competitors the report does not mention at all

**Obot** (`obot-platform/obot`, MIT, ~1.1k stars). MCP Gateway as a single
governed entry point, virtual MCP servers, per-user and per-group access
control, an LLM gateway, hosted MCP servers in containers, **MCP and Skills
registries exposed through the standard MCP Registry API**, IdP login with SCIM
and roles, and **correlated audit logs across the platform**. Self-hosted.
**This owns connector-registry and observability.** Neither is a differentiator
any more. What it lacks: layered org/team/agent policy with tighten-only merge
(its policy is flat per-user/per-group), a forkable marketplace, and evals.

**Open Managed Agents** (`rogeriochaves/open-managed-agents`, AGPL-3.0, ~119
stars). Already has an **Organization → Team → Project hierarchy** with RBAC at
org and team level, per-team provider access and **per-team MCP policies**, 12
built-in connectors with a browse endpoint, and a debug view with per-event
token usage and tool calls. **This is the closest competitor on layering.** What
it lacks: an agent layer, tighten-only merge semantics, versioning and publish,
a marketplace, and eval-gated publish.

**Docker Sandbox Kit Spec** (announced 2026-09-24, Apache-2.0, heading to CNCF).
Packages an agent as an OCI image bundling a typed list of requested resources,
so pinning the image pins the permissions and reviewers diff the request list
when a version asks for more. Declared-permissions-as-reviewable-artifact is now
a vendor-neutral standard. Cite it as validation rather than being told about it.

## What is actually defensible

Nothing shipped in the last six months combines all five of layered
org/team/agent governance with tighten-only merge, a connector registry, a
forkable marketplace, CI-style evals gating publish, and observability. The
*combination* is unoccupied.

But every individual piece now has a credible open-source incumbent, so lead
with the two nobody has:

> **A three-layer tighten-only merge resolved on every run, and a publish
> pipeline where generated eval tests must be human-reviewed before they run and
> nothing ships below threshold.**

Registry and observability are table stakes. Do not spend pitch time on them.

## Standards, stated accurately

**AGENTS.md** is stewarded by the Linux Foundation's Agentic AI Foundation since
Dec 2025, with native support across 20+ tools. Claude Code reads it when no
CLAUDE.md is present. The ~60k-repo figure is project-reported, and one
independent survey found far thinner penetration, so say "the de-facto
repo-level convention, Linux Foundation-stewarded" and leave the number out.
Do not quote the 35-55% bug-reduction figure; its own author flagged it as
unreliable.

**A2A** went to the Linux Foundation in June 2025 and **v1.0 released March
2026**, with 150+ supporting orgs, multi-protocol bindings, and
cryptographically signed Agent Cards (JWS). The genuinely useful point for us:
**A2A defines no authorization framework and no central Agent Card registry.**
That is an open hole adjacent to what we are building.

**LangSmith / LangGraph Platform.** LangGraph Platform was folded into LangSmith
Deployment. Self-hosted and hybrid are **Enterprise-only, custom-priced**, so
"no free self-hosted tier" holds. Phrase it as "no free self-hosted
*platform or observability* tier", because the open-source LangGraph *library*
does run on your own infra for free and someone will say so.
