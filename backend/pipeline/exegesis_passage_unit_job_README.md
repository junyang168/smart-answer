# #411 first-layer background job

`python -m backend.pipeline.exegesis_passage_unit_job --help`

This job only produces passage units. Every model stage receives the complete
book Claim set and complete current proposal in one request. It never runs L2
grouping, CVP processing, or Claim/Registry writes. Current Matthew input is the
973-Claim candidate set, not a completion claim for all 3,843 eligible Claims.

Use `--initial-proposal` with the existing sealed whole-book manifest to skip
generation. The completed 973-Claim/110-unit Matthew proposal is reused in the
current job; its original model provenance remains recorded. Changing the model
for future calls never requires regenerating an already completed proposal.

Generation, correction and disputed arbitration use `gpt-6.1-sol/high`.
Independent initial and final reviews use `claude-opus-5-5/high`. They review
whole-book passage boundaries and complete membership only. There are no
`primary_reviews`, ownership rewriting, or blanket re-localization tasks.
Correction/arbitration schemas prohibit returning a new primary. Existing
primary ranges can only expose a passage membership/range conflict. There is one
worker and one model call at a time. At most one semantic correction and one
disputed arbitration run. A terminal original-source closeout examines final
outstanding items; remaining disagreements are disclosed for human disposition.

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
`validation-report.json` are final outputs. This job reports L1 semantic review
and preserves input primary ownership; it does not issue new ownership or L2
processing authorization and never starts L2.


## Terminal original-source closeout

After final review, the job reopens SHA-checked physical originals for outstanding
findings and citation errors, including three adjacent rows/blocks on each side.
SVG boundaries remain separate text parts; an unmatched frozen fragment is only
a retrieval hint. The complete book Claim set and proposal remain in every model
request. There is no second primary-location job.

Provable already-applied moves/ranges and physically correct citations omitted
from the review packet receive explicit deterministic dispositions. Other cases
receive at most one GPT 6.1/high original-source adjudication with verbatim
support, bounded edits, or a concrete unresolved explanation. Citation repairs
only replace the mismatched quote at its original source/location/index. They
never overwrite or relabel the independent model's saved response.

Any member/range edit, or rejection of a semantic review finding while retaining
the current plan, requires one Opus 5.5/high whole-book final review. This review
is terminal: remaining findings and explicit unresolved source dispositions go
to the human list. Citation-only repairs and provable no-ops use derived program
validation. No further correction/review loop is started.

Artifacts: `original-source-context.json`, `original-source-disposition.json`,
`original-source-closeout-result.json`, and `source-closeout-validation.json`
(or `source-closeout-final-review/` for semantic changes). Every final manifest
binds both the raw final-review SHA and the separate disposition SHA. Closeout
code joins the resumability fingerprint; changed code requires a fresh output
root and never overwrites previous deliveries.
