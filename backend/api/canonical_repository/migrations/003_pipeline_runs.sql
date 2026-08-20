-- What each pipeline run cost, produced, and how good the result was.
--
-- Before this table the only record of a run was the artifact it happened to
-- leave on disk, and those are scattered over seven staging layouts: 28
-- extraction packages covering 19 sources, one source sitting in two research
-- batches, one manuscript present under three SHAs in four directories. A
-- panel that scans directories starts lying the moment someone writes a batch
-- into an eighth. So the overview reads this table, and the runners write it.
--
-- Token usage was already measured (`llm_usage.usage_row`) and then printed to
-- stdout, which is where it died. `cost_usd` is that usage priced at write
-- time; `price_version` records which table priced it so a later price change
-- does not silently rewrite history.
--
-- Idempotent, like 001 and 002: `migrate()` replays every file in this
-- directory on every run.

CREATE TABLE IF NOT EXISTS wang_knowledge.pipeline_runs (
    run_id            text PRIMARY KEY,
    batch_id          text,

    -- Sermons and articles are separate pipelines and the relation between
    -- them is many-to-many: the Matt 16:13-20 article cites eight sources, and
    -- one sermon can feed several articles. A bare `source_id` could not
    -- record an article run at all, so the subject is what was run and
    -- `source_ids` is every source it touched. The sermon overview projects a
    -- draft's run back onto each cited source's row through the GIN index.
    subject_kind      text NOT NULL DEFAULT 'source'
                        CHECK (subject_kind IN ('source', 'draft', 'batch')),
    subject_id        text NOT NULL,
    source_ids        text[] NOT NULL DEFAULT '{}',

    stage             text NOT NULL
                        CHECK (stage IN ('extraction', 'review', 'adjudication',
                                         'merge', 'ingest', 'article')),

    -- CLI runs write here too. Every piece of work on this corpus is currently
    -- started from a terminal; a ledger that only recorded panel-triggered
    -- runs would show an empty table while the machine was busy.
    trigger           text NOT NULL CHECK (trigger IN ('cli', 'panel')),
    triggered_by      text,

    status            text NOT NULL
                        CHECK (status IN ('queued', 'running', 'succeeded',
                                          'failed', 'cancelled', 'interrupted')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,

    -- A deploy is `launchctl unload` on the API job, and an extraction takes
    -- 5-10 minutes. Without a heartbeat a killed run stays `running` forever
    -- and the table claims work is in progress that nothing is doing.
    heartbeat_at      timestamptz NOT NULL DEFAULT now(),
    cancel_requested  boolean NOT NULL DEFAULT false,

    model_id          text,
    usage             jsonb NOT NULL DEFAULT '[]'::jsonb,
    cost_usd          numeric(10, 4),
    price_version     text,

    -- The stage's own quality measure, snapshotted at completion. Not a score
    -- invented here: the sentence ledger's accounting, the review's routing
    -- summary, the adjudication's human-disagreement count.
    quality           jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Which inputs this run actually read, so a later read can tell "current"
    -- from "stale" instead of only "exists".
    input_sha256      jsonb NOT NULL DEFAULT '{}'::jsonb,

    output_paths      text[] NOT NULL DEFAULT '{}',
    command           text,
    error_message     text,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS pipeline_runs_subject_stage_idx
    ON wang_knowledge.pipeline_runs (subject_id, stage, started_at DESC);

CREATE INDEX IF NOT EXISTS pipeline_runs_source_ids_idx
    ON wang_knowledge.pipeline_runs USING gin (source_ids);

CREATE INDEX IF NOT EXISTS pipeline_runs_live_idx
    ON wang_knowledge.pipeline_runs (status, heartbeat_at)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS pipeline_runs_started_at_idx
    ON wang_knowledge.pipeline_runs (started_at DESC);
