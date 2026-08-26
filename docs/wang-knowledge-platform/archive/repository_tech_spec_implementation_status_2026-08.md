# 文库 Technical Specification 实施状态（截至 2026-08-16）

> **读者**：Developer
> **类型**：记录
> **状态**：历史记录，只说明写下当时
> **与代码对齐**：不适用
> **权威范围**：无。

本节原是《文库 Technical Specification》的「19. Implementation Status」。一份规范里追加一段变更日志，
会和它自己的规范部分打架：同一份文档的 §16 Implementation Phases 把 Phase 1 写成待办，§19 却说
Phase 1 已经在代码里。规范说该怎么做，状态说此刻做到哪，两者的更新节奏不同，不该同居一处。原文照录。

## 19. Implementation Status

This section describes implemented repository functionality only. The formal shared-knowledge authoring store and package importer are now implemented, but the complete original-language review workflow, evolving thought map, Publication Profile workflow, AnswerEvidenceBundle service, compiled knowledge build, and knowledge-grounded QA described above remain target architecture unless explicitly listed below.

Phase 1 foundation is now represented in code under `backend/api/canonical_repository`:

* typed canonical unit, source, source-map, citation, relationship, and resolution records;
* atomic JSON authoring storage;
* deterministic transcript paragraph/line/time mapping and notes page/OCR mapping;
* exact-substring citation creation with stale-source detection;
* public lookup and admin rebuild/edit/build endpoints;
* atomic compiled Bible/topic JSON and SQLite read models; and
* automatic best-effort source-map refresh when a transcript is imported or notes Unified Input is rebuilt;
* an admin preview list at `/admin/canonical-repository`, with candidate-only seed import, passage/topic views, status badges, filtering, source counts, and manuscript Project links;
* a unit review page for title/type/reference/topic editing, citation approval, deterministic source-map citation creation, publish validation, and preview-before-apply merges;
* a public Bible/topic repository shell that reads only an activated build and never exposes candidates;
* a notes source reader showing the exact scanned page beside highlighted OCR, with stale-source warnings and authenticated-reader gating;
* exact `verbatim_source_excerpt` generation and validation in new Transcript Evidence Inventories, with safe full-range compatibility for legacy inventories;
* cross-lecture integration patches that retain evidence IDs, source ranges, verbatim excerpts, source document IDs, and citation IDs;
* Markdown rendering of the linked manuscript section in the admin unit review page;
* embedded audio/video players above sermon excerpts, with citation-time seeking and new-tab links to the complete sermon;
* source-title resolution from the original sermon record rather than the derived manuscript Project title;
* editor-only sermon right-rail lists of all citing passage and topic units, including unpublished statuses, using the `source_origin_id` lineage filter;
* non-destructive heading-only citation cleanup and prevention of future heading-only source cards; and
* Bible index grouping by canonical book and chapter, followed by verse-order sorting and per-unit deduplication across multiple Bible references.

The shared-knowledge authoring foundation is now represented by `knowledge_models.py`, `knowledge_importer.py`, and the `RepositoryStore.knowledge_*` methods:

* versioned atomic records for Source Fragment, Question, Observation, Claim, authoritative Topic Node, Evidence Step, evidence and claim relations, external Position, Knowledge Route, Editorial Synthesis, Composition Plan/Decision, Editorial Check, and Tension;
* full-package validation before writes, including duplicate IDs and unresolved source, evidence, claim, position, route, synthesis, and composition references;
* Canonical Citation binding before import, with exact-text/hash validation and approval gates for claims/evidence lacking a valid citation;
* explicit topic reconciliation: taxonomy nodes are authoritative, candidate aliases may migrate, analysis routes gain canonical foreign keys, and old search IDs remain projections mapped to CanonicalUnit;
* incremental package imports that may reference records already present in the canonical repository;
* idempotent imports with SHA256 package manifests and protection for existing human review fields;
* revision-guarded record updates for concurrent editorial work; and
* admin endpoints for package import, collection status, collection reads, individual reads, and revision-guarded updates.

Authoring records are stored under `canonical_repository/knowledge/<collection>/<record-id>.json`; package manifests are stored under `canonical_repository/knowledge/packages/`. These are internal authoring records. They are not included in the public active build until a later compiler and publication gate explicitly approves them.

Topic reconciliation is written to `canonical_repository/knowledge/reconciliation/topic_identity.json` and can be rerun through `POST /admin/canonical-repository/knowledge/topics/reconcile`. The report must remain empty for unknown unit topics and unresolved `topic_research` routes before a topic build is activated.

The Matthew pilot migration is implemented by `backend/pipeline/canonical_repository_pilot.py`. It attaches multi-lecture citations to the Amen, dispensationalism, and Transfiguration units, and creates a separate cross-passage `小信` concept unit related to—rather than replacing—the individual passage units.

The sermon reader accepts a citation deep link, highlights the exact original excerpt, scrolls it into view, and seeks authenticated audio/video to the citation start time. The public unit page currently hides manuscript text intentionally and presents approved original sources first; the admin unit page still renders the manuscript Markdown for editorial comparison. The remaining release work is primarily editorial: additional candidate citations must be reviewed, units moved to `published`, and the repository expanded beyond the Matthew pilot. Wider corpus migration remains incremental because notes without Evidence Inventory ranges require human source selection rather than guessed provenance.
