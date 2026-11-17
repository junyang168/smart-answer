-- 同工 review of golden-case answers. Lives in the new knowledge database only.
CREATE SCHEMA IF NOT EXISTS argument_review;

CREATE TABLE IF NOT EXISTS argument_review.questions (
    question_id text PRIMARY KEY,
    lecture text NOT NULL,
    group_id text NOT NULL,
    item_id text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    cases_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lecture, group_id, item_id)
);

-- One active answer per reviewer per question; a changed answer supersedes, never overwrites.
CREATE TABLE IF NOT EXISTS argument_review.answers (
    answer_id bigserial PRIMARY KEY,
    question_id text NOT NULL REFERENCES argument_review.questions(question_id),
    reviewer text NOT NULL,
    choice text,
    opinion text,
    created_at timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz,
    CHECK (choice IS NOT NULL OR opinion IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS answers_one_active
    ON argument_review.answers (question_id, reviewer) WHERE superseded_at IS NULL;

-- A model's confirmation or clarifying question about a free-text opinion, and the reply.
CREATE TABLE IF NOT EXISTS argument_review.followups (
    followup_id bigserial PRIMARY KEY,
    answer_id bigint NOT NULL REFERENCES argument_review.answers(answer_id),
    kind text NOT NULL CHECK (kind IN ('confirm', 'ask')),
    text text NOT NULL,
    suggested_choice text,
    model text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    reply_choice text,
    reply_text text,
    replied_at timestamptz
);

-- The owner's ruling where reviewers have no majority or an opinion stayed unclear.
CREATE TABLE IF NOT EXISTS argument_review.rulings (
    question_id text PRIMARY KEY REFERENCES argument_review.questions(question_id),
    ruler text NOT NULL,
    choice text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- A question can be set aside (e.g. replaced by a rule question) without losing its answers.
ALTER TABLE argument_review.questions ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
