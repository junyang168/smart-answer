# Golden cases: argument layer

Design: `docs/wang-knowledge-platform/10-extraction/argument_layer_rebuild_design.md` §0.

`2011-nysc-matthew-2-1/` holds lecture 1 of 2011 NYSC 專題：馬太福音釋經（二）, groups G01–G05.

| File | What it is |
| --- | --- |
| `cases.json` | The answers used for grading. Each sentence is located by its transcript `script_index` plus its exact text, so the answer survives regrouping. |
| `consolidated.json` | The same answers in the trial's per-request numbering (#N), kept as provenance for `cases.json`. |
| `trial-keys/` | The trial keys verbatim, including every change the owner made during the trial. |

Each item has a `status`:
- `owner`: the owner ruled it, so it counts for grading.
- `draft`: Claude drafted it. It does not count until two 同工 agree blind, or the owner rules on their disagreement.

Accepted variants are recorded as `accept`, `accept_kind`, `accept_merge` or `optional`.

Every trial sentence has exactly one place. 345 sentences, each checked against its transcript paragraph.
