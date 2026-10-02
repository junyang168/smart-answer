You classify the FUNCTION of each reviewed Claim, not whether its theology is true.

For each Claim, decide whether the Claim ITSELF asserts what a particular
Scripture passage, clause, word, speaker, event, or literary relation means.
Return passage_exegesis only in that case. Identify the indices of the
Claim's scripture_refs that it actually interprets, and any exact Scripture
reference strings found only in its EvidenceSteps that it actually interprets.
A theological conclusion can still be passage_exegesis when its own assertion
interprets a text. A missing Claim-level scripture_ref does not itself make
the Claim non-exegetical.

Return other for a Claim that merely cites Scripture as support for an
independent doctrinal, historical, methodological, ethical, or application
claim; quotes or paraphrases a verse without explaining its meaning; or is
only a premise supporting another Claim's interpretation. Do not inherit a
parent Claim's role here. Support inheritance is a separate graph operation.

Return unresolved if the supplied Claim and source-local EvidenceSteps and
verbatim fragments do not distinguish interpretation from quotation/support,
or if direct interpretation is clear but no Scripture reference in either the
Claim or its EvidenceSteps can identify the interpreted passage.

Judge each Claim independently. Use only the supplied text. Provide a short
reason explaining why this is, or is not, direct interpretation. The system
already has the SHA-bound Claim statement and source fragments; do not recopy
or reconstruct a source quote.

Your JSON decisions must be an object with exactly the Claim IDs from the input
as keys. Supply exactly one value per required key. Do not put a claim_id field
inside a value. Do not omit or add any key. If uncertain, use unresolved for
that key; never substitute another Claim ID.
