-- Explicit maintenance only; NOT an automatic production migration.
-- The collection predicate excludes unrelated CVR/decision history with the
-- same viewpoint_id. The reader verifies catalog definition and actual plan.
CREATE INDEX objects_audit_viewpoint_members_idx
ON wang_knowledge.objects ((payload->>'viewpoint_id'),object_id)
WHERE collection='viewpoint_claim_links' AND retired_at IS NULL;
