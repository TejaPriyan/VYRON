-- VYRON schema, migration 0001: extensions, enumerated types, shared trigger function.
--
-- STATUS: UNVERIFIED. This SQL has been checked by a static linter (apps/api tests) but has never
-- been executed against a PostgreSQL server. Requires PostgreSQL 13+ (built-in gen_random_uuid()).
-- The enum values below must match apps/api/src/vyron_api/domain; a test enforces that.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE entity_kind AS ENUM (
    'technology', 'language', 'framework', 'library', 'ai_model', 'company', 'organization',
    'repository', 'dataset', 'research_paper', 'event', 'api', 'database', 'platform'
);

CREATE TYPE relation_type AS ENUM (
    'USES', 'DEPENDS_ON', 'BUILT_ON', 'RELATED_TO', 'EVOLVED_FROM', 'REPLACED_BY', 'USED_BY',
    'CREATED_BY', 'USES_LANGUAGE', 'USES_FRAMEWORK', 'OWNED_BY', 'FORKS', 'BUILDS', 'OWNS',
    'ACQUIRED', 'INVESTS_IN', 'REFERENCES', 'RELATES_TO', 'SUPPORTS', 'CONTRADICTS'
);

CREATE TYPE provenance AS ENUM (
    'sourced', 'estimated', 'inferred', 'predicted', 'user_generated', 'ai_generated', 'seed'
);

CREATE TYPE source_type AS ENUM (
    'official_docs', 'repository', 'paper', 'news', 'blog', 'archive', 'dataset', 'other'
);

CREATE TYPE reliability AS ENUM ('primary', 'secondary', 'tertiary', 'unverified');

CREATE TYPE date_precision AS ENUM ('day', 'month', 'year', 'decade');

CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');

CREATE TYPE map_mode AS ENUM (
    'ecosystem', 'dependencies', 'companies', 'ai', 'open_source', 'research', 'timeline'
);

CREATE TYPE research_kind AS ENUM (
    'explain_entity', 'explain_relationship', 'compare', 'historical', 'explain_repository',
    'risk', 'research', 'graph_question', 'future_scenario'
);

CREATE TYPE research_status AS ENUM ('queued', 'running', 'succeeded', 'failed');

CREATE TYPE user_role AS ENUM ('user', 'admin');

-- Keeps updated_at honest even when application code forgets to set it.
CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
