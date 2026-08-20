-- Widen `pipeline_runs.stage` to the stages the pipeline actually has.
--
-- 003 listed six stages, and two of the runners in the chain had no name among
-- them: `cross_section`, which puts back the relations that sectioned
-- extraction necessarily splits. A run with no stage name is a run the overview
-- cannot show, so the one question the panel exists to answer -- where is this
-- source stuck -- was unanswerable for the stage most likely to have been
-- skipped, and which had in fact already been skipped once unnoticed.
--
-- Idempotent like the rest: DROP IF EXISTS then ADD always ends on the current
-- constraint, whatever the table started with. `migrate()` replays every file
-- in this directory on every run, and 004 sorts after 003, so the inline CHECK
-- 003 creates with the table is always replaced by this one.

-- An earlier revision of this file admitted `apply` as a stage name. Nothing
-- reads it: the overview's 合併 column, and `run_ledger_backfill`, have always
-- called that artifact `merge`. Any row already filed under the old name is
-- carried across before the constraint narrows -- both because the constraint
-- cannot be added while a violating row exists, and because the run really did
-- happen and deleting it would lose that.
UPDATE wang_knowledge.pipeline_runs SET stage = 'merge' WHERE stage = 'apply';

ALTER TABLE wang_knowledge.pipeline_runs
    DROP CONSTRAINT IF EXISTS pipeline_runs_stage_check;

ALTER TABLE wang_knowledge.pipeline_runs
    ADD CONSTRAINT pipeline_runs_stage_check
    CHECK (stage IN ('extraction', 'cross_section', 'review', 'adjudication',
                     'merge', 'ingest', 'article'));
