You are proposing screening-only semantic groups among source-local Claims.

Use every Claim semantic signature and only the listed review or context edges. Review
edges are assigned exactly once across packets; context edges supply local graph
connectivity but are owned by another packet. A group may
represent possible equivalence, a component relationship, or a substantive tension.
Do not infer transitive identity: A related to B and B related to C does not prove A
and C are one viewpoint. Preserve polarity, stance, modality, scope, conditions,
qualifications, and evidence insufficiency. Application, Greek-language analysis,
interpretive method, and passage-specific exposition may all express viewpoints.
Reported external positions may supply contrast but cannot become professor-viewpoint
members. Do not create canonical IDs, approve wording, mutate master data, or treat
embedding/rule/signature recall as identity evidence. Claims not placed in a proposal
remain unresolved, not rejected.

Return only JSON conforming to the supplied response schema.

For each proposal, assign participants one of these roles:
- possible_equivalent: every participant is candidate_member;
- component: use candidate_member for the proposed core, component for a necessary
  narrower assertion, and contrast_only only for contextual opposition;
- tension: include at least one tension_side_a and one tension_side_b.

Prefer proposals connected by the packet's listed candidate edges; they need not be a
clique. When a defensible proposal is not connected by those edges, set
requires_recall_extension=true. Otherwise set it false. A later deterministic compiler
will add the minimum traceable group_model_discovery edges before identity review; this
response itself does not mutate the recall graph. A Claim may appear in more than one proposal when the signature contains
multiple semantic atoms. Put every participant Claim that needs source-local Evidence
before identity review in evidence_required_claim_ids. Use stable packet-local IDs in
the form G001, G002, and so on. Empty proposals are valid when the packet supplies no
defensible grouping.
