-- Discovery belongs to existing SQL runs, not a second content/workflow store.
-- Exact issue identity uses the existing PK; completion is bounded by its owner.
CREATE INDEX IF NOT EXISTS pipeline_runs_claim_issue_owner_idx
    ON wang_knowledge.pipeline_runs ((metadata->>'owner_run_id'))
    WHERE metadata->>'kind'='claim_issue';

INSERT INTO wang_knowledge.schema_migrations(migration_id)
VALUES ('007_claim_issue_lookup') ON CONFLICT DO NOTHING;
