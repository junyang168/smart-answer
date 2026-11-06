CREATE SCHEMA IF NOT EXISTS media_ingest;
CREATE TABLE IF NOT EXISTS media_ingest.batches (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
 revision integer NOT NULL DEFAULT 1, desired text NOT NULL DEFAULT 'running',
 config jsonb NOT NULL, reserved_usd numeric NOT NULL DEFAULT 0,
 calls integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media_ingest.recordings (
 id text PRIMARY KEY, pcm_sha text, source_sha text NOT NULL,
 item text NOT NULL UNIQUE, info jsonb NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS recording_pcm ON media_ingest.recordings(pcm_sha) WHERE pcm_sha IS NOT NULL;
CREATE TABLE IF NOT EXISTS media_ingest.assets (
 sha text PRIMARY KEY, recording text REFERENCES media_ingest.recordings(id), info jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS media_ingest.tasks (
 id text PRIMARY KEY, recording text NOT NULL REFERENCES media_ingest.recordings(id),
 stage text NOT NULL, unit text NOT NULL DEFAULT 'all', input_sha text NOT NULL,
 state text NOT NULL DEFAULT 'pending', attempt integer NOT NULL DEFAULT 0,
 dependencies text[] NOT NULL DEFAULT '{}', payload jsonb NOT NULL DEFAULT '{}',
 result jsonb, worker text, heartbeat timestamptz, progress jsonb NOT NULL DEFAULT '{}',
 not_before timestamptz NOT NULL DEFAULT now(),
 error text, updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(recording,stage,unit,input_sha)
);
CREATE TABLE IF NOT EXISTS media_ingest.batch_tasks (
 batch text REFERENCES media_ingest.batches(id), task text REFERENCES media_ingest.tasks(id),
 PRIMARY KEY(batch,task)
);
CREATE INDEX IF NOT EXISTS task_queue ON media_ingest.tasks(state, updated_at);
CREATE INDEX IF NOT EXISTS task_recording ON media_ingest.tasks(recording);
CREATE TABLE IF NOT EXISTS media_ingest.calls (
 id text PRIMARY KEY, batch text NOT NULL REFERENCES media_ingest.batches(id),
 task text NOT NULL REFERENCES media_ingest.tasks(id), model text NOT NULL,
 reserved_usd numeric NOT NULL, state text NOT NULL, info jsonb NOT NULL DEFAULT '{}',
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS media_ingest.controls (
 batch text REFERENCES media_ingest.batches(id), operation text NOT NULL,
 request jsonb NOT NULL, revision integer NOT NULL, PRIMARY KEY(batch,operation)
);

CREATE INDEX IF NOT EXISTS asset_pcm_lookup ON media_ingest.assets ((info->>'pcm_sha'));
CREATE INDEX IF NOT EXISTS legacy_duration_lookup ON media_ingest.recordings (((info->>'duration')::double precision)) WHERE info->>'existing_transcript'='true';

ALTER TABLE media_ingest.tasks ADD COLUMN IF NOT EXISTS not_before timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS media_ingest.disk_reservations (
 task text NOT NULL REFERENCES media_ingest.tasks(id), attempt integer NOT NULL,
 device text NOT NULL, path text NOT NULL, bytes bigint NOT NULL CHECK (bytes>=0),
 floor_bytes bigint NOT NULL, limit_bytes bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(task,attempt,device)
);
CREATE INDEX IF NOT EXISTS disk_reservation_device ON media_ingest.disk_reservations(device);
