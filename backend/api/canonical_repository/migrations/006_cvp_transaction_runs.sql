-- SQL CVP checkpoints share the existing run and history tables. No duplicate
-- changelog, snapshot store, or background work is created by this migration.
ALTER TABLE wang_knowledge.pipeline_runs
    DROP CONSTRAINT IF EXISTS pipeline_runs_stage_check;
ALTER TABLE wang_knowledge.pipeline_runs
    ADD CONSTRAINT pipeline_runs_stage_check
    CHECK (stage IN ('sectioning','extraction','cross_section','review',
                    'adjudication','merge','ingest','article','cvp_group'));

CREATE UNIQUE INDEX IF NOT EXISTS pipeline_runs_cvp_group_idx
    ON wang_knowledge.pipeline_runs
       ((metadata->>'campaign_sha256'), (metadata->>'group_key'))
    WHERE stage='cvp_group';
CREATE INDEX IF NOT EXISTS object_versions_change_set_idx
    ON wang_knowledge.object_versions(change_set_id, collection, object_id);
CREATE INDEX IF NOT EXISTS edges_semantic_identity_idx
    ON wang_knowledge.edges(edge_collection, from_id, to_id, relation_type)
    WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS objects_evidence_producers_idx
    ON wang_knowledge.objects USING gin ((payload->'produced_claim_ids'))
    WHERE collection='evidence_steps' AND retired_at IS NULL;
CREATE INDEX IF NOT EXISTS objects_source_transcript_idx
    ON wang_knowledge.objects ((payload->>'transcript_id'))
    WHERE collection='source_documents' AND retired_at IS NULL;
CREATE INDEX IF NOT EXISTS objects_current_dependency_manifest_idx
    ON wang_knowledge.objects USING gin ((COALESCE(payload->'dependency_manifest','[]'::jsonb)))
    WHERE collection='product_dependencies' AND retired_at IS NULL;
CREATE INDEX IF NOT EXISTS objects_current_dependency_claim_idx
    ON wang_knowledge.objects ((payload->>'claim_id'))
    WHERE collection='product_dependencies' AND retired_at IS NULL;
CREATE INDEX IF NOT EXISTS pipeline_runs_cvp_claims_idx
    ON wang_knowledge.pipeline_runs USING gin ((metadata->'claim_ids'))
    WHERE stage='cvp_group';
CREATE INDEX IF NOT EXISTS pipeline_runs_cvp_import_views_idx
    ON wang_knowledge.pipeline_runs USING gin ((metadata->'import_certificate'->'viewpoint_ids'))
    WHERE stage='cvp_group';
CREATE INDEX IF NOT EXISTS pipeline_runs_cvp_reviewed_views_idx
    ON wang_knowledge.pipeline_runs USING gin ((metadata->'reviewed'->'evidence'->'viewpoint_ids'))
    WHERE stage='cvp_group';
CREATE INDEX IF NOT EXISTS pipeline_runs_cvp_semantic_targets_idx
    ON wang_knowledge.pipeline_runs USING gin ((metadata->'semantic_adjudication_targets'))
    WHERE stage='cvp_group';
CREATE INDEX IF NOT EXISTS pipeline_runs_cvp_review_action_idx
    ON wang_knowledge.pipeline_runs ((metadata->>'parent_run_id'),(metadata->>'action_id'))
    WHERE stage='cvp_group' AND metadata->>'kind'='cvp_review_archive';

INSERT INTO wang_knowledge.schema_migrations(migration_id)
VALUES ('006_cvp_transaction_runs') ON CONFLICT DO NOTHING;
