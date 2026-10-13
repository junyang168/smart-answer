# Reviewed first-layer manifest → second-layer grouping

`python -m backend.pipeline.exegesis_passage_grouping_job --help`

This adapter consumes the sealed, semantically accepted first-layer passage
manifest without rerunning L1 or relocalizing Claims. Whole frozen Claim packets,
read-only primary ownership, source/revision/SHA and secondary evidence remain
in the output. This is Matthew-only, not a completion claim for all 3,843 Claims.

Whole units <=20 become one deterministic group, backed by the accepted L1
membership. Units >20 use the existing `split_reviewed_unit` implementation and
argument-boundary prompt with all unit members. Matthew 16:19 regression context
is used only for that actual passage. No fixed member count or mechanical split.

Default: three bounded parallel passage workers, `claude-opus-5-5/high` split,
`gpt-6.1-sol/high` independent per-passage group review. The exact requested
Claude model is probed through subscription CLI and its returned model IDs are
checked before work. No downgrade or API fallback. Parallel workers hold separate
output directories and each performs split then review sequentially.

Every call checks complete request bytes and saves raw answers before parsing.
Failures prevent dispatch of additional passages; already active workers finish
and preserve their artifacts. No retry loop. Root and shared-parent L2 locks
reject duplicate jobs. Identical fingerprints reuse verified successes on resume;
incomplete attempts remain for explicit inspection/recovery. Changed code,
model or input requires a new root.

`status.json`, `events.jsonl`, per-unit artifacts, raw model/reviewer calls,
`manifest.json`, `validation-report.json` and failure files record progress.
Run detached from the #411 worktree with explicit manifest, sources, output root,
CLI paths, model and workers. No Claim/Registry writes, no CVP calls, no #357
processing. L1 delivery and its original reviews are never overwritten.

Pass `--direct-preparation` to reuse sealed direct groups from a previous
preparation. The L1 SHA, every unit result, exact membership and counts must
match; imported result SHAs are retained in validation bases.

`split_reviewed_unit` pins `scope_label` to the reviewed unit ID in both payload
and schema. `--recover-scope-root` supports a narrowly bounded recovery of saved
raw splits rejected solely for scope label mismatch: verifies parent model/L1,
request/response SHA, exact original unit and membership, then normalizes only
that label. Group IDs, members, rationale and ordering must remain identical.
New root retains normalization provenance and still performs independent review;
no model split is repeated and the original failed root remains immutable.

L2 reviewer findings now use a schema enum of the exact group keys and an exact
finding count; the prompt explicitly keeps frozen L1 units/primary outside the
review scope. `--prior-review-root` can import saved legacy reviews: only a
`group:` key prefix is normalized, all per-group semantic findings and evidence
are unchanged, and extra unit/proposal findings are retained separately.
Bindings and originals are verified; semantic objections remain objections.

A `needs_resolution` review triggers one Opus5.5/high correction of the entire
passage, with all Claim members and SHA-checked complete original prose for its
sources (SVG excluded). The correction records argument continuity and cross-group
support in rationale without new Claim/Registry links. GPT6.1/high then performs
one terminal independent review. There is no second semantic correction loop;
remaining objections are saved explicitly. Recovered initial reviews are not
called again. Each worker reports original-source-correction/final-review phases.
