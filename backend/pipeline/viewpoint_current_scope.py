"""Current selected Claim graph, not historical/global or semantic approval."""
from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord, EvidenceStepRecord, SourceFragmentRecord, evidence_fragment_ids,
)
from backend.pipeline.viewpoint_validation_context import (
    typed_current_records, current_evidence_producers, read_current_objects,
)


def read_current_scope(store, claim_ids):
    """Read forward AND reverse Evidence edges in one guarded SQL phase.

    Retain all related Evidence, singular/plural Fragment identities and source
    records. No semantic prefilter, hold upgrade, historic substitute, query
    LIMIT or whole-table fallback. Existing callers perform graph/source/hold
    validation; returning this closure does not assert those gates passed.
    """
    ids = sorted(set(claim_ids))
    claims = typed_current_records(store, 'claims', ids, ClaimRecord, 'claim_id')
    forward = {eid for cid in ids for eid in claims[cid].evidence_step_ids}
    evidence_ids = forward | current_evidence_producers(store, ids)
    evidence = (typed_current_records(store, 'evidence_steps', sorted(evidence_ids),
                    EvidenceStepRecord, 'evidence_step_id') if evidence_ids else {})
    fragment_ids = {fid for eid in evidence for fid in evidence_fragment_ids(evidence[eid])}
    fragments = (typed_current_records(store, 'source_fragments', sorted(fragment_ids),
                    SourceFragmentRecord, 'fragment_id') if fragment_ids else {})
    # ClaimRecord does not require source_id across extraction eras. The
    # canonical review compiler derives it from the bound Fragment population;
    # never invent a Claim field or use an unrelated source/title fallback.
    source_ids = {fragments[fid].source_id for fid in fragments}
    source_ids.update(sid for cid in ids if (sid := getattr(claims[cid],'source_id',None)) is not None)
    if any(not isinstance(sid,str) or not sid for sid in source_ids):
        raise ValueError('selected Claim/Fragment has no exact current source identity')
    sources = {}
    for (col, sid), row in read_current_objects(store, {('source_documents',sid) for sid in source_ids}).items():
        if row['retired'] or row['payload'].get('source_id') != sid:
            raise ValueError('retired/misidentified current source: '+str(sid))
        sources[sid] = row['payload']
    return claims, evidence, fragments, sources
