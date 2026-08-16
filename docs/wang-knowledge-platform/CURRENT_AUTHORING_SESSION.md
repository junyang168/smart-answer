# Matthew exposition current authoring session

Updated: 2026-08-15 (America/Chicago)

## Project boundary

This session belongs to Wang Knowledge Platform, not notes-to-sermon-agent. Do not run notes-to-sermon fidelity audit, source reconstruction, or article generation outside the Matthew exposition runner.

## Completed articles

- Article 1, Matt.16.1–12: editorial pass, Program Audit, human approval and repository publication completed. Published manuscript SHA: `c71a6da593b0c8c9093f152282a3b4ee562c60f98754915613ac74ba7173502a`.
- Article 2, Matt.16.13–20: multi-agent authoring and technical staging completed; it has no inherited publication approval from Article 1.
- Article 3, Matt.16.21–23: Author Agent, two revision rounds, Program Audit, automated publication decision and repository publication completed. Its diagnostic run used a now-retired score-gap call; do not copy that call into a new article workflow. Published manuscript SHA: `342fa88d5af7c339174bd82a301f0e204f3fd650962029024c01d35c9e97c0d7`; editorial score 90; Program Audit `pass`, 0 errors and 0 warnings; public slug `matthew-16-21-23`.

The Article 3 runtime artifacts are present under Wang repository, but the currently running production backend loads code from `/opt/homebrew/var/www/smart-answer`, not this workspace, and still recognizes only the legacy human decision schema. A later authorized backend deployment is required before the live HTTP/UI process lists the automated decision. Do not work around this by labeling an automated decision as human approval.

## Article 3 artifacts

- Final manuscript: `output/claim-layer/matthew-16-21-23-sources/authoring-v1/round-02/revised-draft.md`
- Publication-bound editorial review: `output/claim-layer/matthew-16-21-23-sources/authoring-v1/round-02/publication-editorial-review.json`
- Program Audit: `output/claim-layer/matthew-16-21-23-sources/authoring-v1/round-02/program-audit/program-audit.json`
- Program Audit staging manifest: `output/claim-layer/matthew-16-21-23-sources/authoring-v1/round-02/program-audit/editorial-draft-manifest.json`

## Publication rule

Matthew exposition articles now publish automatically when the program verifies editorial score >= 90, no hard gates or hard failures, and Program Audit `pass` or `pass_with_warnings` with zero errors. The workflow creates `automated-publication-decision.v1`; it must not claim human approval. Do not push or deploy.

For a new article, start from its existing fast-passage CompositionPlan and knowledge snapshot, prepare the article-specific base contract, and invoke `backend.pipeline.matthew_exposition_authoring_runner` with `--program-audit-manifest`, `--program-audit-draft-id`, `--auto-accept-maintained-findings`, and `--max-revision-rounds 2`.

Reviewer-call invariant: one Independent Editorial Review for the initial draft, then exactly one Final Delta Review per revision. A Delta Review must return any next-round findings in the same response. Never add a Score-Gap Review or send a revised manuscript back through full Editorial Review.
