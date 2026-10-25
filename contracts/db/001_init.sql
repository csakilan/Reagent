-- ===========================================================================
-- ReAgent v0 schema. FROZEN CONTRACT.
--
-- Every pattern here was executed against PostgreSQL 17.11 + pgvector 0.8.7 +
-- TimescaleDB 2.31.0 before being written. Three parts of the paper model in
-- SPEC.md §13 do not compile; the corrections are marked CORRECTION below with
-- the exact error they produce.
--
-- Owner: wf/data. Everyone else reads it and does not edit it.
-- Apply with:  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f contracts/db/001_init.sql
-- ===========================================================================

-- Order does not matter between these two; verified in both directions.
-- timescaledb needs shared_preload_libraries + a server restart, which is why
-- the Compose image must be a Timescale image with vector added, never the
-- pgvector image with timescale added.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Layers and identity
-- ---------------------------------------------------------------------------

CREATE TABLE enterprise (
    id          bigserial PRIMARY KEY,
    name        text        NOT NULL UNIQUE,
    spec        jsonb       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
-- SPEC.md §4: exactly one row. Enforced, because a second row would silently
-- change which policy every agent resolves against.
CREATE UNIQUE INDEX enterprise_singleton ON enterprise ((true));

CREATE TABLE teams (
    id          bigserial PRIMARY KEY,
    name        text        NOT NULL UNIQUE,
    spec        jsonb       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id                  bigserial PRIMARY KEY,
    name                text    NOT NULL UNIQUE,
    email               text    NOT NULL UNIQUE,
    is_enterprise_admin boolean NOT NULL DEFAULT false
);

CREATE TABLE team_members (
    team_id     bigint  NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id     bigint  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_approver boolean NOT NULL DEFAULT false,
    PRIMARY KEY (team_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Registry
-- ---------------------------------------------------------------------------

CREATE TABLE registry_items (
    id         bigserial PRIMARY KEY,
    kind       text        NOT NULL CHECK (kind IN ('connector', 'model')),
    name       text        NOT NULL,
    version    text        NOT NULL,
    spec       jsonb       NOT NULL,
    status     text        NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'deprecated')),
    created_by bigint      NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, name, version)
);

