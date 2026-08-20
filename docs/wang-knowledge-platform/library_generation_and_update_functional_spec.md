# Functional Specification: Library Generation and Update

> Companion: [Functional Specification: Pipeline Operations Console](./pipeline_operations_console_functional_spec.md) — this document defines what the pipeline must guarantee; that one defines how an operator sees and drives it.
>
> Related: [独立 AI 复审](./independent_ai_review_v1.md) · [详细知识抽取工作流](./detailed_knowledge_extraction_workflow_v1.md) · [马太释经多智能体写作工作流](./matthew_exposition_multi_agent_authoring_workflow_v1.md)

## 1. Purpose

The library is a derived work. Nothing in it is authored from nothing: every claim, every relation, every published paragraph descends from a sermon transcript or a reviewed notes manuscript through a chain of extraction, review, adjudication, composition, and editorial authoring.

Producing that chain once is solved. This specification governs the other half — **keeping the library true to its sources as both the sources and the pipeline change over the years the collection is maintained.**

A derived library has exactly one failure mode that matters: an artifact that no longer follows from the source it claims to follow from, while still looking valid. This document defines how that condition is detected, how much of the library it invalidates, and how the minimum necessary work is rebuilt.

## 2. Problem Statement

### 2.1. What already works

A published unit carries a `human-publication-decision.json` bound to three hashes — manuscript, technical audit, editorial review — plus paragraph-level provenance and a knowledge snapshot. The question "on what basis was this published" has an answer, and the answer is machine-checkable.

Extraction, review, and adjudication each record a fingerprint over model, prompt, schema, and source hash. Re-running with an unchanged identity is a no-op; prior generations are archived rather than overwritten.

### 2.2. What does not work

**Change detection stops at the source file's bytes.** `load_knowledge_source_document` raises `source hash mismatch` when a source no longer hashes to what an artifact recorded. That is a guard: it refuses to review a package against a source it was not extracted from. It is not a refresh path. Nothing computes what became stale, what must be rebuilt, or in what order.

