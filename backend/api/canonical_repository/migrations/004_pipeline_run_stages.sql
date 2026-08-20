-- Widen `pipeline_runs.stage` to the stages the pipeline actually has.
--
-- 003 listed six stages, and two of the runners in the chain had no name among
-- them: `cross_section`, which puts back the relations that sectioned
-- extraction necessarily splits, and `apply`, which turns the adjudicator's
-- overrides into the reviewed candidate package. A run with no stage name is a
-- run the overview cannot show, so the one question the panel exists to answer
-- -- where is this source stuck -- was unanswerable for exactly the two stages
-- most likely to have been skipped.
--
-- Idempotent like the rest: DROP IF EXISTS then ADD always ends on the current
-- constraint, whatever the table started with. `migrate()` replays every file
-- in this directory on every run, and 004 sorts after 003, so the inline CHECK
-- 003 creates with the table is always replaced by this one.

ALTER TABLE wang_knowledge.pipeline_runs
    DROP CONSTRAINT IF EXISTS pipeline_runs_stage_check;

ALTER TABLE wang_knowledge.pipeline_runs
    ADD CONSTRAINT pipeline_runs_stage_check
    CHECK (stage IN ('extraction', 'cross_section', 'review', 'adjudication',
                     'apply', 'merge', 'ingest', 'article'));
