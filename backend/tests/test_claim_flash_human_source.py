"""Flash consumes durable source clarification, never inferred human approval."""
from copy import deepcopy
import json

import pytest
from backend.pipeline import claim_human_source as human, claim_flash_evidence as stage
from backend.pipeline import claim_flash_components as components, evidence_refresh as e
from backend.api.canonical_repository.postgres_store import ChangeSetConflict, POSTGRES_APPLY_ADVISORY_LOCK_KEY
from backend.tests.test_claim_flash_evidence import setup, saved_inventory, Ports, seeded
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def fact(sources, pk='S0001', supersedes=()):
    record={'schema_version':'wang_claim_human_source_verification_v1', 'verified_by':'user',
            'human_content_approval':False, 'user_confirmation':'已確認所指，未判斷錄音字音。',
            'answers':[{'question':'這裡指哪卷？', 'answer':'此處指約翰，保留原字面。',
                        'citation':{'source_id':'S','paragraph_key':pk,'exact_quote':sources['S']['paragraphs'][pk]['text']}}],
            'supersedes':{'S/'+pk:list(supersedes)}}
    return human.entry({'path':'/archived/confirmation.json','file_sha256':'a'*64},record,'S',sources['S'],[pk],'confirmation')


def save(store, values):
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        human.register(cursor,values,origin='isolated-human-source-fixture')


def test_material_preserves_raw_quotes_and_only_projects_relevant_current_facts(tmp_path):
    records,sources,_=setup(tmp_path);before=deepcopy((records,sources))
    facts=[fact(sources),fact(sources,'S0002')]
    value=stage.material('evidence_steps/E',records,sources,facts)
    assert len(value['human_source_decisions'])==1
    projected=value['human_source_decisions'][0]
    assert projected['decision_id']==facts[0]['decision_id'] and projected['applicability']=='current'
    assert projected['claim_content_approval'] is False
    assert value['original_text_excerpts']==[sources['S']['paragraphs']['S0001']['text']]
    assert (records,sources)==before
    parts=[{'role':'judgment','text':'此處的判斷。'}];ports=Ports()
    components.check(tmp_path/'jev',ports,'evidence_steps',records['evidence_steps/E']['payload'],value,parts,fragment_only=True)
    req=ports.calls[0][1]
    assert req['state']['human_source_decisions']==value['human_source_decisions']
    assert 'Claim已獲批准' in req['state']['source_interpretation']


def test_speaker_confirmation_reaches_update_without_changing_source_location(tmp_path):
    records, sources, _ = setup(tmp_path)
    key = 'evidence_steps/E'
    original = stage.material(key, records, sources)
    sample = fact(sources)
    record = sample['record']
    record['user_confirmation'] = '不是教授說的'
    record['answers'][0].update(question='這句是教授說的嗎？', answer='不是教授說的')
    decision = human.entry(sample['artifact'], record, 'S', sources['S'], ['S0001'], 'confirmation')

    material = stage.material(key, records, sources, [decision])
    assert material['fragments'] == original['fragments']
    assert material['original_text_excerpts'] == original['original_text_excerpts']
    ports = Ports()
    parts = [{'role': 'judgment', 'text': '核對說話者。'}]
    components.check(tmp_path/'speaker', ports, 'evidence_steps', records[key]['payload'],
                     material, parts, fragment_only=True)
    supplied = ports.calls[0][1]['state']['human_source_decisions'][0]
    assert supplied['record']['answers'][0]['answer'] == '不是教授說的'
    assert supplied['applicability'] == 'current'
    assert supplied['claim_content_approval'] is False


def test_unrelated_facts_preserve_existing_fingerprint_but_relevant_facts_invalidate_it(tmp_path):
    records,sources,_=setup(tmp_path);key='evidence_steps/E';p=records[key]['payload']
    original=stage.material(key,records,sources)
    expected=e.digest({'key':key,'record':stage.visible(p),'fragments':original['fragments']})
    assert stage.fingerprint(key,p,original)==expected
    assert stage.fingerprint(key,p,stage.material(key,records,sources,[fact(sources,'S0002')]))==expected
    assert stage.fingerprint(key,p,stage.material(key,records,sources,[fact(sources)]))!=expected


def test_changed_paragraph_requires_reconfirmation_even_if_bound_quote_still_matches(tmp_path):
    records,sources,_=setup(tmp_path);value=fact(sources)
    sources['S']['paragraphs']['S0001']['text']+='新增限定。'
    fragment=records['source_fragments/F0']['payload']
    fragment['paragraph_text_sha256']=human.text_sha(sources['S']['paragraphs']['S0001']['text'])
    with pytest.raises(ValueError,match='needs reconfirmation'):
        stage.material('evidence_steps/E',records,sources,[value])
    corrupt=deepcopy(value);corrupt['record']['answers'][0]['answer']='冒充確認'
    with pytest.raises(ValueError,match='identity drift'):
        stage.material('evidence_steps/E',records,sources,[corrupt])


def test_new_source_file_with_same_paragraph_keeps_confirmation(tmp_path):
    records,sources,_=setup(tmp_path);value=fact(sources)
    sources['S']['file']['file_sha256']='b'*64
    projected=stage.material('evidence_steps/E',records,sources,[value])['human_source_decisions'][0]
    assert projected['source_version_changed'] and projected['applicability']=='current'


