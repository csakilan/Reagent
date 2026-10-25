# Analytics handoff — WF4 to runtime/API and dashboard

`runtime.db.analytics.get_metrics` returns the frozen `Metrics` object for
`GET /v0/metrics`. It needs no migration and never selects prompt text, output,
trace payloads or user tokens. Only connector-policy fields are read internally
to filter action names; enterprise configuration is never returned. The API owner wires
the function into its own route; WF4 has not edited the API or dashboard.

```python
from runtime.db.analytics import get_metrics, MetricsAccessError

# user_id must come from the server's authenticated/demo session principal.
# Use a psycopg connection with its default tuple row factory.
with connection.transaction():
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    response = get_metrics(
        connection,
        user_id=session_user.id,
        team=team_filter,
        agent=agent_filter,
        since=from_timestamp,
        until=to_timestamp,
    )
```

Keep database pools small (2 connections, 1 overflow). A caller-owned,
repeatable-read transaction keeps the report consistent across queries while
runs/approvals change. The function opens no extra connections and uses a fixed
number of queries rather than a query per run. It accepts timezone-aware Python
datetimes; invalid windows raise `ValueError` (map to HTTP 422).
`MetricsAccessError` covers unknown users and unavailable team/agent filters;
map it to a generic forbidden/not-found response without disclosing existence.

## Access and calculation rules

- Enterprise admins can inspect all runs, including private agents. Team
  members are restricted in SQL to their memberships; private enterprise-agent
  runs are excluded. A query parameter never overrides these restrictions.
- User-visible enterprise meta-agent/service runs contribute to the root team's
  activity. Their enterprise evaluation suites/configurations remain hidden.
- Top tools for members intersect enterprise and current owning-team connector
  grants, inherit omitted allowlists, and apply connector and guardrail denials
  from both layers. Counts of denials and policy
  violations still include the caller-visible run events.
- Date window is `[from, to)`, defaults to the last 30 days, and buckets by UTC
  calendar day. Empty days are omitted. The dashboard may fill those with zeros.
- Costs sum stored run rows using decimal arithmetic. This follows the frozen
  aggregate-accounting model; child-run costs must be stored once by runtime.
- Success rate uses only terminal runs. Failures include failed, denied and
  cancelled runs. Running/awaiting-approval runs still count as activity/cost.
- Latency percentiles use terminal runs with known latency, via nearest rank;
  empty samples return 0 to match the non-null integer schema.
- Human intervention rate is distinct runs with one or more approval requests
  divided by all visible runs, including pending and expired requests. Empty
  samples return null. Multiple approvals never multiply a run's count.
- `denials` counts denied tool-call events. `policy_violations` counts the
  corresponding event type. Top tools are capped at 20, sorted deterministically.
- Eval results use the newest non-null CI pass rate per visible agent/version
  inside the time window. Labels are `agent-name@version` to avoid collisions.
- `budget_usd` is the visible monthly team/agent cap (minimum if both apply),
  not a prorated date-window budget or permission decision. It is null when no
  applicable cap is visible or the selection spans multiple teams without a
  specific agent. A selected team agent uses its owning team's cap. Aggregate
  budget enforcement remains runtime's responsibility; hidden enterprise caps
  must not be inferred from this reporting field.

The implementation aggregates selected scalar rows in Python, keeping the SQL
portable and the role filtering testable. Memory scales with the selected run
window; a large production deployment should move aggregation into SQL.

## Verification

```text
python -m pytest runtime/db/test_analytics.py -q
python runtime/db/analytics.py --user alice --team platform-eng
python runtime/db/analytics.py --user frank
```

The first command runs calculations, JSON-schema validation, and actual SQL
selection/joins against an in-memory relational database; its adapter changes
only psycopg parameter markers. It does not establish live Tiger connectivity.
The latter commands open a read-only, repeatable-read PostgreSQL transaction
and validate the resulting report against the frozen OpenAPI schema. They print
only aggregated metrics, with sanitized failures. `--user` is for this local
operator diagnostic; never expose it as an HTTP identity override.
