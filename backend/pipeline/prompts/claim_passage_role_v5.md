You classify the FUNCTION of each reviewed Claim, not whether its theology is true.

The bar for passage_exegesis is deliberately strict. The Claim ITSELF must
resolve an interpretive question about an identifiable Scripture passage:
the meaning or referent of its words, an original-language/translation issue,
who is speaking or addressed, the scope or structure of a passage, a metaphor
or parable's meaning, or a proposed reading that the passage supports or
rejects. State which exact Claim scripture_ref indices and/or exact
EvidenceStep scripture_ref strings identify the interpreted passage.

Return other when the Claim only:
- cites a verse as evidence for an independent doctrinal or ethical position;
- restates, paraphrases, or summarizes what the verse says without resolving
  an interpretive question;
- draws a historical, theological, causal, or practical inference from a
  passage without saying what the passage means;
- applies a passage to conduct, makes a value judgment about an action, or
  states a hermeneutical method;
- serves as a premise supporting another Claim's exegesis. Do not inherit
  that other Claim's role here; graph propagation is a later operation.

For example, "the passage's 'rock' refers to God" is exegesis because it
resolves a referent. "The teacher should equip believers" is not exegesis
merely because it summarizes a verse. "Not eating meat is loving" is an
application/evaluation, not exegesis merely because a passage supports it.
"The burial request was unusual" is a historical observation, not an
interpretation of the verse's meaning. These are role examples only; decide
every supplied Claim from its own pinned text and source-local evidence.

Return unresolved if the supplied material cannot distinguish these roles,
or if direct interpretation is clear but neither the Claim nor its
EvidenceSteps identifies the particular Scripture passage. Do not guess a
reference from general theological knowledge. A missing Claim-level ref alone
does not force other: an EvidenceStep can identify the passage.

Use only the supplied Claim, EvidenceSteps, and verbatim fragments. Do not
judge whether the teaching is correct. Provide a short reason that names the
interpretive question resolved, or explains why the Claim is only
quotation, inference, application, or other. The system already retains the
SHA-bound original text; do not recopy a quote.

Your JSON decisions must be an object with exactly the Claim IDs from the
input as keys, one value per key. Do not put claim_id inside a value. Never
omit or add a key; use unresolved for that key if uncertain.
