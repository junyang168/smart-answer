CREATE SCHEMA IF NOT EXISTS wang_knowledge;

CREATE TABLE IF NOT EXISTS wang_knowledge.schema_migrations (
    migration_id text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wang_knowledge.change_sets (
    change_set_id text PRIMARY KEY,
    fingerprint_sha256 char(64) NOT NULL UNIQUE,
    package_id text NOT NULL,
    source_kind text NOT NULL,
    source_sha256 char(64) NOT NULL,
    status text NOT NULL CHECK (status IN ('planned', 'applied', 'rejected')),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz
);

CREATE TABLE IF NOT EXISTS wang_knowledge.objects (
    collection text NOT NULL,
    object_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    review_status text NOT NULL DEFAULT 'candidate',
    visibility text NOT NULL DEFAULT 'internal',
    content_sha256 char(64) NOT NULL,
    source_fingerprint text,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    PRIMARY KEY (collection, object_id)
);

CREATE INDEX IF NOT EXISTS objects_review_status_idx
    ON wang_knowledge.objects (collection, review_status)
    WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS objects_payload_gin_idx
    ON wang_knowledge.objects USING gin (payload);

CREATE TABLE IF NOT EXISTS wang_knowledge.object_versions (
    collection text NOT NULL,
    object_id text NOT NULL,
    revision integer NOT NULL,
    content_sha256 char(64) NOT NULL,
    payload jsonb NOT NULL,
    change_set_id text NOT NULL REFERENCES wang_knowledge.change_sets(change_set_id),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, object_id, revision)
);

CREATE TABLE IF NOT EXISTS wang_knowledge.change_operations (
    change_set_id text NOT NULL REFERENCES wang_knowledge.change_sets(change_set_id) ON DELETE CASCADE,
    operation_index integer NOT NULL,
    operation text NOT NULL CHECK (operation IN ('create', 'update', 'retire', 'invalidate')),
    collection text NOT NULL,
    object_id text NOT NULL,
    before_sha256 char(64),
    after_sha256 char(64),
    before_revision integer,
    after_revision integer,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (change_set_id, operation_index)
);

CREATE TABLE IF NOT EXISTS wang_knowledge.edges (
    edge_collection text NOT NULL,
    edge_id text NOT NULL,
    from_id text NOT NULL,
    to_id text NOT NULL,
    relation_type text NOT NULL,
    review_status text NOT NULL DEFAULT 'candidate',
    revision integer NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    PRIMARY KEY (edge_collection, edge_id),
    FOREIGN KEY (edge_collection, edge_id)
        REFERENCES wang_knowledge.objects(collection, object_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edges_from_idx
    ON wang_knowledge.edges (from_id, relation_type)
    WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS edges_to_idx
    ON wang_knowledge.edges (to_id, relation_type)
    WHERE retired_at IS NULL;

CREATE TABLE IF NOT EXISTS wang_knowledge.review_events (
    review_event_id text PRIMARY KEY,
    collection text NOT NULL,
    object_id text NOT NULL,
    object_revision integer NOT NULL,
    reviewer_kind text NOT NULL CHECK (reviewer_kind IN ('human', 'ai', 'system')),
    reviewer_id text NOT NULL,
    decision text NOT NULL,
    reason text NOT NULL DEFAULT '',
    artifact jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS review_events_object_idx
    ON wang_knowledge.review_events (collection, object_id, created_at DESC);

INSERT INTO wang_knowledge.schema_migrations(migration_id)
VALUES ('001_postgres_authoring_store')
ON CONFLICT (migration_id) DO NOTHING;
