-- EXPLICIT maintenance only, NOT in migrations/. No automatic DDL/fallback.
-- Required by bounded committed-ChangeSet observations. Existing versions PK
-- starts with collection/object_id and cannot index a change_set_id-only read.
CREATE INDEX integrity_object_versions_change_set_idx
ON wang_knowledge.object_versions (change_set_id, collection, object_id, revision);
