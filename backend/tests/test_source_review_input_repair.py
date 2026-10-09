from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from backend.pipeline import source_review_input_repair as m
from backend.pipeline.source_reconciliation_runner import write
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


@pytest.fixture
def sample():
    text='甲。乙。'
    h=lambda v: hashlib.sha256(v.encode()).hexdigest()
    frozen={'manifest':{'source_id':'S','claim_issue':{'path':'exact'}},
            'after':[{'body_sha256':h(text),'paragraphs':{'P':{'text':text}}}]}
    old={'source_id':'S','paragraph_key':'P','verbatim_excerpt':'乙。','verbatim_excerpt_sha256':None,
         'paragraph_text_sha256':h(text),'source_sha256':h(text),'anchor_state':'source_version_bound'}
    new={**old,'verbatim_excerpt_sha256':h('乙。')}
    row=lambda p:{'collection':'source_fragments','object_id':'F','payload':p}
    response={'records':[row(old)],'reason':'SHA为null'}
    proposed={'records':[row(new)]}
    proposal=SimpleNamespace(model_dump=lambda:deepcopy(proposed))
    return frozen,response,proposal


def test_receipt_keeps_model_null_and_reason_separate_from_exact_program_hash(sample):
    frozen,response,proposal=sample
    before=deepcopy(response)
    proof=m.receipt(frozen,response,proposal)
    assert proof['records'][0]['model_fields']['verbatim_excerpt_sha256'] is None
    assert proof['records'][0]['program_fields']['verbatim_excerpt_sha256']==hashlib.sha256('乙。'.encode()).hexdigest()
    assert proof['model_reasons_preserved'] and response==before


@pytest.mark.parametrize('change',['source','quote','hash','paragraph_hash','body_hash','extra_content','ambiguous'])
def test_receipt_rejects_wrong_source_or_any_unproved_fragment_change(sample,change):
    frozen,response,proposal=sample
    data=proposal.model_dump(); p=data['records'][0]['payload']
    if change=='source':response['records'][0]['payload']['source_id']='foreign'
    if change=='quote':p['verbatim_excerpt']='丙。'
    if change=='hash':p['verbatim_excerpt_sha256']='0'*64
    if change=='paragraph_hash':p['paragraph_text_sha256']='0'*64
    if change=='body_hash':p['source_sha256']='0'*64
    if change=='extra_content':p['statement']='added'
    if change=='ambiguous':frozen['after'][0]['paragraphs']['P']['text']='乙。乙。'
    with pytest.raises((ValueError,KeyError)):
        m.receipt(frozen,response,SimpleNamespace(model_dump=lambda:data))


def direction(root,frozen,proposal,packet,raw,correction):
    return {'schema_version':m.SCHEMA,'authorized_by':'owner','owner_direction':'继续修bug，补一次精确终审输入',
            'action_root':str(root.resolve()),'materials_sha256':sha256_json(frozen),
            'proposal_sha256':sha256_json(proposal.model_dump()),'original_final_packet_sha256':sha256_json(packet),
            'original_final_raw_sha256':raw['artifact_sha256'],'correction_raw_sha256':correction['artifact_sha256'],
            'additional_review_limit':1,'content_change_authorized':False}


def test_repaired_packet_preserves_original_rejection_and_substantive_postimage(sample,tmp_path,monkeypatch):
    frozen,response,proposal=sample
    monkeypatch.setattr('backend.pipeline.source_reconciliation_runner.effective_proposal',lambda *_:proposal)
    packet={'proposal':proposal.model_dump(),'whole_source':'complete'}
    raw={'artifact_sha256':'old','response':{'verdicts':[{'approved':False,'reason':'hash not explained'}]}}
    correction={'artifact_sha256':'correct','response':response}
    write(tmp_path/m.NAME,direction(tmp_path,frozen,proposal,packet,raw,correction))
    repaired=m.repaired_packet(tmp_path,frozen,proposal,packet,raw,correction)
    assert repaired['proposal']==packet['proposal'] and repaired['whole_source']=='complete'
    assert repaired['input_repair']['original_terminal_review']==raw['response']
    assert not repaired['input_repair']['another_input_repair_allowed']


