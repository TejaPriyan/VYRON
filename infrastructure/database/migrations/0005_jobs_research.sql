-- Migration 0005: background jobs, AI research runs with evidence mappings, API usage.
--
-- STATUS: UNVERIFIED (never executed against PostgreSQL; see 0001).
--
-- The job row is the durable record; any queue (Redis) is transient. A research run stores the
-- evidence it was shown (ai_research_run_evidence); every claim citation is a composite foreign
-- key into that set, so the database itself refuses a citation to evidence the model never saw.

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL CHECK (
        job_type IN ('ingest_repository', 'ingest_organization', 'refresh_entity',
                     'project_graph', 'research_run')
    ),
    status job_status NOT NULL DEFAULT 'queued',
    source text NOT NULL CHECK (length(btrim(source)) > 0),
    entity_id uuid REFERENCES entities (id) ON DELETE SET NULL,
    target text,
    dedupe_key text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts >= 1),
    run_after timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    error_code text,
    error_message text CHECK (error_message IS NULL OR length(error_message) <= 1000),
    created_by uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX jobs_entity_idx ON jobs (entity_id);
CREATE INDEX jobs_created_by_idx ON jobs (created_by);
CREATE INDEX jobs_status_idx ON jobs (status, created_at DESC);
CREATE INDEX jobs_claim_idx ON jobs (run_after) WHERE status = 'queued';
CREATE UNIQUE INDEX jobs_dedupe_active_key ON jobs (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');

CREATE TABLE ai_research_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES users (id) ON DELETE SET NULL,
    kind research_kind NOT NULL,
    status research_status NOT NULL DEFAULT 'queued',
    query text NOT NULL CHECK (length(btrim(query)) BETWEEN 1 AND 2000),
    provider text,
    model text,
    response jsonb CHECK (response IS NULL OR jsonb_typeof(response) = 'object'),
    error_code text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ai_research_runs_user_idx ON ai_research_runs (user_id, created_at DESC);

CREATE TABLE ai_research_run_entities (
    run_id uuid NOT NULL REFERENCES ai_research_runs (id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, entity_id)
);

CREATE INDEX ai_research_run_entities_entity_idx ON ai_research_run_entities (entity_id);

CREATE TABLE ai_research_run_evidence (
    run_id uuid NOT NULL REFERENCES ai_research_runs (id) ON DELETE CASCADE,
    evidence_id uuid NOT NULL REFERENCES evidence (id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, evidence_id)
);

CREATE INDEX ai_research_run_evidence_evidence_idx ON ai_research_run_evidence (evidence_id);

CREATE TABLE ai_research_claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES ai_research_runs (id) ON DELETE CASCADE,
    ordinal smallint NOT NULL CHECK (ordinal >= 0),
    claim_text text NOT NULL CHECK (length(btrim(claim_text)) BETWEEN 1 AND 1000),
    provenance provenance NOT NULL CHECK (provenance <> 'seed'),
    confidence real CHECK (confidence BETWEEN 0 AND 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, ordinal),
    UNIQUE (id, run_id)
);

CREATE TABLE ai_research_claim_evidence (
    claim_id uuid NOT NULL,
    run_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    PRIMARY KEY (claim_id, evidence_id),
    FOREIGN KEY (claim_id, run_id) REFERENCES ai_research_claims (id, run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, evidence_id)
        REFERENCES ai_research_run_evidence (run_id, evidence_id) ON DELETE CASCADE
);

CREATE INDEX ai_research_claim_evidence_run_idx ON ai_research_claim_evidence (run_id, evidence_id);

CREATE TABLE api_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    provider text NOT NULL CHECK (provider IN ('github', 'gemini', 'vyron')),
    operation text NOT NULL CHECK (length(btrim(operation)) > 0),
    status_code smallint CHECK (status_code BETWEEN 100 AND 599),
    duration_ms integer CHECK (duration_ms >= 0),
    rate_limit_remaining integer CHECK (rate_limit_remaining >= 0),
    rate_limit_resource text,
    cache_hit boolean NOT NULL DEFAULT false,
    user_id uuid REFERENCES users (id) ON DELETE SET NULL,
    job_id uuid REFERENCES jobs (id) ON DELETE SET NULL
);

CREATE INDEX api_usage_occurred_idx ON api_usage (occurred_at);
CREATE INDEX api_usage_user_idx ON api_usage (user_id);
CREATE INDEX api_usage_job_idx ON api_usage (job_id);

CREATE TRIGGER jobs_set_updated_at BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER ai_research_runs_set_updated_at BEFORE UPDATE ON ai_research_runs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
