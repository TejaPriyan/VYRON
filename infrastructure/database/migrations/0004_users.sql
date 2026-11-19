-- Migration 0004: accounts, sessions and per-user data.
--
-- STATUS: UNVERIFIED (never executed against PostgreSQL; see 0001).
--
-- Secrets are never stored in the clear: `password_hash` holds an Argon2id encoded hash and
-- `token_hash` holds the SHA-256 of the opaque session cookie value, so a database leak does not
-- hand out working sessions. No IP addresses are stored.

CREATE TABLE users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL CHECK (length(email) BETWEEN 3 AND 254 AND position('@' IN email) > 1),
    password_hash text NOT NULL,
    display_name text CHECK (display_name IS NULL OR length(btrim(display_name)) BETWEEN 1 AND 80),
    role user_role NOT NULL DEFAULT 'user',
    email_verified_at timestamptz,
    last_login_at timestamptz,
    failed_login_count integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE UNIQUE INDEX users_email_lower_key ON users (lower(email)) WHERE deleted_at IS NULL;

CREATE TABLE sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash bytea NOT NULL UNIQUE,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at > created_at)
);

CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);

CREATE TABLE bookmarks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    note text CHECK (note IS NULL OR length(note) <= 1000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, entity_id)
);

CREATE INDEX bookmarks_entity_idx ON bookmarks (entity_id);

-- `state` is the map viewport, filters and selection: genuinely free-form UI state, size-capped.
CREATE TABLE saved_maps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 120),
    mode map_mode NOT NULL DEFAULT 'ecosystem',
    state jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(state) = 'object' AND pg_column_size(state) <= 65536),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE INDEX saved_maps_user_idx ON saved_maps (user_id, updated_at DESC);

CREATE TABLE search_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    query text NOT NULL CHECK (length(query) BETWEEN 1 AND 200),
    result_count integer CHECK (result_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX search_history_user_idx ON search_history (user_id, created_at DESC);

CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    category text NOT NULL CHECK (category IN ('job_finished', 'research_ready', 'system')),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    body text CHECK (body IS NULL OR length(body) <= 2000),
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC);

CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER sessions_set_updated_at BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER bookmarks_set_updated_at BEFORE UPDATE ON bookmarks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER saved_maps_set_updated_at BEFORE UPDATE ON saved_maps
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER notifications_set_updated_at BEFORE UPDATE ON notifications
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
