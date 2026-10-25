# Demo database bootstrap

From the repository root with the project Python environment active:

```text
python runtime/db/bootstrap.py
```

The command reads the root `.env`, applies `contracts/db/001_init.sql` unchanged
on a fresh database, inserts demo fixtures in the same transaction, runs all
seven live H4 contract tests, and releases H4 through the handoff tool. It stops
on any error and refuses to release when tests skip. A partial existing schema
is reported rather than patched. No OpenRouter credits are spent.

The seed includes three teams, nine users, four connectors, four models, nine
agents with versions, declared memory bucket metadata, and thirty explicitly
synthetic historical runs. Platform agents start published with platform origin
as required by FROZEN D8; team agents remain drafts for the human review pipeline.
History is attributed to caller teams and contains no raw inputs or outputs.

Re-running preserves existing policies, registry entries and version specs.
Existing team memberships and version lineage are preserved as well. Reusing
a bucket name with a different type, owner or ACL stops the transaction.
The seed serializes concurrent writers with a transaction advisory lock and
uses a marker on historical run events to avoid duplicating activity.

The current frozen H4 contract expects every enterprise agent's current version
to be a published platform version. A database already containing a user-origin
enterprise agent may fail that contract; this bootstrap reports the failure
instead of changing the existing agent or weakening the gate.

Other commands:

```text
python fixtures/seed/seed.py --plan
python fixtures/seed/seed.py --check
python -m pytest fixtures/seed/ -q
```

`--plan` prints fixture counts without a database connection; `--check` verifies
live seed data in a read-only transaction. Offline tests do not establish H4
readiness. All database operations require network access to Tiger Cloud.
