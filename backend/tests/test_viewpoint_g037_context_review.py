from copy import deepcopy
import pytest
from backend.pipeline import viewpoint_g037_context_review as m
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def case(monkeypatch):
    targets=[]
    for n in range(5):
        cid=m.CLAIM if n==0 else f'C{n}'
        old={'claim_id':cid,'viewpoint_id':m.VIEWPOINT if n==0 else 'V',
            'viewpoint_claim_link_id':m.LINK if n==0 else f'L{n}',
            'revision':1,'link_type':'equivalent_component','component_locator':{'exact_text':'意志'},'evidence_bindings':['E']}
        after=deepcopy(old);after.update(revision=2,link_type='supports',component_locator=None)
        targets.append({'claim':{'claim_id':cid},'before_link':old,'after_link':after,
            'sources':[{'source_id':'SRC-2017_NYSC_4-78f4b0b8eb95','paragraphs':[{'paragraph_key':'S0003','text':m.QUOTE}]}]})
    packet={'targets':targets,'policy':{'policy_id':'old'}}
    pin=sha256_json(packet);monkeypatch.setattr(m,'PACKET_SHA',pin)
    response={'verdicts':[{'claim_id':t['claim']['claim_id'],'approved':n!=0,'problems':['gap'] if n==0 else [],'reason':'reason'} for n,t in enumerate(targets)]}
    raw={'model_id':'gpt-6.1-sol','backend':'codex_subscription','request_payload_sha256':pin,'response':response,'response_sha256':sha256_json(response)}
    raw['artifact_sha256']=sha256_json(raw);monkeypatch.setattr(m,'RAW_SHA',raw['artifact_sha256'])
    return packet,raw


def test_only_one_equivalence_restored_four_supports_unchanged(monkeypatch):
    packet,raw=case(monkeypatch);before=deepcopy((packet,raw))
    result=m.contextual_packet(packet,deepcopy(packet),raw)
    m.validate_postimages(result)
    assert (packet,raw)==before
    assert result['targets'][0]['after_link']['component_locator']==packet['targets'][0]['before_link']['component_locator']
    assert result['targets'][0]['after_link']['link_type']=='equivalent_component'
    assert result['targets'][1:]==packet['targets'][1:]
    assert result['owner_context']['human_content_approval'] is False
    assert result['owner_context']['old_refusal']==raw['response']['verdicts'][0]


@pytest.mark.parametrize('mutation',['packet','raw','backend','response','second_refusal','missing_context','foreign_target'])
def test_invalid_authority_or_context_never_reviews(monkeypatch,mutation):
    packet,raw=case(monkeypatch);old=deepcopy(packet)
    if mutation=='packet':packet['targets'][1]['claim']['statement']='changed'
    if mutation=='raw':raw['artifact_sha256']='bad'
    if mutation=='backend':raw['backend']='api'
    if mutation=='response':raw['response']['verdicts'][0]['reason']='changed'
    if mutation=='second_refusal':raw['response']['verdicts'][1]['approved']=False
    if mutation=='missing_context':packet['targets'][0]['sources'][0]['paragraphs']=[]
    if mutation=='foreign_target':packet['targets'][0]['before_link']['viewpoint_id']='OTHER'
    with pytest.raises(ValueError):m.contextual_packet(packet,old,raw)


@pytest.mark.parametrize('mutation',['locator','evidence','endpoint','fourth_equivalence','approval','population'])
def test_package_cannot_expand_owner_scope(monkeypatch,mutation):
    packet,raw=case(monkeypatch);p=m.contextual_packet(packet,packet,raw)
    if mutation=='locator':p['targets'][0]['after_link']['component_locator']=None
    if mutation=='evidence':p['targets'][0]['after_link']['evidence_bindings']=[]
    if mutation=='endpoint':p['targets'][0]['after_link']['viewpoint_id']='OTHER'
    if mutation=='fourth_equivalence':p['targets'][3]['after_link']['link_type']='equivalent_component'
    if mutation=='approval':p['owner_context']['human_content_approval']=True
    if mutation=='population':p['targets'].pop()
    with pytest.raises(ValueError):m.validate_postimages(p)


def test_across_root_reservation_cannot_repeat(tmp_path):
    source=tmp_path/m.OLD_ROOT_NAME;source.mkdir()
    for n in ('packet.json','before.json','raw-gpt.json','raw-gpt.request-encoding.json','failure.json'):
        (source/n).write_text('{}')
    m.reserve(tmp_path/'r1',source,tmp_path)
    with pytest.raises(ValueError,match='already reserved'):m.reserve(tmp_path/'r2',source,tmp_path)
    assert not (tmp_path/'r2/owner-authorization.json').exists()


def test_context_package_materializes_equivalence_not_supports(monkeypatch,tmp_path):
    import json
    from backend.pipeline.viewpoint_audit_link_repair import package_for
    packet,raw=case(monkeypatch);p=m.contextual_packet(packet,packet,raw)
    states={}
    for n,t in enumerate(p['targets']):
        t['before_link']['decision_id']=f'D{n}'
        t['after_link']['decision_id']=f'D{n}'
        states['viewpoint_identity_decisions',f'D{n}']={'identity_candidate_id':f'I{n}'}
        states['viewpoint_identity_candidates',f'I{n}']={'seed_relation_ids':[]}
    for label in ('gpt','opus'):(tmp_path/f'raw-{label}.json').write_text(json.dumps({'artifact_sha256':label}))
    pkg=package_for(states,p,{'gpt':{},'opus':{}},tmp_path)
    assert len(pkg['viewpoint_claim_links'])==5
    assert [d['claim_link_decisions'][0]['link_type'] for d in pkg['viewpoint_identity_decisions']]==['equivalent_component']+['supports']*4
    assert set(pkg)=={'package_id','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'}
    assert all(d['policy_version']==m.POLICY_ID and d['approval_basis']=='dual_model_consensus' for d in pkg['viewpoint_identity_decisions'])