**And the guard misses the more common case.** On 2026-08-19, WKP-F01.11 (#102) changed how transcripts are read: text a proofreader struck through with `~~…~~` is no longer treated as spoken. No source file changed. Every hash still matched. All 22 existing packages continued to validate — while having been built under a different reading of the same bytes. The extraction fingerprint covers the prompt, the model, and the schema; it does not cover the code that turns a file into segments and sentences. A semantic change to source interpretation therefore invalidated the corpus silently, and no check in the system could see it.

This is not an oversight to patch. It is the general shape of the problem: **a derived artifact's identity must cover everything that could change its content, and today it covers only some of those things.**

### 2.3. Why this must be settled before scale

| | count |
|---|---:|
| sources in the catalog | 131 |
| sources with any extraction package | 22 |
| packages on the current architecture (v4, sectioned, sentence-audited) | 2 |
| published editorial units | 3 (all Matthew 16) |

Every pipeline conclusion currently rests on two runs. At 131 sources, each pipeline improvement silently produces a cohort of stale artifacts, and the cost of discovering that later is a full re-run of work already paid for.

## 3. Principles

### 3.1. The source is authoritative; everything else is disposable

Extraction packages, reviews, adjudications, applied packages, and knowledge snapshots are caches of reasoning over a source. Any of them may be discarded and rebuilt. Nothing downstream may hold state that cannot be reconstructed from the source plus recorded decisions.

Two things are **not** disposable: human decisions, and the record that a decision was made. A rebuild reproduces derived content; it never silently re-decides something a person decided.

### 3.2. Staleness is computed, never remembered

If answering "does this need rebuilding" requires a person to recall which change landed when, the system has failed. Every artifact records an identity sufficient to answer the question mechanically.

### 3.3. Identity covers meaning, not just inputs

An artifact's fingerprint must change when anything that could change its content changes. That includes the source bytes, the model, the prompt, the response schema — and the **reader**: the code that segments a source, strips soft deletions, splits sentences, and assigns locators. Anything a change to which would produce a different artifact from identical bytes belongs in the identity.

### 3.4. Rebuild is minimal and ordered

A correction to one sermon must not re-run a chapter. The system computes the smallest set of artifacts a change invalidates, in dependency order, with a cost estimate, before spending anything.

### 3.5. A published unit never silently diverges

When a change invalidates knowledge underneath a published unit, the unit is flagged for editorial re-review. It is not auto-republished, and it is not left quietly standing on retracted ground. Divergence is a visible state with an owner.

### 3.6. Rebuilding does not launder a human gate

An artifact that required human confirmation before does so again if the rebuild changes what the person saw. Where the rebuild is content-identical, the prior decision carries forward with its original identity intact and recorded as carried, not as newly made.

## 4. The Dependency Chain

Each stage consumes the artifacts above it and produces an artifact with its own identity.

| Stage | Input | Output | Identity covers |
|---|---|---|---|
| Source reading | source file | segments, sentences, locators | source bytes, **reader version** |
| Extraction | read source | detailed-knowledge package | reading identity, prompt, model, effort, schema, section plan |
| Independent review | package + read source | review artifact | package hash, reading identity, prompt, model |
| Adjudication | package + review + read source | adjudication + overrides | review identity, both prompts, both models |
| Consensus application | package + overrides | applied package | package hash, overrides hash |
| Knowledge store ingestion | applied package | store rows, snapshot | applied package hash |
| Composition planning | store | composition plan | snapshot identity |
| Authoring | plan + store | manuscript | plan identity, prompt, model |
| Editorial review + audit | manuscript | review, audit | manuscript hash |
| Publication decision | manuscript + review + audit | decision record | all three hashes |
| Repository publication | decision | published unit | decision hash |

Two properties of this chain are requirements, not observations:

* **Every edge is a recorded hash, not an implied ordering.** Given any artifact, the system can name the exact upstream artifacts it was built from.
* **The chain is a DAG over sources, not a per-source line.** A composition plan may draw on several sources; a source feeds several plans. Invalidation follows edges, so one corrected sermon can reach several units, and the system must say which.

## 5. Classes of Change and Their Blast Radius

| Change | Detected by | Invalidates |
|---|---|---|
| Source file re-transcribed or corrected | source hash | everything derived from that source |
| Source reading changed (e.g. soft deletion) | **reader version** — new requirement | every artifact built by that reader, across all sources |
| Extraction prompt or schema changed | extraction fingerprint | extractions and everything below |
| Extraction model changed | extraction fingerprint | as above |
| Review prompt or model changed | review fingerprint | reviews, adjudications, applied packages |
| Adjudication prompt or model changed | adjudication fingerprint | adjudications and applied packages |
| Authoring prompt, profile, or rubric changed | authoring identity | manuscripts and their reviews |
| Editorial policy changed (thresholds, gates) | policy version — new requirement | publication decisions, which must be re-evaluated rather than re-derived |

Two entries above are new capabilities this specification requires: a **reader version** and a **policy version**. Both exist today only as code, so both are invisible to every identity in the system.

### 5.1. Not every invalidation is a rebuild

Invalidated means "no longer known to follow from its source," not "known to be wrong." The system distinguishes:

* **Stale** — identity no longer matches; content may or may not differ.
* **Divergent** — rebuilt and the content actually differs.
* **Confirmed** — rebuilt and byte-identical; the new identity is recorded against the same content.

Only divergence propagates further down the chain. A stale artifact that rebuilds identically stops the cascade, which is what keeps a reader-version bump from forcing a rebuild of the entire library.

## 6. Rebuild Semantics

### 6.1. Computing the set

Given one or more changes, the system produces a rebuild plan naming every affected artifact, in dependency order, with:

* why each is included, traced to the change that invalidated it;
* whether it can be reused, recomputed locally, or requires a model call;
* an estimated cost and duration for the model calls;
* which published units the plan can reach.

The plan is producible without spending anything. An operator sees the bill before authorizing it.

### 6.2. What is reused

Not every rebuild is a re-inference. Section caches hold raw per-section extraction responses keyed by source and fingerprint; a rebuild whose section identity is unchanged rebuilds a package from cache with zero model calls. The plan states, per artifact, which of these applies.

### 6.3. Ordering and partial failure

Artifacts rebuild in dependency order. A failure stops that branch and leaves the previous artifact in place — a half-rebuilt chain is never published, and the failure is recorded against the plan rather than lost. Rebuilds are resumable: re-running a plan skips what already succeeded.

### 6.4. Cost control

A plan exceeding a configured ceiling requires explicit confirmation. This is a safety property, not a convenience: the library is 131 sources, review alone runs roughly $0.5 per source, and a rebuild triggered by a reader-version bump can in principle name every artifact in the collection.

## 7. Published Units

When a rebuild reaches a published unit's knowledge:

1. The unit's status becomes **divergent**, recording which claims changed and which change caused it.
2. The published manuscript stays visible and unchanged. Readers are never shown a manuscript that no editorial process has seen.
3. The divergence is queued for editorial review, which decides: re-author, accept the divergence as immaterial, or revert the underlying change.
4. Re-publication follows the normal gate — profile-passing editorial review plus a clean program audit — and produces a new decision record. It never inherits the previous one.

A unit whose sources changed materially and whose manuscript is not re-reviewed is the failure this section exists to prevent. It must be visible as a count an operator can see, not a condition discovered by reading.

## 8. Out of Scope

* The operator interface, run history, and job execution — see the operations console specification.
* Any change to how extraction, review, adjudication, or authoring judge content. This document governs identity, invalidation, and rebuild only.
* Automatic re-publication. Every publication remains gated as it is today.
* Migration of the 26 existing v1/v2 packages. They are stale by definition under the current architecture; whether to rebuild or retire them is an operational decision, informed by this specification but not made by it.

## 9. Acceptance

The specification is satisfied when:

1. Given any artifact, the system names the upstream artifacts it was built from, by hash.
2. A change to the source reader marks every artifact built by the previous reader as stale, without any source file changing.
3. A rebuild plan is producible with zero model calls, and states cost, duration, cache reuse, and reachable published units.
4. A stale artifact that rebuilds byte-identically is recorded as confirmed and stops the cascade.
5. A change reaching a published unit produces a divergent status and a queue entry, never a silent update and never an auto-republication.
6. The number of divergent published units, and the number of artifacts stale against the current pipeline, are both single queries.

Two of these are directly testable against history: #102's reader change must mark all 22 packages stale, and the two current-architecture packages must remain confirmed against it.
