-- EXPLICIT MAINTENANCE ONLY. Not in migrations/: migrate()/startup must never
-- install this as a hidden fallback. No existing objects/payloads are modified.
-- Installation is NOT a baseline certification. An initial complete, stable
-- readback must separately bind its fingerprint to the resulting generation.
CREATE TABLE wang_knowledge.integrity_head (
    singleton smallint PRIMARY KEY CHECK (singleton = 1),
    journal_id uuid NOT NULL DEFAULT gen_random_uuid(),
    sequence bigint NOT NULL DEFAULT 0 CHECK (sequence >= 0)
);
INSERT INTO wang_knowledge.integrity_head(singleton) VALUES (1);

CREATE TABLE wang_knowledge.integrity_changes (
    journal_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    transaction_id text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
    before_collection text,
    before_object_id text,
    after_collection text,
    after_object_id text,
    before_signature jsonb,
    after_signature jsonb,
    PRIMARY KEY (journal_id, sequence)
);

CREATE FUNCTION wang_knowledge.object_integrity_signature(value wang_knowledge.objects)
RETURNS jsonb LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog SET timezone = 'UTC'
AS $wkp$
SELECT jsonb_build_object(
    'revision', value.revision,
    'content_sha256', btrim(value.content_sha256::text),
    'retired', value.retired_at IS NOT NULL,
    'row_sha256', encode(sha256(convert_to(to_jsonb(value)::text,'UTF8')),'hex'));
$wkp$;

CREATE FUNCTION wang_knowledge.capture_object_integrity_change()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
AS $wkp$
DECLARE identity uuid; position bigint;
BEGIN
    UPDATE wang_knowledge.integrity_head SET sequence = sequence + 1
    WHERE singleton = 1 RETURNING journal_id, sequence INTO identity, position;
    IF NOT FOUND THEN RAISE EXCEPTION 'integrity head missing; object write refused'; END IF;
    INSERT INTO wang_knowledge.integrity_changes
        (journal_id,sequence,transaction_id,operation,
         before_collection,before_object_id,after_collection,after_object_id,
         before_signature,after_signature)
    VALUES (identity,position,pg_current_xact_id()::text,TG_OP,
        CASE WHEN TG_OP <> 'INSERT' THEN OLD.collection END,
        CASE WHEN TG_OP <> 'INSERT' THEN OLD.object_id END,
        CASE WHEN TG_OP <> 'DELETE' THEN NEW.collection END,
        CASE WHEN TG_OP <> 'DELETE' THEN NEW.object_id END,
        CASE WHEN TG_OP <> 'INSERT' THEN wang_knowledge.object_integrity_signature(OLD) END,
        CASE WHEN TG_OP <> 'DELETE' THEN wang_knowledge.object_integrity_signature(NEW) END);
    RETURN NULL;
END;
$wkp$;

CREATE FUNCTION wang_knowledge.guard_integrity_metadata_write()
RETURNS trigger LANGUAGE plpgsql VOLATILE
SET search_path = pg_catalog
AS $wkp$
BEGIN
    IF pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'direct integrity metadata write refused';
    END IF;
    -- Another trigger is not permission to rewind the head or edit history.
    -- A privileged caller can invalidate a baseline, never restore its old
    -- sequence by a nested DELETE/UPDATE without changing guarded catalogs.
    IF TG_TABLE_NAME = 'integrity_head' THEN
        IF TG_OP <> 'UPDATE' OR NEW.singleton <> OLD.singleton
           OR NEW.journal_id <> OLD.journal_id OR NEW.sequence <> OLD.sequence + 1 THEN
            RAISE EXCEPTION 'integrity head must advance exactly once; identity/rewind refused';
        END IF;
    ELSIF TG_TABLE_NAME = 'integrity_changes' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'integrity history is immutable';
        END IF;
        IF NEW.transaction_id <> pg_current_xact_id()::text
           OR NOT EXISTS (SELECT 1 FROM wang_knowledge.integrity_head h
                          WHERE h.journal_id = NEW.journal_id AND h.sequence = NEW.sequence) THEN
            RAISE EXCEPTION 'integrity history must match current transaction/head';
        END IF;
    ELSE
        RAISE EXCEPTION 'unexpected integrity guard table';
    END IF;
    RETURN NEW;
END;
$wkp$;

CREATE FUNCTION wang_knowledge.deny_integrity_truncate()
RETURNS trigger LANGUAGE plpgsql VOLATILE
SET search_path = pg_catalog
AS $wkp$
BEGIN
    RAISE EXCEPTION 'integrity-protected truncate requires explicit new maintenance baseline';
END;
$wkp$;

CREATE TRIGGER integrity_capture AFTER INSERT OR UPDATE OR DELETE ON wang_knowledge.objects
FOR EACH ROW EXECUTE FUNCTION wang_knowledge.capture_object_integrity_change();
CREATE TRIGGER integrity_objects_no_truncate BEFORE TRUNCATE ON wang_knowledge.objects
EXECUTE FUNCTION wang_knowledge.deny_integrity_truncate();
CREATE TRIGGER integrity_head_write_guard BEFORE INSERT OR UPDATE OR DELETE ON wang_knowledge.integrity_head
FOR EACH ROW EXECUTE FUNCTION wang_knowledge.guard_integrity_metadata_write();
CREATE TRIGGER integrity_changes_write_guard BEFORE INSERT OR UPDATE OR DELETE ON wang_knowledge.integrity_changes
FOR EACH ROW EXECUTE FUNCTION wang_knowledge.guard_integrity_metadata_write();
CREATE TRIGGER integrity_head_no_truncate BEFORE TRUNCATE ON wang_knowledge.integrity_head
EXECUTE FUNCTION wang_knowledge.deny_integrity_truncate();
CREATE TRIGGER integrity_changes_no_truncate BEFORE TRUNCATE ON wang_knowledge.integrity_changes
EXECUTE FUNCTION wang_knowledge.deny_integrity_truncate();

ALTER TABLE wang_knowledge.objects ENABLE ALWAYS TRIGGER integrity_capture;
ALTER TABLE wang_knowledge.objects ENABLE ALWAYS TRIGGER integrity_objects_no_truncate;
ALTER TABLE wang_knowledge.integrity_head ENABLE ALWAYS TRIGGER integrity_head_write_guard;
ALTER TABLE wang_knowledge.integrity_head ENABLE ALWAYS TRIGGER integrity_head_no_truncate;
ALTER TABLE wang_knowledge.integrity_changes ENABLE ALWAYS TRIGGER integrity_changes_write_guard;
ALTER TABLE wang_knowledge.integrity_changes ENABLE ALWAYS TRIGGER integrity_changes_no_truncate;
REVOKE ALL ON wang_knowledge.integrity_head, wang_knowledge.integrity_changes FROM PUBLIC;
