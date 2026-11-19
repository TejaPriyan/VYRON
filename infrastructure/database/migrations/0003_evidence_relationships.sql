-- Migration 0003: sources, evidence and relationships (the trust layer and the graph's source of truth).
--
-- STATUS: UNVERIFIED (never executed against PostgreSQL; see 0001).
--
-- PostgreSQL is authoritative for relationships; Neo4j holds a rebuildable projection. A
-- relationship with provenance 'sourced' must have at least one relationship_evidence row. That
-- rule spans two tables, so the application enforces it on write and the integrity views at the
-- bottom let CI and operators detect any violation.

CREATE TABLE sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    url text NOT NULL CHECK (url ~ '^https?://' AND length(url) <= 2048),
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    publisher text,
    published_on date,
    source_type source_type NOT NULL,
    reliability reliability NOT NULL DEFAULT 'unverified',
    retrieved_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE UNIQUE INDEX sources_url_key ON sources (url) WHERE deleted_at IS NULL;

-- claim -> source -> entity -> timestamp
CREATE TABLE evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    claim text NOT NULL CHECK (length(btrim(claim)) BETWEEN 1 AND 1000),
    locator text,
    retrieved_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX evidence_entity_idx ON evidence (entity_id);
CREATE INDEX evidence_source_idx ON evidence (source_id);

CREATE TABLE relationships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    target_entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    relation_type relation_type NOT NULL,
    provenance provenance NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_entity_id, target_entity_id, relation_type),
    CHECK (source_entity_id <> target_entity_id)
);

CREATE INDEX relationships_target_idx ON relationships (target_entity_id, relation_type);

CREATE TABLE relationship_evidence (
    relationship_id uuid NOT NULL REFERENCES relationships (id) ON DELETE CASCADE,
    evidence_id uuid NOT NULL REFERENCES evidence (id) ON DELETE CASCADE,
    PRIMARY KEY (relationship_id, evidence_id)
);

CREATE INDEX relationship_evidence_evidence_idx ON relationship_evidence (evidence_id);

CREATE TRIGGER sources_set_updated_at BEFORE UPDATE ON sources
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER evidence_set_updated_at BEFORE UPDATE ON evidence
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER relationships_set_updated_at BEFORE UPDATE ON relationships
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Integrity checks: each view must return zero rows.
CREATE VIEW integrity_sourced_relationships_without_evidence AS
SELECT r.id
FROM relationships r
WHERE r.provenance = 'sourced'
  AND NOT EXISTS (SELECT 1 FROM relationship_evidence re WHERE re.relationship_id = r.id);

CREATE VIEW integrity_sourced_entities_without_evidence AS
SELECT e.id
FROM entities e
WHERE e.provenance = 'sourced'
  AND e.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM evidence ev WHERE ev.entity_id = e.id);
