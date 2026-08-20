# Functional Specification: Pipeline Operations Console

> Companion: [Functional Specification: Library Generation and Update](./library_generation_and_update_functional_spec.md) — that document defines what the pipeline must guarantee; this one defines how an operator sees and drives it.
>
> Related: [操作手册](../operations-runbook.md) · [独立 AI 复审](./independent_ai_review_v1.md)

## 1. Purpose

The console is the operator's view of the content pipeline: what state each source is in, what has run, what it cost, what is stale, and what to run next. It also lets an operator run extraction and the stages after it on a chosen sermon or set of sermons, rather than composing a command line.

It is an operations surface, not an editorial one. It answers "what is the machine doing and what should it do next." It does not review claims, edit manuscripts, or approve publication — those live in the existing Wang admin pages and in the review workflow.

## 2. Problem Statement

### 2.1. The pipeline has no operational memory

Every stage runs from a CLI and writes JSON into staging directories whose layout varies by batch — `matthew-16-notes/v3-sections/`, `transcript-sections/`, and so on. To answer "which of the 131 catalog sources have been extracted," an operator scans the filesystem and infers.

A console built on filesystem inference inherits that fragility: it starts lying the first time someone writes a batch into a directory the scanner does not know. What is missing is not a view — it is a **record that a run happened**: what ran, against which source, with which identity, when, for how long, at what cost, and whether it succeeded.

Cost illustrates the gap. Token accounting was under-reporting a 57,000-token review as 1,134 until 2026-08-19, because Anthropic keeps both cache legs out of `input_tokens`. Nobody noticed, because nothing was reading the number.

### 2.2. The existing job pattern cannot carry this work

`series_index_refresh.py` holds status in a module-level dict guarded by a lock, and runs work through FastAPI `BackgroundTasks`. That is appropriate for a 30-second reindex.

Extraction is 5–10 minutes and roughly $0.50 per source; review is comparable. Running that inside the API process means:

* **every deploy kills it** — `scripts/deploy.sh` restarts the backend LaunchAgent, and an in-flight run dies mid-call with the money already spent and no record it existed;
* status vanishes on restart, so a run cannot be inspected after the fact;
* concurrent runs are unbounded, and nothing stops a mistaken selection from starting 131 of them.

### 2.3. Article generation is already half-solved

`/admin/wang` already reports article progress across ten stages via `matthew_exposition_progress.py`. That surface stays. The console links to it rather than reimplementing it; a second progress view over the same data would drift from the first.

## 3. Users

**Operator (editor role).** Runs the pipeline, watches cost, decides what to rebuild. Needs to see the whole collection at once and to act on a subset.

**Reviewer.** Reads the console to find what is ready for review or newly divergent, then leaves for the editorial surfaces. Does not run anything.

Both are authenticated. The frontend enforces the `editor` role today; the backend currently has no admin dependency on any route. An endpoint that spends money must not rely on the network being private.

## 4. Views

### 4.1. Source inventory

One row per catalog source — 131 today — with the columns that let an operator find work:

* identity: title, category (NYSC, Dallas HLC, notes-to-manuscript, …), series and lecture, passage coverage;
* stage state: extracted / reviewed / adjudicated / applied / ingested / composed / authored / published, each as *absent*, *current*, *stale*, or *failed*;
* the identity each stage ran under, so a stale state can be explained rather than merely displayed;
* source coverage: the share of substantive prose that reached the claim layer, which is the existing measure of whether an extraction actually read the source.

Filterable by state, category, series, and passage. Sortable by staleness and by last run. The inventory is the console's home.

### 4.2. Run history

Every run, newest first: source, stage, status, start and finish, duration, model, tokens (including cache read and write legs), cost, artifact path, and on failure the error and the validation feedback that accompanied it. Retried attempts appear as their own rows — a rejected attempt is billed and must be visible.

Filterable by source, stage, status, and date. Totals per day and per stage, because the useful question is usually "what did this week cost."

