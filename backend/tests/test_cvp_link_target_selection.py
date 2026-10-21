"""Real g093 proposition regression; simulated decisions are not approval."""
from copy import deepcopy
import os
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline import cvp_link_target_selection as flow

CLAIM = 'DK-a26f0a7e9ba4-P03-CL002'
OLD = 'CV-4876c434e8af1c36dc6f'
NEW = 'CV-a797c5fbc4c09e117142'
LINK = 'VCL-dbbb7d4da0bffd23d248'
STATEMENT = '兩次餵食神蹟顯明耶穌有能力餵養猶太人，也有能力餵養外邦人，而且食物仍有剩餘。'


def fixture(monkeypatch):
    # Minimal structural fixture with the actual three propositions. Full
    # production shapes/current source SHA are covered by the opt-in SQL test.
    claim = {'claim_id':CLAIM,'revision':3,'statement':STATEMENT,'review_status':'ai_consensus_reviewed'}
    old = {'viewpoint_claim_link_id':LINK,'claim_id':CLAIM,'pinned_claim_revision':3,
           'revision':2,'viewpoint_id':OLD,'validated_against_viewpoint_revision_id':'old-cvr',
           'effective_state':'active','review_status':'system_approved','link_type':'equivalent_component'}
    payloads = {('claims',CLAIM):claim,('viewpoint_claim_links',LINK):old}
    for vid, rid, text in [(OLD,'old-cvr','两次喂饱神迹的受众分别是犹太人与外邦人。'),
                           (NEW,'new-cvr','即使门徒没有带饼，耶稣仍能供应他们的食物需要。')]:
        payloads['canonical_viewpoints',vid] = {'viewpoint_id':vid,'revision':1,
            'current_revision_id':rid,'review_status':'system_approved'}
        payloads['viewpoint_revisions',rid] = {'viewpoint_revision_id':rid,'viewpoint_id':vid,
            'revision':1,'core_proposition':text,'review_status':'system_approved'}
    member = {'claim_id':'member','revision':3,'review_status':'ai_consensus_reviewed',
              'statement':'曾餵飽五千人和四千人的耶穌也能供應門徒的需要。'}
    payloads['claims','member'] = member
    payloads['viewpoint_claim_links','member-link'] = {**old,'viewpoint_claim_link_id':'member-link',
        'claim_id':'member','viewpoint_id':NEW,'validated_against_viewpoint_revision_id':'new-cvr'}
    payloads['source_documents','source'] = {'source_id':'source','revision':1}
    records = {k:{'revision':v['revision'],'content_sha256':record_content_sha(v),
                  'payload':v,'retired':False} for k,v in payloads.items()}
    calls = []
    class Store:
        def get_record(self,c,i):return deepcopy(payloads[c,i])
        def read_cvp_context(self,claims,*,viewpoint_ids):
            calls.append((claims,viewpoint_ids)); return deepcopy(records)
    monkeypatch.setattr(flow,'load_context_source',lambda doc:('/exact/source','a'*64,
        SimpleNamespace(body_sha256='b'*64,spoken_rows=[{'text':'完整自身全文，不截断'}])))
    return Store(), records, calls


def selected(**changes):
    return {'selection':{'claim_id':CLAIM,'diagnosis':'different_target','viewpoint_id':NEW,
        'link_type':'supports','reason':'TEST ONLY: prior feeding ability supplies an argument for supply.',
        'old_target_reason':'TEST ONLY: ability does not establish audience identities.',**changes}}


def test_real_case_compares_ability_with_supply_not_only_audience_or_link_type(monkeypatch):
    store, records, calls = fixture(monkeypatch); original = deepcopy(records)
    packet = flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    assert packet['claim']['statement'] == STATEMENT
    assert packet['old_target']['current_revision']['core_proposition'] != packet['candidates'][0]['current_revision']['core_proposition']
    assert packet['candidates'][0]['member_claims'][0]['statement'].startswith('曾餵飽')
    result = flow.validate_selection(packet,selected())
    assert result['next_stage'] == 'exact_target_postimage_review'
    assert result['semantic_approval'] is False and result['registry_writes'] == 0
    assert records == original and calls == [([CLAIM],[OLD,NEW])]