def test_gpt_gets_same_authority_and_completed_unrelated_record_is_not_reviewed_again(seeded):
    store,sources,plan,previous,root=seeded
    class Check(Ports):
        def repair(self,req):
            if req['input']['task']=='evidence_fidelity_repair':
                job=req['input']['jobs'][0]
                assert job['human_source_decisions'][0]['applicability']=='current'
                assert job['source_interpretation']==stage.SOURCE_INTERPRETATION
            return super().repair(req)
    save(store,[fact(sources)])
    result=stage.run(store,'S',plan,root,Check(),previous,group_ids=['A'],apply=True)
    assert result['status']=='completed'
    # Adding an unrelated paragraph confirmation changes neither input nor cache.
    save(store,[fact(sources,'S0002')])
    ports=Ports();again=stage.run(store,'S',plan,root,ports,previous,group_ids=['A'],apply=True)
    assert again['status']=='completed' and ports.calls==[]


@pytest.mark.parametrize('mode',['noop','replay','transaction'])
def test_changed_human_evidence_blocks_stale_noop_replay_and_actual_transaction(seeded,mode):
    store,sources,plan,previous,root=seeded;first=fact(sources);save(store,[first])
    result=stage.run(store,'S',plan,root,Ports(),previous,group_ids=['A'],apply=True)
    assert result['status']=='completed'
    guard={'paragraphs':{'S':['S0001']},'entries':[first]}
    replacement=fact(sources,supersedes=[first['decision_id']]);save(store,[replacement])
    before=store.get_record_state('evidence_steps','E')
    if mode=='transaction':
        post={**before['payload'],'statement':'不應寫入。'}
        sqlplan=store.plan_package({'package_id':'stale-human-proof','evidence_steps':[post]},source_kind=stage.VERSION)
        with pytest.raises(ChangeSetConflict,match='human source decisions changed'):
            store.apply_plan(sqlplan,expected_current_human_source_scope=guard)
    else:
        phases=list((root/'groups'/'A').glob('*/verification.json'));assert len(phases)==1
        phase=phases[0].parent
        if mode=='noop':
            value=json.loads(phases[0].read_text());value['updates']={}
            # A separate synthetic no-op stage, without altering original review.
            other=root/'stale-noop';e.save(other/'verification.json',value)
            e.save(other/'before.json',json.loads((phase/'before.json').read_text()));phase=other
        with pytest.raises(ChangeSetConflict,match='human source decisions changed'):
            e.commit_verified(store,phase)
    assert store.get_record_state('evidence_steps','E')==before


@pytest.mark.parametrize('registered_before_lower',[True,False])
def test_claim_layer_consumes_same_lower_confirmation_without_reauditing_lower(seeded,registered_before_lower):
    from backend.pipeline import claim_flash_statements as statements, claim_update_flash as flash, claim_flash_pairs as pairs
    from backend.tests.test_claim_update_flash import Ports as ClaimPorts
    store,sources,plan,previous,root=seeded
    plan['groups'][0]['claim_ids']=['C'];first=fact(sources)
    if registered_before_lower:save(store,[first])
    assert stage.run(store,'S',plan,root,Ports(),previous,group_ids=['A'],apply=True)['status']=='completed'
    if not registered_before_lower:save(store,[first])
    lower=store.get_record_state('evidence_steps','E')
    records,current=stage.f.load_group_source(store,'S',plan['groups'][0])
    old=stage.material('evidence_steps/E',records,sources,[first])
    inherited=stage.material('evidence_steps/E',records,current)
    assert inherited==old and len(inherited['human_source_decisions'])==1
    assert flash.claim_material('claims/C',records,current,'S')['evidence'][0]['human_source_decisions']==old['human_source_decisions']
    assert pairs.node('claims/C',records,current)['human_source_decisions']==old['human_source_decisions']
    ports=ClaimPorts();result=statements.run(store,'S',['C'],root,root/'claim-statements',ports,apply=True)
    assert not result['pending'] and store.get_record_state('evidence_steps','E')==lower
    assert all('record_fidelity' not in req.get('questions',{}) for role,req in ports.calls if role=='jev')
    assert all(req['state']['evidence'][0]['human_source_decisions'] for role,req in ports.calls if role=='jev')
    save(store,[fact(sources,supersedes=[first['decision_id']])])
    with pytest.raises(ChangeSetConflict,match='human source decisions changed'):
        statements.run(store,'S',['C'],root,root/'claim-statements',ClaimPorts(),apply=True)


def test_cross_group_attached_evidence_consumes_existing_human_proof(seeded):
    from backend.pipeline import claim_flash_statements as statements, claim_update_flash as flash
    from backend.tests.test_claim_update_flash import Ports as ClaimPorts

    store, sources, plan, previous, root = seeded
    group = plan['groups'][1]
    group['claim_ids'] = ['C']
    original = store.get_record_state('claims', 'C')['payload']
    store.apply_plan(store.plan_package({
        'package_id': 'cross-group-attachment',
        'claims': [{**original, 'evidence_step_ids': []}],
        'knowledge_relations': [{'relation_id': 'EC', 'from_id': 'E', 'to_id': 'C',
                                 'relation_type': 'supports', 'record_namespace': 'claim_update_flash_v1',
                                 'reason': '原文支持這項主張。'}],
    }))
    confirmation = fact(sources)
    save(store, [confirmation])
    assert stage.run(store, 'S', plan, root, Ports(), previous, apply=True)['status'] == 'completed'

    records, current = stage.f.load_group_source(store, 'S', group)
    assert current['S']['human_source_guard']['entries'] == [confirmation]
    assert flash.claim_material('claims/C', records, current, 'S')['evidence'][0]['human_source_decisions']
    ports = ClaimPorts()
    result = statements.run(store, 'S', ['C'], root, root/'cross-group-statements', ports, apply=True)
    assert not result['pending']
    assert all('record_fidelity' not in req.get('questions', {}) for role, req in ports.calls if role == 'jev')
    replay = ClaimPorts()
    statements.run(store, 'S', ['C'], root, root/'cross-group-statements', replay, apply=True)
    assert replay.calls == []
