-- A knowledge ID identifies one record, not a (collection, id) pair.
--
-- The original table primary key allowed the same text ID in two collections.
-- That made an endpoint such as ``from_id`` ambiguous even though every
-- individual collection looked internally valid.  Current production data has
-- no such collision; fail closed if an older installation does, then let the
-- unique index arbitrate concurrent ChangeSets at the database boundary.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM wang_knowledge.objects
        GROUP BY object_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'cannot enforce global knowledge ID uniqueness: cross-collection duplicates exist';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS objects_object_id_global_unique_idx
    ON wang_knowledge.objects (object_id);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM wang_knowledge.objects
        WHERE collection = 'source_documents' AND retired_at IS NULL
          AND (
            NULLIF(btrim(payload->>'source_type'), '') IS NULL
            OR NULLIF(btrim(payload->>'transcript_id'), '') IS NULL
          )
    ) THEN
        RAISE EXCEPTION
            'cannot enforce current transcript identity uniqueness: SourceDocument lacks source_type or transcript_id';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM wang_knowledge.objects
        WHERE collection = 'source_documents' AND retired_at IS NULL
        GROUP BY btrim(payload->>'source_type'), btrim(payload->>'transcript_id')
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'cannot enforce current transcript identity uniqueness: duplicate SourceDocuments exist';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS source_documents_current_transcript_identity_unique_idx
    ON wang_knowledge.objects (
        (btrim(payload->>'source_type')),
        (btrim(payload->>'transcript_id'))
    )
    WHERE collection = 'source_documents' AND retired_at IS NULL;
