# Lecture 1 rerun, round 1 (2026-11-18)

Prompt: afeff383 (the topic-question rule). 19 GPT calls, 483k tokens, 10 min.
Run: `argument-layer-runs/gpt-20261118-lecture1-r2`. Not saved to the instance.

## Topic-question key (`../topic-questions.json`)

| Item | Saved v6 | Round 1 |
| --- | --- | --- |
| L1-structure | FAIL 3/4 | FAIL 3/4: the ranges moved into CL0002 and CL0003; the top point CL0001 no longer says there are two parts |
| L1-which-parts | FAIL 2/3 | FAIL 2/3: 「摩西律法至少有一部分我們不必再遵守」 still names no part |

## Owner key (`cases.json`, owner items)

| Group | v6 | Round 1 |
| --- | --- | --- |
| G01 | ct1 missed (owner accepted) | same |
| G02 | correct | posct missed; one argument sentence proposed as 題外話 |
| G03 | 1 extra (grader artifact) | same |
| G04 | correct | correct |
| G05 | correct | pos missed; p5 and p6 split into two records each |

Also worse, not in either key: CL0008 「八福應許具有聖經所說的溫柔的人必蒙神之福。」 is not a sentence.

## Why

The new rule sits next to two older ones that tell the top point not to list:
「蓋子寫結論，不寫目錄，也不把下面各條的內容再寫一遍」 and
「已各成分論點的部分，上層只點出名稱，不重述其內容」. The model kept the older ones.
The G02/G05 losses may be run-to-run variation; one run cannot tell.
