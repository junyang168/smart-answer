# #411 first-layer background job

`python -m backend.pipeline.exegesis_passage_unit_job --help`

This job only produces passage units. Every model stage receives the complete
book Claim set and complete current proposal in one request. It never runs L2
grouping, CVP processing, or Claim/Registry writes. Current Matthew input is the
973-Claim candidate set, not a completion claim for all 3,843 eligible Claims.

Generation, correction and disputed arbitration use `gpt-6.1-sol/high`.
Independent initial and final reviews use `claude-opus-5-5/high`. There is one
worker and one model call at a time. At most one semantic correction and one
disputed arbitration run; final unresolved items are disclosed explicitly.

Inputs: sealed whole-book statement-first request, alias map, physical source
map, formal #409 v8 ledger and frozen role packet. Generation keeps audited
primary distinct from candidates. Independent review reopens SHA-checked
originals, deduplicates physically verified exact excerpts, and retrieves
explicitly requested original locations for correction/final review. No SVG,
source summary or capacity truncation is supplied as evidence. Normalized
frozen excerpts that fail physical matching remain retrieval gaps.

Preflight measures the complete prompt/schema/payload. Defaults: generation
and GPT stages 2,500,000 bytes plus the CLI's 1,048,576 character limit;
whole-book Opus review 2,000,000 bytes. These are explicit transport limits,
not permission to split a book or omit Claims. Raw transport responses are
saved before parsing. Byte overflow, source drift and failures stop with
artifacts intact.

Use `backend/.venv/bin/python` from the #411 worktree. Pass all paths explicitly,
including `--codex-executable` for the subscription CLI. Launch using a detached
process with stdout/stderr redirected to an exclusive log in a new output root.
Monitor `status.json`, `events.jsonl`, the log, and the PID recorded in status.
Use `backend/.venv/bin/python scripts/monitor-exegesis-passage-job.py OUTPUT_ROOT`
for a read-only foreground watcher, or add `--once` for a status snapshot.
A shared output-parent lock and output lock reject duplicate jobs.

Resume with the same arguments and output root. Input/model/prompt/code-bound
successful responses and reviews are reused; changed bindings fail closed.
An incomplete model stage is preserved and requires inspection before recovery;
resume never silently retries it or replaces its output. No automatic infinite
retry occurs. `passage-unit-manifest.json`, `unresolved.json` and
`validation-report.json` are final outputs. A passing manifest makes L2 eligible
but this job does not start it.