CREATE TABLE connector_requests (
    id           bigserial PRIMARY KEY,
    team_id      bigint      NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    requested_by bigint      NOT NULL REFERENCES users(id),
    description  text        NOT NULL,
    status       text        NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'fulfilled', 'rejected')),
    connector_id bigint      NULL REFERENCES registry_items(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Agents and versions
-- ---------------------------------------------------------------------------

CREATE TABLE agents (
    id                 bigserial PRIMARY KEY,
    name               text        NOT NULL UNIQUE,
    level              text        NOT NULL CHECK (level IN ('team', 'enterprise')),
    team_id            bigint      NULL REFERENCES teams(id) ON DELETE CASCADE,
    access             text        NULL
                         CHECK (access IN ('private', 'run_only', 'service')),
    current_version_id bigint      NULL,   -- FK added after agent_versions exists
    created_at         timestamptz NOT NULL DEFAULT now(),
    -- SPEC.md §6: team is required for level: team, meaningless for enterprise.
    CONSTRAINT team_level_requires_team CHECK (
        (level = 'team'       AND team_id IS NOT NULL AND access IS NULL) OR
        (level = 'enterprise' AND team_id IS NULL     AND access IS NOT NULL)
    )
);

CREATE TABLE agent_versions (
    id                     bigserial PRIMARY KEY,
    agent_id               bigint      NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version                text        NOT NULL,          -- semver, e.g. 1.0.0
    status                 text        NOT NULL DEFAULT 'draft'
                             CHECK (status IN ('draft', 'published', 'deprecated')),
    spec                   jsonb       NOT NULL,
    forked_from_version_id bigint      NULL
                             REFERENCES agent_versions(id) ON DELETE SET NULL,
    -- SPEC.md §10 bootstrap: meta-agents and secret-scanner ship published
    -- without passing their own publish pipeline. 'platform' marks them so the
    -- pipeline is not asked to validate itself. Audit finding 8.
    origin                 text        NOT NULL DEFAULT 'user'
                             CHECK (origin IN ('user', 'platform')),
    published_by           bigint      NULL REFERENCES users(id),
    published_at           timestamptz NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),

    -- #>> IS immutable (backed by jsonb_extract_path_text, provolatile='i'),
    -- so these are legal exactly as SPEC.md §13 sketches them. A missing path
    -- yields NULL, so never add NOT NULL here.
    description     text GENERATED ALWAYS AS
                      (spec #>> '{spec,use_case,description}') STORED,
    desired_outcome text GENERATED ALWAYS AS
                      (spec #>> '{spec,use_case,desired_outcome}') STORED,

    -- CORRECTION 1. The obvious form, to_tsvector('english', description), is
    -- rejected:
    --   ERROR: cannot use generated column "description" in column generation
    --          expression
    -- A generated column cannot reference another generated column, so the JSON
    -- paths are re-derived here. Writing them twice is unavoidable.
    -- The explicit regconfig is also required: to_tsvector(text) is STABLE
    -- (it reads default_text_search_config) and gives
    --   ERROR: generation expression is not immutable
    -- while to_tsvector(regconfig, text) is IMMUTABLE.
    search tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english',
            coalesce(spec #>> '{spec,use_case,description}', '')), 'A')
     || setweight(to_tsvector('english',
            coalesce(spec #>> '{spec,use_case,desired_outcome}', '')), 'B')
    ) STORED,

    -- CORRECTION 2. embedding must NOT be generated. A generated vector column
    -- compiles, which is the trap, but it cannot be written:
    --   ERROR: cannot insert a non-DEFAULT value into column "embedding"
    -- Embeddings come from an API, so this is a plain column the app fills.
    -- NULL doubles as the backfill queue: WHERE embedding IS NULL.
    embedding            vector(1536) NULL,
    -- Detects a stale vector after a spec edit rather than silently serving a
    -- mismatched embedding.
    embedding_spec_hash  text         NULL,

    UNIQUE (agent_id, version)
);

ALTER TABLE agents
    ADD CONSTRAINT agents_current_version_fk
    FOREIGN KEY (current_version_id) REFERENCES agent_versions(id) ON DELETE SET NULL;

CREATE INDEX agent_versions_search     ON agent_versions USING gin (search);
CREATE INDEX agent_versions_agent      ON agent_versions (agent_id, created_at DESC);
CREATE INDEX agent_versions_published  ON agent_versions (status)
    WHERE status = 'published';
-- Swap ivfflat/hnsw for `USING diskann` on Tiger Cloud; it is a Tiger-only
-- index and worth a sentence in the pitch. See docs/research/tigerdata.md.
CREATE INDEX agent_versions_embedding  ON agent_versions
    USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Runs. Time-series, for the analytics page and the Tiger Data track.
-- ---------------------------------------------------------------------------

CREATE TABLE runs (
    id                bigserial   NOT NULL,
    parent_run_id     bigint      NULL,
    agent_version_id  bigint      NOT NULL REFERENCES agent_versions(id),
    team_id           bigint      NULL REFERENCES teams(id),
    trigger           text        NOT NULL
                        CHECK (trigger IN ('http','mcp','schedule','github','slack','agent','system')),
    -- Audit finding 2: the acting principal comes from the session token, never
    -- from the request body. NULL only for trigger IN ('schedule','system').
    triggered_by      bigint      NULL REFERENCES users(id),
    model             text        NULL,
    status            text        NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','succeeded','failed','denied','cancelled','awaiting_approval')),
    input_tokens      integer     NOT NULL DEFAULT 0,
    output_tokens     integer     NOT NULL DEFAULT 0,
    cost_usd          numeric(12,6) NOT NULL DEFAULT 0,
    -- Audit finding 23: SPEC.md §15 records steps per run but §13 had no column.
    steps             integer     NOT NULL DEFAULT 0,
    latency_ms        integer     NULL,
    error             text        NULL,
    started_at        timestamptz NOT NULL DEFAULT now(),
    ended_at          timestamptz NULL,

    -- extract(epoch FROM interval) is IMMUTABLE; extract(epoch FROM timestamptz)
    -- is STABLE and would be rejected here. Subtract first.
    duration_ms integer GENERATED ALWAYS AS
        (floor(extract(epoch FROM (ended_at - started_at)) * 1000)::integer) STORED,

    -- CORRECTION 3. SPEC.md §13 says `runs (id, ...)` with id as the key, and
    -- that is incompatible with a hypertable:
    --   ERROR: cannot create a unique index without the column "started_at"
    --          (used in partitioning)
    -- Every UNIQUE index and PK on a hypertable must contain all partitioning
    -- columns, because uniqueness is per chunk. A separate UNIQUE(id) is also
    -- rejected.
    --
    -- CONSEQUENCE, and everyone must know this: nothing can foreign-key to
    -- runs(id) alone. run_events, approvals and eval_runs therefore carry a
    -- plain run_id with no FK constraint. id stays unique in practice via the
    -- sequence; the database no longer enforces it.
    PRIMARY KEY (id, started_at)
);

SELECT create_hypertable('runs', by_range('started_at'));

CREATE INDEX runs_team_time   ON runs (team_id, started_at DESC);
CREATE INDEX runs_agent_time  ON runs (agent_version_id, started_at DESC);
CREATE INDEX runs_parent      ON runs (parent_run_id) WHERE parent_run_id IS NOT NULL;
CREATE INDEX runs_status      ON runs (status, started_at DESC);
-- Audit finding 7: usd_per_month and max_concurrent_runs are AGGREGATE limits
-- enforced per layer, so the hot query is a sum by team over a month window.
CREATE INDEX runs_cost_rollup ON runs (team_id, started_at) INCLUDE (cost_usd);

CREATE TABLE run_events (
    id               bigserial   NOT NULL,
    run_id           bigint      NOT NULL,   -- no FK: see CORRECTION 3 above
    seq              integer     NOT NULL,
    type             text        NOT NULL
                       CHECK (type IN ('span_open','span_close','model_call','tool_call',
                                       'approval_requested','approval_decided','redaction_hit',
                                       'policy_violation','retry','metric','error')),
    action           text        NULL,       -- '<connector>:<action>' or 'agent:<name>'
    allowed          boolean     NULL,
    payload_redacted jsonb       NULL,       -- ALWAYS post-redaction. Never raw.
    ts               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable('run_events', by_range('ts'));
CREATE INDEX run_events_run ON run_events (run_id, seq);

-- ---------------------------------------------------------------------------
-- Approvals
-- ---------------------------------------------------------------------------

CREATE TABLE approvals (
    id           bigserial PRIMARY KEY,
    run_id       bigint      NOT NULL,   -- no FK: see CORRECTION 3
    action       text        NOT NULL,
    args_redacted jsonb      NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    -- Audit finding 16: SPEC.md never said what an approval timeout does, and
    -- enterprise.yaml carries timeout_minutes. Frozen rule: expiry DENIES and
    -- fails the run with APPROVAL_EXPIRED, and counts as a rejection in the
    -- human intervention rate.
    expires_at   timestamptz NULL,
    approver_id  bigint      NULL REFERENCES users(id),
    decision     text        NULL CHECK (decision IN ('approved','rejected','expired')),
    decided_at   timestamptz NULL,
    CONSTRAINT decided_has_time CHECK (
        (decision IS NULL AND decided_at IS NULL) OR
        (decision IS NOT NULL AND decided_at IS NOT NULL)
    )
);
CREATE INDEX approvals_pending ON approvals (requested_at) WHERE decision IS NULL;
CREATE INDEX approvals_run     ON approvals (run_id);

-- ---------------------------------------------------------------------------
-- Evaluation
-- ---------------------------------------------------------------------------

CREATE TABLE eval_runs (
    id                      bigserial PRIMARY KEY,
    agent_version_id        bigint      NOT NULL REFERENCES agent_versions(id) ON DELETE CASCADE,
    kind                    text        NOT NULL CHECK (kind IN ('ci','production','mutation')),
    pass_rate               numeric(5,4) NULL,
    pass_rate_ci_low        numeric(5,4) NULL,
    pass_rate_ci_high       numeric(5,4) NULL,
    pass_k                  numeric(5,4) NULL,
    recall                  numeric(5,4) NULL,
    false_alarm_rate        numeric(5,4) NULL,
    safety_pass_rate        numeric(5,4) NULL,
    mutation_score          numeric(5,4) NULL,
    -- Audit finding 23: SPEC.md §7 lists these per version, §13 had no columns.
    tool_call_accuracy      numeric(5,4) NULL,
    human_intervention_rate numeric(5,4) NULL,
    avg_cost                numeric(12,6) NULL,
    p95_latency             integer     NULL,
    results                 jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX eval_runs_version ON eval_runs (agent_version_id, created_at DESC);

CREATE TABLE judge_calibration (
    id          bigserial PRIMARY KEY,
    judge_model text        NOT NULL,
    -- Audit finding 19: SPEC.md §7 points at enterprise.spec.evaluation.judge
    -- .calibration, a path the JSON Schema REJECTS. Calibration lives only
    -- here. rubric_hash = sha256 of the judge rubric string.
    rubric_hash text        NOT NULL,
    agreement   numeric(5,4) NOT NULL,
    kappa       numeric(5,4) NULL,
    n_labels    integer     NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE calibration_labels (
    id               bigserial PRIMARY KEY,
    agent_version_id bigint  NULL REFERENCES agent_versions(id) ON DELETE SET NULL,
    case_name        text    NOT NULL,
    output_redacted  text    NOT NULL,
    human_verdict    boolean NOT NULL,
    labeled_by       bigint  NOT NULL REFERENCES users(id),
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Publish pipeline (SPEC.md §11)
-- ---------------------------------------------------------------------------

CREATE TABLE publish_runs (
    id               bigserial PRIMARY KEY,
    agent_id         bigint      NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    draft_version_id bigint      NOT NULL REFERENCES agent_versions(id) ON DELETE CASCADE,
    stage            text        NOT NULL
                       CHECK (stage IN ('validate','security','generate','review','evals','gate','publish')),
    -- Audit finding 17: SPEC.md enumerated stage but left status undefined, and
    -- 'published' appeared as both a stage and a version status. Frozen: status
    -- is the lifecycle, stage names the current or failing step only.
    status           text        NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running','failed','abandoned','succeeded')),
    started_by       bigint      NOT NULL REFERENCES users(id),
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz NULL,
    stage_results    jsonb       NOT NULL DEFAULT '{}'::jsonb
);
-- At most one running pipeline per agent. Without this, two concurrent
-- pipelines both reach stage 7 and race current_version_id.
CREATE UNIQUE INDEX publish_runs_one_active ON publish_runs (agent_id)
    WHERE status = 'running';

CREATE TABLE case_reviews (
    id              bigserial PRIMARY KEY,
    publish_run_id  bigint      NOT NULL REFERENCES publish_runs(id) ON DELETE CASCADE,
    -- Audit finding 15: a bare case name is unique only within one agent's own
    -- cases, while inherited enterprise and team default_cases land in the same
    -- run. Identity is '<scope>/<name>' where scope is 'agent', 'enterprise' or
    -- 'team:<name>'.
    case_id         text        NOT NULL,
    -- sha256 of json.dumps(case, sort_keys=True, separators=(",",":")) with
    -- kind and origin defaults materialised and all other defaults omitted.
    -- Computed SERVER-SIDE ONLY. Editing a case invalidates its approval.
    case_hash       text        NOT NULL,
    origin          text        NOT NULL CHECK (origin IN ('generated','authored','inherited')),
    decision        text        NOT NULL CHECK (decision IN ('approved','edited','rejected')),
    edited_case     jsonb       NULL,
    reason          text        NULL,
    reviewed_by     bigint      NOT NULL REFERENCES users(id),
    reviewed_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (publish_run_id, case_id)
);

-- ---------------------------------------------------------------------------
-- Memory (SPEC.md §9)
-- ---------------------------------------------------------------------------

CREATE TABLE memory_buckets (
    id            bigserial PRIMARY KEY,
    -- Audit finding 6: names are a flat global namespace, so without UNIQUE a
    -- team layer can redefine an enterprise bucket and widen its ACL to
    -- write: ["*"]. The validator passed exactly that mutation.
    name          text    NOT NULL UNIQUE,
    type          text    NOT NULL CHECK (type IN ('semantic','episodic','procedural')),
    owner_team_id bigint  NULL REFERENCES teams(id) ON DELETE CASCADE,
    read          jsonb   NOT NULL DEFAULT '[]'::jsonb,
    write         jsonb   NOT NULL DEFAULT '[]'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memory_items (
    id            bigserial PRIMARY KEY,
    bucket_id     bigint      NOT NULL REFERENCES memory_buckets(id) ON DELETE CASCADE,
    content       text        NOT NULL,
    embedding     vector(1536) NULL,
    -- SPEC.md §9: records which run wrote each entry, because shared memory is
    -- a prompt-injection path.
    source_run_id bigint      NULL,
    -- 'pending' exists for procedural entries, which apply only after the
    -- agent's evals pass again.
    status        text        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('pending','active','rejected')),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memory_items_bucket    ON memory_items (bucket_id, created_at DESC);
CREATE INDEX memory_items_embedding ON memory_items
    USING hnsw (embedding vector_cosine_ops);
