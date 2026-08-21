-- Let the ledger record `sectioning`.
--
-- 90 of 115 published transcripts carry no headings, so where they break is
-- decided by a model call: the same subtitle generator the sermon editor uses.
-- That call was the one model call in the pipeline that wrote no row at all,
-- which is why the staged cost table for 馬太福音釋經（四）1 reads $4.4568 and is
-- incomplete -- the call that decided the source was cut into six sections is
-- not in it.
--
-- It is not a usage row on the extraction it feeds: the section plan is an input
-- to the extraction's fingerprint, so it is computed before that run exists, and
-- it is cached per source hash while extraction may re-run many times over the
-- same plan. The editor also makes the call with no extraction anywhere in
-- sight. So it gets a stage of its own, and `run_ledger.STAGES` has to agree.
--
-- Idempotent, like 001-003: `migrate()` replays every file in this directory on
-- every run, so the constraint is dropped by name before it is added.

ALTER TABLE wang_knowledge.pipeline_runs
    DROP CONSTRAINT IF EXISTS pipeline_runs_stage_check;

ALTER TABLE wang_knowledge.pipeline_runs
    ADD CONSTRAINT pipeline_runs_stage_check
    CHECK (stage IN ('sectioning', 'extraction', 'review', 'adjudication',
                     'merge', 'ingest', 'article'));
