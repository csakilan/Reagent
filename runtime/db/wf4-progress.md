# WF4 remaining execution — docs/agents/WF4-data.md

- H4 and H7 released previously; App installed on csakilan/reagent-demo, PR #1.
- Task 3: wrappers, strict registry names/ports, Origin validation, wrapper-only credentials, App token refresh, offline tests then live H3 checks.
- Task 4: existing seed verified offline; live seed verification when DB reachable.
- Task 5: analytics and optional Tiger indexes/search after core wrappers; never invent a live success.
- Ruling: work in the existing wf/data checkout as explicitly assigned; preserve all current uncommitted seed/bootstrap work. Git metadata is sandbox read-only.
- Pre-flight: frozen registry provides names/ports; SDK is absent and installation currently cannot reach package index. Fakes advertised by brief are absent in this checkout, so HTTP test doubles will exercise handlers without paid/live calls.
- Ruling: no public tunnel until a receiving webhook endpoint exists; frozen API has none. A public tunnel without a receiver cannot implement the demo trigger.

- Task 3 implementation: all 21 registered GitHub/Slack/memory actions, real MCP 2.3 SDK hosting, exact registry mapping/ports, strict Origin and loopback Host protection, internal runtime bearer authentication and trusted identity headers, App installation-token refresh, redacted results and memory writes.
- Ruling: discovery clients omitting SDK 2.3 clientCapabilities advertise an empty capability set; normalize only discovery, granting no capabilities. Frozen tests/contracts unchanged.
- Verification: complete suite with PYTHONPATH=.venv/connector-deps:.venv/lib/python3.12/site-packages python -m pytest -q: 60 passed, 35 skipped. SDK discovery and authenticated call tests execute in process, not a live H3 release.
- Final fresh review: three important findings (DM channel-scope bypass, structured-secret redaction, H3 probing a separate client instead of running server). Each has failing regression tests and fixes; green full suite afterward.
- Environment blockers: OS sandbox rejects bind(127.0.0.1:7102) with Operation not permitted; Tiger DNS unavailable; git add rejects .git/index.lock. Live H3 is 1 passed / 6 skipped and NOT RELEASED. No contract was changed and no fake ready status was written.
- Host command prepared: .venv/bin/python connectors/launch.py --install --release. Starts wrappers, makes an authenticated real PR read THROUGH running MCP, verifies fixture redaction, runs all seven live contract checks with zero skips before ready H3 --yes, and keeps wrappers alive.
- Required integration notes: runtime uses CONNECTOR_SHARED_SECRET HTTP bearer and root-run identity headers; demo repo scope must be updated by WF2 across all policy layers. Exact fake key AKIAIOSFODNN7EXAMPLE. See connectors/README.md.
- Task 4: seed already live from H4; existing 13 offline seed/bootstrap tests pass. Rechecking live DB is blocked by sandbox DNS.
- Task 5: Tiger hybrid search/fork/index enhancements deferred until core live H3 can be verified, per MVP-before-stretch rule.


## Analytics follow-up (2026-10-25)

- H3 was subsequently released by the user's normal-terminal launcher: real MCP PR read/redaction succeeded and all seven live checks passed, zero skips. Connector/bootstrap code was committed and pushed as dd4ef2f on wf/data.
- Added runtime/db/analytics.py and ANALYTICS.md for the frozen GET /v0/metrics response. Uses server-derived user_id, SQL membership/ownership filters, inherited connector action visibility, and no raw trace payloads.
- Ruling: use nearest-rank integer latency percentiles, UTC [from,to) windows, terminal-run success rates, distinct-run approval rates, latest non-null CI report per agent@version, and visible monthly team/agent budget caps. These choices fill undefined reporting details; enforcement remains runtime-owned. Costs if wrong are reporting-definition changes, not altered permissions.
- Ruling: aggregate scoped scalar rows in Python for this hackathon dataset; SQL still performs tenant filtering. Memory scales with the requested window; move aggregation into SQL for production scale.
- Verification: 17 analytics tests passed, exercising actual relational joins/filters and schema validation; full suite 77 passed, 35 skipped. Live Tiger check remains unavailable from sandbox (sanitized OperationalError), so no live PostgreSQL success claimed.
- Fresh read-only review identified missing inherited/guardrail action-denial filtering; regression tests failed first and now pass. Author also added regressions for cross-team version ownership and multi-membership budget selection.
- API/dashboard owners can wire get_metrics using ANALYTICS.md. No API/dashboard or frozen contracts were edited. Optional Tiger extensions/index/fork work remains deferred pending integrated demo verification.
