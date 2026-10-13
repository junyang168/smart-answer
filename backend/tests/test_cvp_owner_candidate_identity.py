from copy import deepcopy
import json
import pytest

from backend.pipeline.cvp_owner_candidate_identity import (
    SCHEMA, template, validate_bounded_proposal, validate_direction,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def original():
    return {'batch_id':'B', 'new_viewpoint_candidates':[
        {'local_key':x,'core_proposition':x} for x in ('A','B','C')],
        'claim_decisions':[{'claim_id':cid,'components':[{
            'local_new_viewpoint_key':key, 'reason':'old', 'spans':[{'start_char':0,'end_char':2}],
            'evidence_step_ids':['E'+cid], 'disposition':'new_viewpoint'}]}
            for cid,key in [('1','A'),('2','B'),('3','C')]],
        'viewpoint_relations':[
            {'source_local_key':'B','target_local_key':'A','relation_type':'specializes','reason':'old'},
            {'source_local_key':'B','target_local_key':'C','relation_type':'entails','reason':'old'}],
        'structures':[{'focal':[{'local_key':'A','structure_role':'central_claim'},
                               {'local_key':'B','structure_role':'positive_identification'},
                               {'local_key':'C','structure_role':'other'}],
                       'central_synthesis':'old','reason':'old','unresolved_items':['old']}],
        'viewpoint_revisions':[]}


def test_exact_mechanical_redirect_drops_only_self_edge_preserves_claims():
    p=original(); before=deepcopy(p); q=template(p,['A','B'],'B')
    assert p==before
    assert [x['local_key'] for x in q['new_viewpoint_candidates']]==['B','C']
    assert len(q['claim_decisions'])==3
    assert q['viewpoint_relations']==[p['viewpoint_relations'][1]]
    assert [f['local_key'] for f in q['structures'][0]['focal']]==['B','C']
    validate_bounded_proposal(p,q,['A','B'],'B')


def test_only_new_target_content_and_affected_reasons_are_allowed():
    p=original();q=template(p,['A','B'],'B')
    q['new_viewpoint_candidates'][0]['core_proposition']='new model content'
    q['claim_decisions'][0]['components'][0]['reason']='own-context identity'
    q['structures'][0]['unresolved_items']=[]
    validate_bounded_proposal(p,q,['A','B'],'B')


@pytest.mark.parametrize('change',[
    lambda q:q['claim_decisions'].pop(),
    lambda q:q['claim_decisions'][0]['components'][0]['evidence_step_ids'].append('FOREIGN'),
    lambda q:q['claim_decisions'][0]['components'][0]['spans'][0].update(end_char=1),
    lambda q:q['claim_decisions'][0]['components'][0].update(disposition='defer'),
    lambda q:q['claim_decisions'][2]['components'][0].update(reason='unrelated'),
    lambda q:q['new_viewpoint_candidates'][1].update(core_proposition='unrelated'),
    lambda q:q['viewpoint_relations'][0].update(relation_type='applies'),
    lambda q:q['structures'][0]['focal'].pop(),
    lambda q:q['viewpoint_revisions'].append({'body':'unauthorized'}),
])
def test_rejects_scope_drift(change):
    p=original();q=template(p,['A','B'],'B');change(q)
    with pytest.raises(ValueError):validate_bounded_proposal(p,q,['A','B'],'B')


def direction_fixture(tmp_path):
    from backend.pipeline.cvp_sql_flow import binding
    p=original();con={'schema_version':'wang_canonical_viewpoint_identity_consolidation_v2',
        'verdicts':[{'local_key':'B','verdict':'new','reason':'new'},
                    {'local_key':'C','verdict':'new','reason':'new'}],
        'unresolved_items':[{'local_key':'A','reason':'uncertain'}]}
    packet={'batch_id':'B','claims':[{'claim_id':cid,'pinned_claim_revision':1,
                                    'claim_revision_sha256':'abc'} for cid in ('1','2','3')]}
    for name in ('batch-packet.json','raw-proposal.json','raw-consolidation.json'):
        (tmp_path/name).write_text(json.dumps({'saved':name}))
    d={'schema_version':SCHEMA,'authorized_by':'owner','owner_quote':'same CVP',
       'decision':'authorize_same_identity_revision','batch_id':'B',
       'candidate_keys':['A','B'],'retained_local_key':'B','claim_ids':['1','2'],
       'claim_pins':{cid:{'revision':1,'sha256':'abc'} for cid in ('1','2')},
       'saved_inputs':{n:binding(tmp_path/n) for n in ('batch-packet.json','raw-proposal.json','raw-consolidation.json')}}
    d['artifact_sha256']=sha256_json(d)
    return d,p,con,packet


def test_direction_exact_saved_artifacts_and_versions(tmp_path):
    d,p,con,packet=direction_fixture(tmp_path)
    assert validate_direction(d,p,con,packet,tmp_path).unresolved_items[0].local_key=='A'
    packet['claims'][0]['pinned_claim_revision']=2
    with pytest.raises(ValueError):validate_direction(d,p,con,packet,tmp_path)


def test_direction_rejects_tampering_and_unrelated_unresolved(tmp_path):
    d,p,con,packet=direction_fixture(tmp_path)
    d['owner_quote']='changed'
    with pytest.raises(ValueError):validate_direction(d,p,con,packet,tmp_path)
    d,p,con,packet=direction_fixture(tmp_path)
    con['verdicts']=con['verdicts'][:1]
    con['unresolved_items'].append({'local_key':'C','reason':'another problem'})
    with pytest.raises(ValueError):validate_direction(d,p,con,packet,tmp_path)


def test_direction_rejects_changed_saved_physical_input(tmp_path):
    d,p,con,packet=direction_fixture(tmp_path)
    (tmp_path/'raw-consolidation.json').write_text('{"different":true}')
    with pytest.raises(ValueError):validate_direction(d,p,con,packet,tmp_path)


def test_direction_cannot_override_existing_registry_merge(tmp_path):
    d,p,con,packet=direction_fixture(tmp_path)
    con['verdicts'][0].update(verdict='merge',target_viewpoint_revision_id='CVR-other')
    with pytest.raises(ValueError):validate_direction(d,p,con,packet,tmp_path)


def test_revision_payload_is_bounded_but_keeps_entire_proposal(monkeypatch):
    from backend.pipeline import cvp_owner_candidate_identity as mod
    monkeypatch.setattr(mod,'own_sources',lambda packet,ids:[{'full_body':'unchanged own source'}])
    packet={'batch_id':'B','claims':[{'claim_id':x} for x in ('1','2','3')]}
    d={'candidate_keys':['A','B'],'retained_local_key':'B','claim_ids':['1','2']}
    payload=mod.revision_payload(original=original(),consolidation={'unresolved':'saved'},packet=packet,direction=d)
    assert [c['claim_id'] for c in payload['target_claims']]==['1','2']
    assert len(payload['required_proposal_template']['claim_decisions'])==3
    assert payload['own_source_bodies']==[{'full_body':'unchanged own source'}]
    assert 'packet' not in payload  # unrelated evidence cannot be revised by this role
