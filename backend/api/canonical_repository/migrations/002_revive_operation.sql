-- `revive` joins the operations a change set may record.
--
-- Retirement is a judgement and judgements are sometimes wrong: a fragment was
-- withdrawn because a short excerpt happened to occur inside a deleted span
-- somewhere in its source, while the occurrence its anchor meant survived.
-- Without this the only remedies were editing rows behind the history tables'
-- back, or leaving a true record withdrawn.
--
-- Idempotent, like 001: the constraint is dropped and recreated rather than
-- altered, because `migrate()` replays every file in the directory each time.

ALTER TABLE wang_knowledge.change_operations
    DROP CONSTRAINT IF EXISTS change_operations_operation_check;

ALTER TABLE wang_knowledge.change_operations
    ADD CONSTRAINT change_operations_operation_check
    CHECK (operation = ANY (ARRAY['create', 'update', 'retire', 'revive', 'invalidate']));
