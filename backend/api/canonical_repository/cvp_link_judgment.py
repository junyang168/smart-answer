"""Recover a connection's approved rationale from its exact committed origin.

Some CVP2 decisions contain only a generic reason. The approved compact
candidate still contains the semantic judgment; do not manufacture a new one
or rewrite that historical decision to supply downstream context.
"""
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.postgres_store import record_content_sha


def approved_link_judgment(link, decision, origin):
    reviewed = origin['reviewed']
    evidence = reviewed['evidence']['cvp_management']
    candidate, approval = evidence['candidate'], evidence['approval']
    if (origin['reviewed_sha256'] != sha256_json(reviewed)
            or not approval['approved']
            or approval['candidate_sha256'] != candidate['artifact_sha256']
            or any(v['artifact_sha256'] != sha256_json({k:x for k,x in v.items() if k != 'artifact_sha256'})
                   for v in (candidate, approval))
            or decision['review_artifact_sha256'] != evidence['provenance']['review_artifact_sha256']
            or link['decision_id'] != decision['identity_decision_id']):
        raise ValueError('connection rationale lacks its exact approved origin')
    writes = [w for w in candidate['writes'] if w['collection'] == 'viewpoint_claim_links'
              and w['object_id'] == link['viewpoint_claim_link_id']]
    if len(writes) != 1 or record_content_sha(writes[0]['payload']) != record_content_sha(link):
        raise ValueError('connection differs from its approved rationale postimage')
    reasons = [r['reason'] for r in candidate['component_dispositions']
               if r.get('link_ref') == writes[0]['ref']]
    reasons += [r['reason'] for r in candidate.get('member_dispositions',[])
                if r.get('member_ref') == writes[0]['ref']]
    if not reasons:
        reasons = [decision['reason']]
    if any(not r.strip() for r in reasons):
        raise ValueError('approved connection has an empty rationale')
    return {'viewpoint_claim_link_id': link['viewpoint_claim_link_id'],
            'decision_id': link['decision_id'], 'claim_id': link['claim_id'],
            'link_type': link['link_type'],
            'viewpoint_revision_id': link['validated_against_viewpoint_revision_id'],
            'reasons': list(dict.fromkeys(reasons)),
            'candidate_sha256': candidate['artifact_sha256'],
            'approval_sha256': approval['artifact_sha256'],
            'review_artifact_sha256': decision['review_artifact_sha256']}
