"""Corrected inputs retain the original once history, not arbitrary retries."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_complete_input_repair as x
from backend.pipeline import claim_issue_rescope as a
from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.api.canonical_repository.postgres_store import sha256_json


@pytest.fixture
def actual_direction():
    if os.environ.get('WKP357_REAL_SAVED_READONLY')!='1':
        pytest.skip('explicit saved real read-only opt-in')
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    return json.loads((root/'owner-g103-complete-input-engineering-repair.json').read_text())


def seal(value):
    value['artifact_sha256']=sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'})
    return value


@pytest.mark.parametrize('fault',[None,'owner','content','proposal','review','correction','defect','run','root',
    'policy','graph','evidence','leaf','terminal_unknown','terminal_answer'])
def test_actual_exact_original_history_and_negative_gates(actual_direction,monkeypatch,fault):
    d=deepcopy(actual_direction)
    if fault=='owner':d['authorized_by']='model'
    elif fault=='content':d['human_content_approval']=True
    elif fault=='proposal':d['additional_proposal_limit']=2
    elif fault=='review':d['additional_review_limit']=2
    elif fault=='correction':d['additional_correction_limit']=1
    elif fault=='defect':d['defects']=['semantic_rejection']
    elif fault=='run':d['run_id']='foreign'
    elif fault=='root':d['old_action_root']+='/foreign'
    elif fault=='policy':d['new_policy']=d['old_raw']
    elif fault=='graph':d['context_claim_ids']=['foreign']
    elif fault=='evidence':d['evidence_ids'].append('foreign')
    elif fault=='leaf':d['old_correction_raw']['file_sha256']='0'*64
    elif fault in {'terminal_unknown','terminal_answer'}:
        original=Path.exists
        suffix='final_review.reservation.json' if fault=='terminal_unknown' else 'final_review.raw.json'
        monkeypatch.setattr(Path,'exists',lambda p:True if p.name==suffix else original(p))
    seal(d)
    root=Path(actual_direction['old_action_root'])
    pins={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    if fault:
        with pytest.raises((ValueError,KeyError)):x.validate(d)
    else:
        old,raw=x.validate(d)
        assert raw['response'] and old['sql_scope']['run_id']==d['run_id']
        assert not d['human_content_approval']
    assert pins=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}


def test_actual_current_manifest_full_packet_zero_writes(actual_direction,monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env');store=PostgresKnowledgeStore()
    direction=actual_direction
    old,_=a.validate_live(store,direction)
    records=a.amendment_records(store,direction,old)
    memory={};original=c.bound_bytes
    def write(path,value,**kw):
        sealed=r.seal(value);memory[str(path)]=json.dumps(sealed,ensure_ascii=False).encode();return sealed
    def binding(path):return {'path':str(path),'file_sha256':hashlib.sha256(memory[str(path)]).hexdigest()}
    def bound(value,snapshots=None):
        v=value.model_dump() if hasattr(value,'model_dump') else value
        return memory[v['path']] if v['path'] in memory else original(value,snapshots)
    monkeypatch.setattr(a,'write',write);monkeypatch.setattr(a,'binding',binding);monkeypatch.setattr(c,'bound_bytes',bound)
    authorization=r.binding(Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/owner-g103-complete-input-engineering-repair.json'))
    manifest=a.amend_manifest(store,direction,authorization,Path('/zero-write-preflight'))
    states={c.key(*k):row['payload'] for k,row in records.items() if not row['retired']}
    frozen=c.freeze(manifest,states)
    frozen['sql_scope']=old['sql_scope']
    packet=r.build_proposal_packet(frozen)
    assert packet['complete_input_repair_receipt']['correction_limit']==0
    assert packet['complete_input_repair_receipt']['original_answers']['correction']==r.read(Path(direction['old_action_root'])/'correction.raw.json')['response']
    assert packet['inventory']['unknown_keys']==[]
    assert packet['read_only_adapter_receipt']['content_write_authority'] is False
    own=next(p for p in manifest.permissions if p.collection=='claims' and p.object_id=='DK-72483dc200ad-CL002')
    assert 'occurrences' in own.fields
    assert all(states[k]==v for k,v in c.index(old['before_records']).items())
    c.guard_files(frozen)
    print({'current_pins':len(records),'complete_packet_bytes':len(json.dumps(packet,ensure_ascii=False).encode()),'old_models_replayed':0,'registry_writes':0})


def test_complete_input_review_never_uses_draft_exception(monkeypatch):
    frozen={'claim_issue_context':{'complete_input_repair':{'correction_limit':0}},'manifest':{'claim_issue':True}}
    calls=[]
    def packet(*args,**kw):calls.append(kw);raise ValueError('strict incomplete graph')
    monkeypatch.setattr(c,'review_packet',packet)
    with pytest.raises(ValueError,match='strict'):r.initial_review_base(frozen,None)
    assert calls==[{}]


def test_citation_review_prompt_isolated_from_legacy_adapter():
    from types import SimpleNamespace
    from backend.pipeline import claim_citation_purpose_review as p
    adapter=SimpleNamespace(_base_prompt='base',_prompt='full',prompt_sha256='old')
    assert p.scoped_adapter(adapter,{}) is adapter
    packet={'claim_citation_purpose_review':{'schema_version':p.VERSION},
            'claim_issue_context':{'complete_input_repair':{'correction_limit':0}}}
    scoped=p.scoped_adapter(adapter,packet)
    assert adapter._prompt=='full' and adapter.prompt_sha256=='old'
    assert scoped._prompt=='full\n'+p.PROMPT and scoped.prompt_sha256!='old'
    packet['claim_issue_context']={}
    with pytest.raises(ValueError):p.scoped_adapter(adapter,packet)


def test_complete_input_rejection_does_not_open_another_correction(monkeypatch,tmp_path):
    class Runner(r.Runner):
        def guard(self,*args,**kw):pass
        def call(self,stage,packet):
            calls.append(stage);return {'response':{}}
        def review_input(self,*args):return {}
    calls=[]
    monkeypatch.setattr(c,'unresolved_consumers',lambda f:[])
    monkeypatch.setattr(r,'build_proposal_packet',lambda f:{})
    monkeypatch.setattr(r,'effective_proposal',lambda *args:None)
    monkeypatch.setattr(c,'validate_review',lambda *args:False)
    frozen={'claim_issue_context':{'complete_input_repair':{'correction_limit':0}}}
    with pytest.raises(r.Stop,match='no extra correction'):Runner(tmp_path,None).reviewed(frozen)
    assert calls==['proposal','initial_review']


@pytest.mark.parametrize('fault',[None,'historical_text','historical_identity','historical_order','receipt','legacy'])
def test_actual_saved_proposal_historical_anchors_are_readonly_not_current_source(actual_direction,fault):
    root=Path(actual_direction['old_action_root']).parent.parent/'complete-input-repair-once/action'
    path=next(root.glob('*/proposal.raw.json'))
    frozen=r.body(r.read(path.parent/'scoped-materials.json'))
    raw=r.validate_raw(path.parent,'proposal',r.body(r.read(path.parent/'proposal.packet.json')))
    response=deepcopy(raw['response'])
    from backend.pipeline.deferred_claim_repair import decode_proposal
    before=c.index(frozen['before_records'])
    base={k:v for k,v in response.items() if k not in {'claim_assessments','cvp_impacts'}}
    data=c.index(c.Proposal.model_validate(decode_proposal(base)).records)
    claim=data['claims/DK-72483dc200ad-CL002']
    if fault in {'historical_text','historical_identity','historical_order'}:
        if fault=='historical_text':claim['occurrences'][0]['anchors'][0]['proposed_highlight']['text']='changed'
        elif fault=='historical_identity':claim['occurrences'][0]['source_id']='foreign'
        else:claim['occurrences'].reverse()
        response={**response,'records':c.rows(data)}
    elif fault=='receipt':frozen['claim_issue_context']['retained_occurrence_context'][0]['occurrences']=[]
    elif fault=='legacy':frozen['claim_issue_context'].pop('current_occurrence_anchor_scope')
    if fault:
        with pytest.raises(ValueError):r.effective_proposal(frozen,response)
    else:
        normalized=r.effective_proposal(frozen,response)
        actual=c.index(normalized.records)['claims/DK-72483dc200ad-CL002']
        assert actual['occurrences'][0]==before['claims/DK-72483dc200ad-CL002']['occurrences'][0]
        assert len(normalized.records)==7
        # This is mechanical normalization, never content approval.
        packet=r.initial_review_base(frozen,normalized)
        purpose=packet['claim_citation_purpose_review']
        assert len(purpose['items'])==1
        assert purpose['automatic_content_approval'] is False
        assert len(packet['proposal']['records'])==7
        response={'proposal_sha256':packet['proposal_sha256'],'verdicts':[
            dict(record_key=d['record_key'],approved=True,reason='test only',problems=[])
            for d in packet['proposal']['dispositions']]}
        with pytest.raises(ValueError,match='denominator'):c.validate_review(packet,response)
        verdict=dict(record_key=purpose['items'][0]['record_key'],approved=False,reason='context needed',problems=['uncertain'])
        response['verdicts'].append(verdict)
        assert c.validate_review(packet,response) is False
        verdict.update(approved=True,reason='diagnostic only',problems=[])
        assert c.validate_review(packet,response) is True


@pytest.mark.parametrize('fault',['missing','duplicate','changed','unresolved','hold','foreign','legacy','unbound_prefix','no_bound_support'])
def test_actual_complete_input_diagnostic_and_readonly_impact_gates(actual_direction,fault):
    path=next((Path(actual_direction['old_action_root']).parent.parent/'complete-input-repair-once/action').glob('*/proposal.raw.json'))
    frozen=r.body(r.read(path.parent/'scoped-materials.json'))
    raw=r.validate_raw(path.parent,'proposal',r.body(r.read(path.parent/'proposal.packet.json')))
    proposal=r.effective_proposal(frozen,raw['response'])
    extra=next(i for i in proposal.cvp_impacts if i.record_key.startswith('claim_relations/'))
    if fault=='missing':proposal.cvp_impacts.pop(next(n for n,i in enumerate(proposal.cvp_impacts) if i.record_key.startswith('canonical_viewpoints/')))
    elif fault=='duplicate':proposal.cvp_impacts.append(deepcopy(extra))
    elif fault=='changed':
        value=deepcopy(c.index(frozen['before_records'])[extra.record_key]);value['revision']+=1
        proposal.records.extend(c.rows({extra.record_key:value}))
    elif fault=='unresolved':extra.action='needs_direction'
    elif fault=='hold':
        before=c.index(frozen['before_records']);before[extra.record_key]['review_status']='human_review_required'
        frozen['before_records']=c.rows(before)
    elif fault=='foreign':frozen['inventory']['affected_keys'].remove(extra.record_key)
    elif fault=='legacy':frozen['claim_issue_context'].pop('complete_input_repair')
    else:
        assessment=next(a for a in proposal.claim_assessments if any('也是當時' in cite.exact_quote for cite in a.citations))
        diagnostic=next(cite for cite in assessment.citations if '也是當時' in cite.exact_quote)
        if fault=='unbound_prefix':diagnostic.exact_quote='這不但是彼得自己的錯誤，也是當時猶太人普遍的錯誤。'
        else:assessment.citations=[diagnostic]
    with pytest.raises((ValueError,KeyError)):r.initial_review_base(frozen,proposal)