### 4.3. Staleness queue

Reads the invalidation model defined by the library specification: artifacts stale against the current pipeline, published units currently divergent, and for each the change that caused it. Each entry offers the rebuild plan for that artifact, with cost, before anything runs.

### 4.4. Cost

Spend by day, stage, and model, against a configured ceiling. A projection for any pending plan. This view exists because the collection is 131 sources and a full pass is a four-figure decision, not a click.

## 5. Actions

### 5.1. Selective run

An operator selects one or more sources from the inventory, chooses a stage, and sees, before confirming:

* how many runs the selection implies, and which sources are already current at that stage (excluded by default, includable deliberately);
* the estimated cost and duration, derived from recorded history for that stage rather than a guess;
* which runs can be served from the section cache with no model call.

Confirmation enqueues. Nothing is charged before confirmation.

### 5.2. Cancel

A queued run is removable. A running one is cancellable at the next stage boundary; a model call already in flight is billed and the run is recorded as cancelled after it returns. Cancellation never leaves a partial artifact in place of a complete one.

### 5.3. Rebuild from a plan

The staleness queue's plans are enqueued the same way, with the same confirmation, so there is one path into the queue rather than two.

## 6. The Run Ledger

The ledger is the console's foundation and its hardest requirement.

**Every run is recorded, whoever started it.** Runs launched from a CLI are recorded exactly as runs launched from the console. This is not a nicety: all work to date is CLI work, and a ledger that only sees console-launched runs would show an empty pipeline while the pipeline is busy. The recording belongs to the runners, not to the API.

Each row carries: source, stage, trigger (cli / console / rebuild plan), status, timings, model and effort, the artifact identity produced, token usage per attempt, cost, output artifact path, and on failure the error. Rows are append-only. A run that produced no artifact is still a row.

**Durability.** The ledger survives a backend restart and a deploy. Given that the authoring store is already PostgreSQL, that is where it belongs; a JSON file in staging would reintroduce the problem the ledger exists to solve.

## 7. Execution Requirements

* **Out of process.** Runs execute outside the API process, so a deploy restart does not kill work in flight. A run interrupted by a machine restart is recoverable — resumed or recorded as interrupted, never left claiming to be running.
* **Bounded concurrency.** A configured maximum of simultaneous runs. Exceeding it queues rather than starts.
* **Cost ceiling.** A configured spend limit per plan and per day. Reaching it stops the queue and reports why, rather than continuing.
* **Idempotence preserved.** A run whose identity matches an existing artifact is a no-op that is still recorded, so "I ran it and nothing happened" is distinguishable from "it never ran."
* **Authorization.** Every endpoint that starts a run requires an authenticated operator, enforced by the backend.
* **One writer per artifact.** Two runs must not write the same artifact concurrently. This is a live hazard, not a theoretical one: several agent sessions share one checkout and one staging tree, and on 2026-08-19 an archive keyed only on reviewer fingerprint silently overwrote a previous generation.

## 8. Out of Scope

* Editorial review, claim editing, manuscript editing, publication approval — existing surfaces.
* A general workflow engine. The stages are a fixed, known chain; the console drives that chain and nothing else.
* Automatic scheduling. Every run is operator-initiated in the first release. Scheduling can follow once cost behaviour is understood.
* The invalidation model itself, which the library specification defines. The console displays and acts on it.

## 9. Acceptance

1. The inventory shows all 131 catalog sources with a state per stage, and the state is derived from the ledger rather than from scanning directories.
2. A run started from the CLI appears in run history with cost, without anyone opting in.
3. A selective run over several sources shows count, cost, and cache reuse before charging anything.
4. A deploy during a run does not lose the run: it completes, or it is recorded as interrupted.
5. Two simultaneous runs cannot write the same artifact.
6. Total spend for a period is a single query, and matches provider billing to within rounding.
7. An unauthenticated request to start a run is refused by the backend, not only by the frontend.