@pytest.mark.parametrize('field',['action_root','materials_sha256','proposal_sha256','original_final_packet_sha256',
                                  'original_final_raw_sha256','correction_raw_sha256','authorized_by',
                                  'additional_review_limit','content_change_authorized','owner_direction'])
def test_exact_direction_cannot_buy_new_root_input_content_or_budget(sample,tmp_path,field):
    frozen,response,proposal=sample;packet={'proposal':proposal.model_dump()}
    raw={'artifact_sha256':'old'};correction={'artifact_sha256':'correct','response':response}
    auth=direction(tmp_path,frozen,proposal,packet,raw,correction);auth[field]='' if field=='owner_direction' else 'foreign'
    write(tmp_path/m.NAME,auth)
    with pytest.raises(ValueError,match='direction drift'):
        m.repaired_packet(tmp_path,frozen,proposal,packet,raw,correction)


@pytest.mark.parametrize('accepted',[True,False])
def test_bounded_repair_consumes_saved_chain_without_second_correction(sample,tmp_path,monkeypatch,accepted):
    from backend.pipeline import source_reconciliation_runner as r
    frozen,response,proposal=sample
    frozen['inventory']={'unknown_keys':[]}
    frozen['update_id']='original'
    monkeypatch.setattr(r,'build_proposal_packet',lambda *_:{'complete':'own body'})
    monkeypatch.setattr(r,'effective_proposal',lambda *_:proposal)
    monkeypatch.setattr(r.contract,'review_packet',lambda *_:{'proposal':proposal.model_dump()})
    monkeypatch.setattr(r.contract,'validate_review',lambda p,v:v.get('approved',False))
    cache={};calls=[]
    class Runner(r.Runner):
        def guard(self,*a,**k):pass
        def call(self,stage,packet):
            if stage in cache:return cache[stage]
            calls.append(stage)
            answer={'approved':accepted} if stage==m.STAGE else {'approved':False} if 'review' in stage else response
            raw={'artifact_sha256':stage,'response':answer}
            cache[stage]=raw
            write(self.root/(stage+'.packet.json'),packet)
            return raw
    runner=Runner(tmp_path,None);runner.root=tmp_path
    with pytest.raises(r.Stop,match='terminal Opus'):runner.reviewed(frozen)
    old=deepcopy(cache)
    packet=r.body(r.read(tmp_path/'final_review.packet.json'))
    write(tmp_path/m.NAME,direction(tmp_path,frozen,proposal,packet,cache['final_review'],cache['correction']))
    for _ in range(2):
        if accepted:assert runner.reviewed(frozen)[1]==cache[m.STAGE]
        else:
            with pytest.raises(r.Stop,match='no further'):runner.reviewed(frozen)
    assert calls==['proposal','initial_review','correction','final_review',m.STAGE]
    assert all(cache[k]==v for k,v in old.items())


def test_unknown_repair_call_reservation_is_never_removed_or_reissued(tmp_path):
    from backend.pipeline import source_reconciliation_runner as r
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    calls=[]
    adapter=model_adapter('final_review',issue=True)
    def fail(packet):
        calls.append(packet)
        raise RuntimeError('network outcome unknown')
    adapter.generate=fail
    runner=r.Runner(tmp_path,SimpleNamespace(claim_issue_adapter=lambda stage:adapter))
    runner.root=tmp_path
    auth=write(tmp_path/m.NAME,{'exact':'owner direction'})
    packet={'manifest':{'claim_issue':{}},'input_repair':{'authorization':auth}}
    packet['manifest']['claim_issue']={'present':True}
    with pytest.raises(r.Stop,match='reservation retained'):runner.call(m.STAGE,packet)
    intent=(tmp_path/(m.STAGE+'.reservation.json')).read_bytes()
    with pytest.raises(r.Stop,match='do not repeat'):runner.call(m.STAGE,packet)
    assert len(calls)==1 and intent==(tmp_path/(m.STAGE+'.reservation.json')).read_bytes()
