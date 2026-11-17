# Lecture 2 re-run (finishing, not held-out) — run gpt-20261116-lecture2-v2 (2026-11-16)

Graded against `reading-before-run.md` with the three logged key corrections. 918 s wall, 15 GPT calls, code a7fafb3f (step pass may return entries that are not Claims).
Held-out score stays 24/33 (first run). This run: 28/33.

## Fixed since the first run
- Time Magazine is a 題外話 (unsupported_comment + personal_story); no Claim from it.
- Returned correctly: same-God premise (CL0004 → Evidence for CL0005), Peter and Cornelius (CL0012 → Evidence for CL0011), the 十條誡 restatement (CL0008 → restates CL0005), Eph 4:13 (CL0029 → Evidence for CL0028).
- The four abstentions' purpose (不使猶太人跌倒) is its own Claim (CL0018); verses that seem to require the law are covered by CL0001.

## Still wrong (5 misses)
1. Rom 8:4 「律法的義」 returned as Evidence (CL0006). He argues the reading from δικαίωμα, so it is a Claim (prompt: 他另給理由去證明它本身的，仍是 Claim).
2. Acts 15 姦淫 = Lev 18 returned as Evidence (CL0017). He argues it from Lev 18; a Claim.
3. Blood still two Claims (CL0014, CL0015).
4. Subjectivity / one meaning / find evidence still three Claims (CL0026, CL0027, CL0028).
5. 2 Tim 2:7 νοέω still has no Claim of its own.

## Wrong Claims (4)
- CL0009 bundles the group (abstentions, stumbling, the 姦淫 caveat).
- CL0019 (Paul's idol-food rule) is Evidence for CL0020 but stayed a Claim.
- CL0022 「把聖經講清楚能幫助人得釋放」 is the pastor's words inside the engineer story (故事中別人說的話不是他的教導).
- CL0025 bundles CL0026–CL0031.

## Open for the owner (review page)
- Lev 17–18 as the source of the four abstentions (CL0013 returned as Evidence for CL0009). Proposed: Evidence.
