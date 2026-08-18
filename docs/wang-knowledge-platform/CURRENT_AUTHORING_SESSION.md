# Matthew exposition current authoring session

Updated: 2026-08-16 (America/Chicago)

## Project boundary

This session belongs to Wang Knowledge Platform, not notes-to-sermon-agent. Do not run notes-to-sermon fidelity audit, source reconstruction, or article generation outside the Matthew exposition runner.

## Completed articles

- Article 1, Matt.16.1–12: editorial pass, Program Audit, human approval and repository publication completed. Published manuscript SHA: `c71a6da593b0c8c9093f152282a3b4ee562c60f98754915613ac74ba7173502a`.
- Article 2, Matt.16.13–20: multi-agent authoring, technical audit, SHA-bound human approval and repository publication completed. It is publicly listed as `matthew-16-13-20`; its approval is independent of Article 1.
- Article 3, Matt.16.21–23: Author Agent, two revision rounds, Program Audit, automated publication decision and repository publication completed. Its diagnostic run used a now-retired score-gap call; do not copy that call into a new article workflow. Published manuscript SHA: `342fa88d5af7c339174bd82a301f0e204f3fd650962029024c01d35c9e97c0d7`; editorial score 90; Program Audit `pass`, 0 errors and 0 warnings; public slug `matthew-16-21-23`.

The Article 3 runtime artifacts are present under the canonical Wang platform repository at `$DATA_BASE_DIR/wang-knowledge-platform/repository`. The production backend at `/opt/homebrew/var/www/smart-answer` was explicitly authorized and cut over to this canonical repository on 2026-08-16; it lists all three articles and preserves their reader-visible Markdown SHAs. The legacy `$DATA_BASE_DIR/wang_repository` path has been archived and deleted. Do not work around the automated publication policy by labeling an automated decision as human approval.

## Repository integration

The automated editorial-review, Program Audit and publication workflow was merged to GitHub `main` through PR #2 (`Optimize Matthew editorial workflow`). Merge commit: `ba7850527de1432f94016f28195ff56e8449851b`. The local checkout and `origin/main` contain the change. The later Wang path cutover deployed only the five audited backend path files from the working tree; it did not push or broadly deploy unrelated uncommitted changes.

WKP-F10.2 recovery and path reconciliation was later merged through PR #27 and deployed as immutable release `92d899e1dd6d2179866f76b495fe365f9b02f9a1`. PostgreSQL remains the authoring authority; no Wang knowledge payload, corpus survey data, staging generation, source manifest, research runner packet, media, or professor-content fixture entered Git. The production sermon catalog and overrides now load from `$DATA_BASE_DIR/wang-knowledge-platform/catalog/`; their two old root/config copies were SHA-verified, archived with a successful restore test, and removed. The deployment report is `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/sermon-catalog-path-cutover-20260817.json`.

## Corpus survey authority

The 205-transcript corpus survey is an important broad knowledge map, not disposable scratch output. Its versioned research release is `$DATA_BASE_DIR/wang-knowledge-platform/repository/research_corpus_snapshots/CORPUS-SURVEY-205-V1/`; all 265 detailed artifacts remain under canonical `staging/corpus-survey/` with a SHA manifest and recovery archive. PostgreSQL stores the project-owner-approved candidate structure: eight revisable primary domains, three cross-cutting axes, 17 reviewed grouping resolutions, and four human-reviewed comparison decisions. This approval is structural only; the 3,752 survey claims remain candidate and must not be presented as approved claims.

The corpus survey is a one-time, closed 205-transcript historical survey. Do not add later sermons, follow later transcript revisions, regenerate its 205 cards, or build a rolling V2 from it. Source SHA differences discovered after the survey are provenance facts, not refresh instructions. The SHA-bound closure policy is `repository/research_corpus_snapshots/CORPUS-SURVEY-205-V1/closure-policy.json` (SHA-256 `3957c536bfb34c521f0da850e69816eaa9717668136ae10a840cae3fa11c1e1c`). On 2026-08-16 an attempted three-card refresh and two independent reviews were withdrawn after this boundary was clarified. The original cards were restored byte-for-byte from the V1 generation archive; all 265 canonical staging files again match the immutable V1 SHA manifest. Withdrawn exploratory artifacts are retained only for audit under `staging/corpus-survey/withdrawn-generations/2026-08-16-partial-refresh/` and must not be read as repository or PostgreSQL authority.

No cron, launchd, API, or web invocation writes this survey automatically. The closure and legacy-copy audit is `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/corpus-survey-closure-audit-20260816.json` (SHA-256 `e651aae1a378729d194443a1752aacc1460abecd1267538a5715dc197b286d32`). After the user authorized continuation of the two exact legacy-removal candidates, both 265-file `output/corpus-survey` copies were removed from their original paths and placed together in the recoverable system Trash directory `/Users/junyang/.Trash/corpus-survey-legacy-20260816-175900/`. No `output/corpus-survey` directory remains in the scanned worktrees, developer checkout, or production roots. The execution report is `$DATA_BASE_DIR/wang-knowledge-platform/deployment-reports/corpus-survey-legacy-removal-20260816.json` (SHA-256 `c7d1e729b5370f336be6370277d0d468f5750da644560cdb5ce663e61f38d968`).

## Article 3 artifacts

- Final published manuscript: `$DATA_BASE_DIR/wang-knowledge-platform/repository/editorial_drafts/DRAFT-M16-003-V1/manuscript.md`
- Publication-bound editorial review: `$DATA_BASE_DIR/wang-knowledge-platform/repository/editorial_drafts/DRAFT-M16-003-V1/publication-editorial-review.json`
- Program Audit: `$DATA_BASE_DIR/wang-knowledge-platform/repository/editorial_drafts/DRAFT-M16-003-V1/program-audit.json`
- Program Audit staging artifacts: `$DATA_BASE_DIR/wang-knowledge-platform/staging/claim-layer/matthew-16-21-23-sources/authoring-v1/round-02/program-audit/`

## Publication rule

Matthew exposition articles now publish automatically when the program verifies that every applicable rubric dimension reached its own minimum, that no hard failure was declared, and that the Program Audit is `pass` or `pass_with_warnings` with zero errors. The dimension minimums live in the quality profile (revision 4: 80% of each weight); no total score gates publication. The workflow creates `automated-publication-decision.v1`; it must not claim human approval. Repository publication is part of the authoring workflow, but source-code push and production deployment remain separate operations.

For a new article, start from its existing fast-passage CompositionPlan and knowledge snapshot, confirm the article's authoring contract on that plan (base source, required argument steps, allowed/ineligible operations), and invoke `backend.pipeline.matthew_exposition_authoring_runner` with `--plan-id <CompositionPlan id>`, `--program-audit-manifest`, `--program-audit-draft-id`, `--auto-accept-maintained-findings`, and `--max-revision-rounds 2`.

The authoring contract now lives on the CompositionPlan in PostgreSQL, not in a `base-manuscript-contract-input.json` beside the staging artifacts. `--plan` / `--base-contract` still read those files for articles not yet migrated, and are mutually exclusive with `--plan-id`. Migrate an existing contract with `backend.pipeline.authoring_contract_migration`, which verifies every `source_excerpt` is still a verbatim substring of the manuscript it names before writing anything.

Reviewer-call invariant: one Independent Editorial Review for the initial draft, then exactly one Final Delta Review per revision. A Delta Review must return any next-round findings in the same response. Never add a Score-Gap Review or send a revised manuscript back through full Editorial Review.
