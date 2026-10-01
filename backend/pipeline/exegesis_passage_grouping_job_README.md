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

Default: three bounded parallel passage workers, `claude-opus-5-1/high` split,
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
