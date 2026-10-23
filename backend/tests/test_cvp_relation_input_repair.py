"""An omitted relation may repair input, never approve a changed postimage."""
from copy import deepcopy
import hashlib
import json

import pytest

from backend.api.canonical_repository import cvp_management_store as persistence
from backend.api.canonical_repository.cvp_recall import seal
from backend.api.canonical_repository.postgres_store import ChangeSetConflict, canonical_json, record_content_sha
from backend.pipeline.cvp_management_runner import _omitted_duplicate_relations, _reconcile_capacity_refusals
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres


def scenario(setup):
    store,_,_,_,_=setup
    payload={'viewpoint_relation_id':'Q1','source_viewpoint_id':'V1','target_viewpoint_id':'V2',
             'relation_type':'tensions_with','effective_state':'active','review_status':'system_approved','visibility':'internal'}
    with store.connect() as conn:
        conn.execute("""INSERT INTO wang_knowledge.objects
            (collection,object_id,revision,content_sha256,payload,review_status,visibility)
            VALUES('viewpoint_relations','Q1',1,%s,%s::jsonb,'system_approved','internal')""",
            (record_content_sha(payload),canonical_json(payload)))
        conn.execute("""INSERT INTO wang_knowledge.edges
            (edge_collection,edge_id,from_id,to_id,relation_type,revision,payload)
            VALUES('viewpoint_relations','Q1','V1','V2','tensions_with',1,%s::jsonb)""",
            (canonical_json(payload),))
    relation=store.get_record_state('viewpoint_relations','Q1')
    old=seal({'schema_version':'wang_cvp_common_material_v1','records':[],
              'claim_input':{'unchanged':'C1'},'candidate_viewpoint_ids':['V1','V2']})
    bundle=seal({**{k:v for k,v in old.items() if k!='artifact_sha256'},
                 'records':[{'collection':'viewpoint_relations','object_id':'Q1',**relation}]})
    parent={'kind':'cvp_management_action','action_id':'repair-parent','result':{'status':'partial'},
            'units':{'g':{'latest_step':'repair-step'}},'call_reservations':{'original':'returned'},
            'request':{'budget':{'max_calls':7}}}
    step={'kind':'cvp_management_round','parent_run_id':'repair-parent','group_key':'g',
          'checkpoint':'reviewed','change_set_ids':[],'materials':{'bundle':old},
          'reviewed_sha256':'original-review','reviewed':{
              'plan':{'change_set_id':'unwritten-plan','operations':[{
                  'collection':'viewpoint_relations','operation':'create',
                  'payload':{**payload,'viewpoint_relation_id':'new-duplicate'}}]},
              'evidence':{'read_pins':[]}}}
    with store.connect() as conn,conn.cursor() as cur:
        persistence._insert(cur,'repair-parent',parent)
        persistence._insert(cur,'repair-step',step)
    return store,bundle,{('viewpoint_relations','Q1'):relation},step


def test_repair_reservation_keeps_original_approval_calls_and_budget(setup):
    store,bundle,relations,step=scenario(setup)
    assert _omitted_duplicate_relations(store,step)==relations
    kwargs={'bundle':bundle,'relations':relations,'implementation':[{'fixed':'source-sha'}]}
    proof=persistence.reserve_missing_relation_repair(store,'repair-parent','repair-step',**kwargs)
    assert proof['requires_fresh_proposal_and_review'] is True
    assert proof['max_repair_generations']==1
    assert persistence.reserve_missing_relation_repair(store,'repair-parent','repair-step',**kwargs)==proof
    assert store.get_cvp_group('repair-step')=={**step,'run_id':'repair-step','status':'running','error':None}
    saved=store.get_cvp_group('repair-parent')
    assert saved['request']['budget']['max_calls']==7
    assert saved['call_reservations']=={'original':'returned'}
    with pytest.raises(ChangeSetConflict,match='already reserved'):
        persistence.reserve_missing_relation_repair(store,'repair-parent','repair-step',
            **{**kwargs,'implementation':[{'different':'code'}]})


@pytest.mark.parametrize('change',['applied','known_relation','different_edge','changed_claim','stale_relation'])
def test_repair_rejects_non_input_defects_and_current_drift(setup,change):
    store,bundle,relations,step=scenario(setup)
    if change=='applied':step['checkpoint']='applied'
    elif change=='known_relation':
        step['materials']['bundle']=bundle
    elif change=='different_edge':
        step['reviewed']['plan']['operations'][0]['payload']['relation_type']='supports'
    elif change=='changed_claim':
        bundle=seal({**{k:v for k,v in bundle.items() if k!='artifact_sha256'},'claim_input':{'changed':'C2'}})
    else:
        with store.connect() as conn:
            conn.execute("UPDATE wang_knowledge.objects SET revision=2 WHERE collection='viewpoint_relations' AND object_id='Q1'")
    with store.connect() as conn,conn.cursor() as cur:persistence._save(cur,'repair-step',step)
    with pytest.raises(ChangeSetConflict):
        persistence.reserve_missing_relation_repair(store,'repair-parent','repair-step',
            bundle=bundle,relations=relations,implementation=[{'fixed':'source-sha'}])
    assert 'relation_input_repairs' not in store.get_cvp_group('repair-parent')


