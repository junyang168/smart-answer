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

# Round 2 (`gpt-20261118-lecture1-r3`, prompt fde0ac9b)

Topic key: both pass (L1-structure after the key accepted 「五3至12」; the owner was told).
Owner key: G01 all correct (ct1 now held); G02, G04 correct; G03 the known artifact;
G05 p5 partial, p6 split: the re-reading of 可7 and 來10 while he restates the tension
went to the Question. Not saved: G05 split for the second time.

# Round 3 (`gpt-20261118-lecture1-r4`, prompt 6ddd80a3: a restated step goes back to its step)

Topic key: both pass. Point 1: 「登山寶訓分為五章三至十二節的彌賽亞身份與五章十三節至七章二十九節的門徒使命兩大段……」;
point 17 names 「食物潔淨的分別與贖罪祭這兩項」.
Owner key: G01, G04 correct; G03 the known artifact; G05 p5, p6 now correct, but
**tension missed**: 「可是耶穌說，你不必再遵行舊約聖經的分辨潔淨不潔淨的食物」, one side of the
contradiction he sets up, went to the food Evidence. The new rule caused it: that sentence
also "re-says a step's 不必". G02 posct missed: 「一大堆！」 proposed as part of the 題外話
(also in round 1; correct in v6 and round 2). Not saved.

# Round 4 (`gpt-20261118-lecture1-r5`, prompt 379773ce: only a re-read verse goes back)

Topic key: both pass. Owner key: G01, G02, G04 correct; G03 the known artifact; G05
tension correct again, **p5 and p6 split again**: the first 可7:18–19 reading became an
Observation and the re-reading Evidence; 「耶穌既然來，為我們獻了一次永遠有效的贖罪祭」 went
to the Question. Every round since round 1 has failed exactly two owner items, a
different pair each time. Prompt changes for G05 stop here (stopping point set before
this round); the choice of round goes to the owner.
