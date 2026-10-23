-- Human source facts are durable workflow inputs, not Claim approvals or runs.
-- Keep the original evidence immutable; explicit replacements retain history.
CREATE TABLE IF NOT EXISTS wang_knowledge.human_source_decisions (
    decision_id text PRIMARY KEY,
    source_id text NOT NULL,
    entry jsonb NOT NULL,
    origin text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    superseded_by text REFERENCES wang_knowledge.human_source_decisions(decision_id)
);
CREATE INDEX IF NOT EXISTS human_source_decisions_current_source_idx
    ON wang_knowledge.human_source_decisions(source_id, decision_id)
    WHERE superseded_by IS NULL;

INSERT INTO wang_knowledge.schema_migrations(migration_id)
VALUES ('008_human_source_decisions') ON CONFLICT DO NOTHING;