@pytest.mark.parametrize('changes',[{'viewpoint_id':OLD},{'viewpoint_id':'foreign'},
    {'claim_id':'foreign'},{'link_type':None},{'diagnosis':'same_target'},
    {'diagnosis':'unresolved'},{'diagnosis':'no_existing_target'}])
def test_diagnosis_cannot_hide_endpoint_change_or_foreign_claim(monkeypatch,changes):
    store,_,_ = fixture(monkeypatch)
    packet = flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    with pytest.raises(ValueError):flow.validate_selection(packet,selected(**changes))


def test_no_existing_target_routes_to_normal_coverage_not_defer(monkeypatch):
    store,_,_ = fixture(monkeypatch)
    packet = flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    result = flow.validate_selection(packet,selected(diagnosis='no_existing_target',viewpoint_id=None,link_type=None))
    assert result['next_stage'] == 'normal_new_viewpoint'


def test_retained_member_is_affected_audit_scope_not_added_to_original_members(monkeypatch):
    store,_,_=fixture(monkeypatch)
    packet=flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    group={'claim_ids':['current-original'],'reviewed':{'evidence':{'viewpoint_ids':[OLD]}}}
    before=deepcopy(group)
    flow.validate_group_scope(group,packet)
    assert group==before
    group['reviewed']['evidence']['viewpoint_ids']=[NEW]
    with pytest.raises(ValueError):flow.validate_group_scope(group,packet)


@pytest.mark.parametrize('candidates',[[],[NEW,NEW],[OLD],['missing']])
def test_candidates_are_exact_not_fuzzy_or_old_target(monkeypatch,candidates):
    store,_,_ = fixture(monkeypatch)
    with pytest.raises((ValueError,KeyError)):flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=candidates)


@pytest.mark.parametrize('drift',['sha','claim-version','member-version','target-version','hold','missing-source'])
def test_stale_or_held_graph_never_becomes_a_proposal(monkeypatch,drift):
    store,records,_ = fixture(monkeypatch)
    if drift == 'sha':records['claims',CLAIM]['content_sha256']='0'*64
    elif drift == 'missing-source':del records['source_documents','source']
    else:
        key = ('claims',CLAIM) if drift == 'hold' else ('viewpoint_claim_links','member-link')
        p = records[key]['payload']
        if drift == 'claim-version':
            key = ('claims',CLAIM); p = records[key]['payload']; p['revision']+=1; records[key]['revision']+=1
        elif drift == 'member-version':p['pinned_claim_revision']=2
        elif drift == 'target-version':p['validated_against_viewpoint_revision_id']='foreign'
        elif drift == 'hold':p['review_status']='human_review_required'
        records[key]['content_sha256']=record_content_sha(p)
    with pytest.raises(ValueError):flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])


def test_new_prompt_and_lossless_identity_not_old_supports_review(monkeypatch):
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter,PROMPT
    store,_,_ = fixture(monkeypatch)
    packet = flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    client = subscription_client('codex','gpt-6.1-sol','high')
    new = flow.adapter(client); old = link_review_adapter(client,PROMPT,contextual_semantics=True)
    assert new.prompt_sha256 != old.prompt_sha256
    proof = new.request_encoding_proof(packet)
    assert proof['exact_roundtrip_verified'] is True
    assert proof['restored_payload_sha256'] == flow.sha256_json(packet)
    assert new.model_id == 'gpt-6.1-sol' and new.backend == 'codex_subscription'


