-- Migration 0002: entities and their typed detail tables.
--
-- STATUS: UNVERIFIED (never executed against PostgreSQL; see 0001).
--
-- Design: one base table `entities` (identity, kind, slug, name, provenance, soft delete) plus a
-- detail table per family for typed columns. Each detail table repeats `kind` and uses a composite
-- foreign key (entity_id, kind) -> entities (id, kind) with a CHECK on `kind`, so a `languages` row
-- can only ever point at a language entity. Companies and organizations live in `organizations`;
-- historical events are entities of kind 'event' whose dates live in `timeline_events`.
-- Soft deletion happens on `entities` only; detail rows follow their entity.

CREATE TABLE entities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind entity_kind NOT NULL,
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    summary text,
    provenance provenance NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    UNIQUE (id, kind)
);

CREATE UNIQUE INDEX entities_kind_slug_key ON entities (kind, slug) WHERE deleted_at IS NULL;
CREATE INDEX entities_kind_name_idx ON entities (kind, lower(name));
CREATE INDEX entities_name_trgm_idx ON entities USING gin (lower(name) gin_trgm_ops)
    WHERE deleted_at IS NULL;

CREATE TABLE entity_aliases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    alias text NOT NULL CHECK (length(btrim(alias)) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX entity_aliases_entity_alias_key ON entity_aliases (entity_id, lower(alias));
CREATE INDEX entity_aliases_alias_trgm_idx ON entity_aliases USING gin (lower(alias) gin_trgm_ops);

CREATE TABLE technologies (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL
        CHECK (kind IN ('technology', 'framework', 'library', 'database', 'platform', 'api')),
    category text,
    first_release_year smallint CHECK (first_release_year BETWEEN 1940 AND 2100),
    homepage_url text CHECK (homepage_url IS NULL OR homepage_url ~ '^https?://'),
    license_spdx text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE TABLE languages (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL DEFAULT 'language' CHECK (kind = 'language'),
    first_appeared_year smallint CHECK (first_appeared_year BETWEEN 1940 AND 2100),
    paradigms text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE TABLE organizations (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL CHECK (kind IN ('company', 'organization')),
    website_url text CHECK (website_url IS NULL OR website_url ~ '^https?://'),
    country_code char(2) CHECK (country_code ~ '^[A-Z]{2}$'),
    founded_year smallint CHECK (founded_year BETWEEN 1800 AND 2100),
    github_login text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE UNIQUE INDEX organizations_github_login_key ON organizations (lower(github_login))
    WHERE github_login IS NOT NULL;

CREATE TABLE repositories (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL DEFAULT 'repository' CHECK (kind = 'repository'),
    github_id bigint,
    owner_login text NOT NULL,
    name text NOT NULL,
    owner_entity_id uuid REFERENCES entities (id) ON DELETE SET NULL,
    default_branch text,
    primary_language_id uuid REFERENCES languages (entity_id) ON DELETE SET NULL,
    topics text[] NOT NULL DEFAULT '{}',
    license_spdx text,
    stars integer NOT NULL DEFAULT 0 CHECK (stars >= 0),
    forks integer NOT NULL DEFAULT 0 CHECK (forks >= 0),
    open_issues integer NOT NULL DEFAULT 0 CHECK (open_issues >= 0),
    archived boolean NOT NULL DEFAULT false,
    is_fork boolean NOT NULL DEFAULT false,
    repo_created_at timestamptz,
    pushed_at timestamptz,
    etag text,
    last_ingested_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE UNIQUE INDEX repositories_github_id_key ON repositories (github_id)
    WHERE github_id IS NOT NULL;
CREATE UNIQUE INDEX repositories_owner_name_key ON repositories (lower(owner_login), lower(name));
CREATE INDEX repositories_owner_entity_idx ON repositories (owner_entity_id);
CREATE INDEX repositories_language_idx ON repositories (primary_language_id);

-- Bytes per language as reported by GitHub. language_id is set only when the name maps to a
-- known language entity ("Jupyter Notebook" or "Makefile" need not).
CREATE TABLE repository_languages (
    repository_id uuid NOT NULL REFERENCES repositories (entity_id) ON DELETE CASCADE,
    language_name text NOT NULL CHECK (length(btrim(language_name)) BETWEEN 1 AND 200),
    language_id uuid REFERENCES languages (entity_id) ON DELETE SET NULL,
    bytes bigint NOT NULL CHECK (bytes >= 0),
    measured_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repository_id, language_name)
);

CREATE INDEX repository_languages_language_idx ON repository_languages (language_id);

CREATE TABLE ai_models (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL DEFAULT 'ai_model' CHECK (kind = 'ai_model'),
    modalities text[] NOT NULL DEFAULT '{}',
    released_on date,
    homepage_url text CHECK (homepage_url IS NULL OR homepage_url ~ '^https?://'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE TABLE datasets (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL DEFAULT 'dataset' CHECK (kind = 'dataset'),
    modality text,
    license_spdx text,
    homepage_url text CHECK (homepage_url IS NULL OR homepage_url ~ '^https?://'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE TABLE research_papers (
    entity_id uuid PRIMARY KEY,
    kind entity_kind NOT NULL DEFAULT 'research_paper' CHECK (kind = 'research_paper'),
    doi text,
    arxiv_id text,
    venue text,
    published_on date,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (entity_id, kind) REFERENCES entities (id, kind) ON DELETE CASCADE
);

CREATE UNIQUE INDEX research_papers_doi_key ON research_papers (lower(doi)) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX research_papers_arxiv_key ON research_papers (lower(arxiv_id))
    WHERE arxiv_id IS NOT NULL;

-- Dated occurrences. Predictions are stored here too but must be flagged; the rule that an event
-- dated in the future has to be a prediction depends on now() and is enforced by the application.
CREATE TABLE timeline_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    occurred_on date NOT NULL,
    date_precision date_precision NOT NULL DEFAULT 'day',
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    event_type text NOT NULL CHECK (length(btrim(event_type)) > 0),
    description text,
    provenance provenance NOT NULL,
    is_prediction boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CHECK (is_prediction = (provenance = 'predicted'))
);

CREATE INDEX timeline_events_entity_idx ON timeline_events (entity_id, occurred_on);
CREATE INDEX timeline_events_occurred_idx ON timeline_events (occurred_on)
    WHERE deleted_at IS NULL;

CREATE TRIGGER entities_set_updated_at BEFORE UPDATE ON entities
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER technologies_set_updated_at BEFORE UPDATE ON technologies
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER languages_set_updated_at BEFORE UPDATE ON languages
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER organizations_set_updated_at BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER repositories_set_updated_at BEFORE UPDATE ON repositories
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER ai_models_set_updated_at BEFORE UPDATE ON ai_models
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER datasets_set_updated_at BEFORE UPDATE ON datasets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER research_papers_set_updated_at BEFORE UPDATE ON research_papers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER timeline_events_set_updated_at BEFORE UPDATE ON timeline_events
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