def test_fragment_repair_binds_own_returned_review_and_does_not_reset_budget(setup):
    store,_,_,_=scenario(setup)
    with store.connect() as conn,conn.cursor() as cur:
        parent=persistence._load(cur,'repair-parent');step=persistence._load(cur,'repair-step')
        step.update(checkpoint='prepared');step['materials']['step_root']='/original/step'
        parent['units']['g'].update(status='resource_hold',steps=['repair-step'])
        parent['call_reservations']={'review':{'role':'opus','state':'returned',
            'intent':{'cache_path':'/original/step/review.json'},
            'returned':{'response':{'result':'review','review':{'findings':[{'category':'source','reason':'Select only the relevant fragment.'}]}}}}}
        persistence._save(cur,'repair-parent',parent);persistence._save(cur,'repair-step',step)
    kwargs={'group_key':'g','review_call_key':'review','defect':'Output contract cannot select a subset of a shared EvidenceStep.',
            'implementation':[{'fixed':'source-sha'}]}
    proof=persistence.reserve_fragment_selection_repair(store,'repair-parent',**kwargs)
    assert proof['requires_fresh_proposal_and_review'] is True
    assert persistence.reserve_fragment_selection_repair(store,'repair-parent',**kwargs)==proof
    assert store.get_cvp_group('repair-parent')['request']['budget']==parent['request']['budget']
    with pytest.raises(ChangeSetConflict):
        persistence.reserve_fragment_selection_repair(store,'repair-parent',**{**kwargs,'defect':'different defect'})
    with store.connect() as conn,conn.cursor() as cur:
        parent=persistence._load(cur,'repair-parent')
        parent['call_reservations']['unknown']={'state':'started','intent':{'cache_path':'/original/step/proposal.json'}}
        persistence._save(cur,'repair-parent',parent)
    with pytest.raises(ChangeSetConflict,match='unknown'):
        persistence.reserve_fragment_selection_repair(store,'repair-parent',**kwargs)


@pytest.mark.parametrize('case',['capacity','partial_stdout','no_exit','response_file','intent_drift'])
def test_capacity_classification_preserves_unknown_output_and_spent_call(setup,tmp_path,monkeypatch,case):
    store,_,_,_=scenario(setup)
    cache=tmp_path/'proposal.json';transport=tmp_path/'proposal.transport';transport.mkdir()
    intent={'backend':'codex_subscription','cache_path':str(cache),'model_id':'gpt-6.1-sol'}
    formal=seal({'schema_version':'wang_model_call_intent_v1',**intent,'automatic_retry':False})
    expected=deepcopy(intent)
    monkeypatch.setattr('backend.pipeline.cvp_management_runner._dispatch_identities',
                        lambda *args:(expected,expected))
    failure=seal({'invocation_sha256':formal['artifact_sha256'],'execution_status':'outcome_unknown'})
    (tmp_path/'proposal.call-intent.json').write_text(json.dumps(formal))
    (tmp_path/'proposal.call-failure.json').write_text(json.dumps(failure))
    (transport/'invocation.json').write_text('{}')
    (transport/'stdout.log').write_text('partial' if case=='partial_stdout' else '')
    (transport/'stderr.log').write_text('ERROR: Selected model is at capacity. Please try a different model.\n')
    if case!='no_exit':
        (transport/'exit.json').write_text(json.dumps({'returncode':1,'files':{
            p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [transport/'stdout.log',transport/'stderr.log']}}))
    if case=='response_file':(transport/'response.json').write_text('{}')
    if case=='intent_drift':intent['model_id']='different-model'
    with store.connect() as conn,conn.cursor() as cur:
        parent=persistence._load(cur,'repair-parent')
        slot={'role':'gpt','state':'started','intent':intent,'intent_sha256':'original'}
        parent['call_reservations']={'original':slot};persistence._save(cur,'repair-parent',parent)
    _reconcile_capacity_refusals(store,'repair-parent')
    parent=store.get_cvp_group('repair-parent');saved=parent['call_reservations']['original']
    assert saved['state']==('refused' if case=='capacity' else 'started')
    assert len(parent['call_reservations'])==1 and parent['request']['budget']['max_calls']==7
    assert json.loads((tmp_path/'proposal.call-failure.json').read_text())==failure
    if case=='capacity':assert saved['refusal']['automatic_retry'] is False


@pytest.mark.parametrize('version,ids,approved',[
    ('candidate_one_hop_v1',['Q1','Q2'],True),
    ('candidate_one_hop_v1',['Q1'],False),
    ('candidate_one_hop_v1',['Q1','Q2','Q3'],False),
    (None,['Q1'],True),(None,['Q1','Q2'],True),(None,['Q2'],False),
])
def test_sql_relation_cut_matches_candidate_one_hop_and_preserves_historical_inputs(version,ids,approved):
    from backend.api.canonical_repository.cvp_management_authority import validate_relationship_scope
    edges={'Q1':('A','Z'),'Q2':('B','W'),'Q3':('Z','W')}
    class Cursor:
        def execute(self,query,params):
            self.rows=[(oid,) for oid,ends in edges.items() if set(ends)&set(params[0])]
            return self
        def fetchall(self):return self.rows
    bundle={'candidate_viewpoint_ids':['A','B'],'maintenance_viewpoint_ids':['A']}
    if version:bundle['relationship_scope']=version
    records={('viewpoint_relations',oid):{'payload':{'effective_state':'active'}} for oid in ids}
    if approved:validate_relationship_scope(Cursor(),records,bundle)
    else:
        with pytest.raises(ValueError,match='relationship scope changed'):
            validate_relationship_scope(Cursor(),records,bundle)