def review_fixture(monkeypatch):
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    store,_,_ = fixture(monkeypatch)
    packet = flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    calls=[]
    class Ports:
        def adapter(self,stage):
            obj = (flow.adapter(subscription_client('codex','gpt-6.1-sol','high')) if stage=='proposal'
                   else flow.review_adapter(subscription_client('claude','claude-opus-5-5','high')))
            # This fixture has no CLI execution or executable identity proof.
            obj._client.executable = None
            def generate(p):
                calls.append(stage)
                return selected() if stage=='proposal' else {'verdicts':[{'claim_id':CLAIM,
                    'approved':True,'problems':[],'reason':'SIMULATED test, no production approval'}]}
            obj.generate=generate
            return obj
    return store,packet,Ports(),calls


def test_single_selection_and_review_resume_without_models_or_sql_writes(monkeypatch,tmp_path):
    store,packet,ports,calls=review_fixture(monkeypatch)
    first=flow.review_selection(store,packet,root=tmp_path,ports=ports)
    second=flow.review_selection(store,packet,root=tmp_path,ports=ports)
    assert first==second and calls==['proposal','initial_review']
    assert first['selection_reviewed'] is True and first['semantic_approval'] is False
    assert first['registry_writes']==0 and first['next_stage']=='exact_target_postimage_review'


def test_selection_reserved_unknown_is_not_reissued(monkeypatch,tmp_path):
    from backend.pipeline.source_reconciliation_runner import Stop,write
    store,packet,ports,calls=review_fixture(monkeypatch)
    root=tmp_path/('selection-'+flow.sha256_json({'link_id':LINK,'revision':2}))
    write(root/'proposal.reservation.json',{'existing_unknown':True})
    with pytest.raises(Stop,match='do not repeat'):flow.review_selection(store,packet,root=tmp_path,ports=ports)
    assert calls==[]


def test_candidate_input_change_cannot_reset_old_revision_call_budget(monkeypatch,tmp_path):
    store,packet,ports,calls=review_fixture(monkeypatch)
    flow.review_selection(store,packet,root=tmp_path,ports=ports)
    changed=deepcopy(packet); changed['candidates'][0]['current_revision']['core_proposition']='different input'
    with pytest.raises(ValueError):flow.review_selection(store,changed,root=tmp_path,ports=ports)
    assert calls==['proposal','initial_review']


def test_independent_rejection_preserved_without_correction(monkeypatch,tmp_path):
    from backend.pipeline.source_reconciliation_runner import Stop
    store,packet,ports,calls=review_fixture(monkeypatch)
    original=ports.adapter
    def rejected(stage):
        obj=original(stage)
        if stage=='initial_review':
            def generate(p):
                calls.append(stage)
                return {'verdicts':[{'claim_id':CLAIM,'approved':False,'problems':['not supported'],
                                    'reason':'SIMULATED rejection'}]}
            obj.generate=generate
        return obj
    ports.adapter=rejected
    for _ in range(2):
        with pytest.raises(Stop,match='independently rejected'):
            flow.review_selection(store,packet,root=tmp_path,ports=ports)
    assert calls==['proposal','initial_review']


@pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_ACCEPTANCE')!='1',reason='explicit read-only real g093 graph/source acceptance')
def test_actual_g093_sql_graph_and_complete_sources_without_models_or_writes():
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env'); store=PostgresKnowledgeStore()
    packet=flow.build_packet(store,link_id=LINK,candidate_viewpoint_ids=[NEW])
    assert packet['claim']['statement']==STATEMENT
    assert packet['candidates'][0]['viewpoint']['current_revision_id']=='CVR-6c46eb0d54e50a52fd5c'
    assert packet['candidates'][0]['links'] and packet['sources']
    own=[s for s in packet['sources'] if s['source_id']=='SRC-2016_NYSC_1-a26f0a7e9ba4']
    assert len(own)==1 and own[0]['file_sha256']=='579e257121149b5f9f8316781c5713995008f6e9ee40d5d30aa482dfd413f287'
    assert len(own[0]['paragraphs'])==18
    assert packet['registry_writes']==0 and packet['semantic_approval'] is False
