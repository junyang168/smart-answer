"""Specific CVP2 admission regressions on socket-only disposable PostgreSQL."""
from copy import deepcopy
import hashlib

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository import cvp_management_store as persistence
from backend.api.canonical_repository.postgres_store import ChangeSetConflict, canonical_json
from backend.pipeline import cvp_management_control as control
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from test_cvp_management_runner import postgres_store, _temporary_postgres
from test_cvp_management import disputed, decision


def seed_run(store, ident, value):
    with store.connect() as conn:
        conn.execute('''INSERT INTO wang_knowledge.pipeline_runs
            (run_id,subject_kind,subject_id,stage,trigger,status,metadata)
            VALUES(%s,'batch',%s,'cvp_group','cli','running',%s::jsonb)''',
            (ident,ident,canonical_json(value)))


@pytest.mark.parametrize('forgery',['sibling','unregistered','wrong_claims'])
def test_completed_unit_cannot_borrow_an_audited_sibling_or_foreign_step(postgres_store,forgery):
    store=postgres_store
    parent={'kind':'cvp_management_action','groups':[{'group_key':'g1','claim_ids':['C1']},
            {'group_key':'g2','claim_ids':['C2']}],
        'units':{'g1':{'status':'pending','steps':['step1'],'claim_ids':['C1']},
                 'g2':{'status':'pending','steps':['step2'],'claim_ids':['C2']}},
        'result':{'status':'running'}}
    step={'kind':'cvp_management_round','checkpoint':'audited','parent_run_id':'parent',
          'group_key':'g1','claim_ids':['C1']}
    if forgery=='sibling':step.update(group_key='g2',claim_ids=['C2'])
    if forgery=='wrong_claims':step['claim_ids']=['C2']
    stepid='missing-in-unit' if forgery=='unregistered' else 'step1'
    seed_run(store,'parent',parent);seed_run(store,stepid,step)
    with pytest.raises(ChangeSetConflict,match='readback/closure'):
        persistence.progress(store,'parent',group_key='g1',status='completed',step_id=stepid)
    current=store.get_cvp_group('parent')
    assert current['units']==parent['units'] and current['result']==parent['result']


def test_human_supersession_preview_and_persistence_preserve_original_protection(postgres_store,tmp_path):
    store=postgres_store
    req,bundle,candidate,args=disputed()
    dispute=domain.build_content_dispute(req,bundle,**args)
    parent={'kind':'cvp_management_action','request':req.model_dump(mode='json'),
        'action_id':req.action_id,'execution_root':str(tmp_path/'original-action'),'human_constraints':[],
        'units':{bundle['group_key']:{'status':'human_required','steps':['step']}}}
    step={'kind':'cvp_management_round','parent_run_id':'parent','group_key':bundle['group_key'],
          'content_dispute':dispute,'checkpoint':'prepared'}
    seed_run(store,'parent',parent);seed_run(store,'step',step)
    def manifest(value):
        parsed=domain.HumanDecision.model_validate(value).model_dump(mode='json')
        path=tmp_path/(value['decision_id']+'.json')
        direction={'schema_version':domain.VERSION+'_human_direction','action_id':req.action_id,
            'dispute_sha256':dispute['artifact_sha256'],
            'decision':{k:v for k,v in parsed.items() if k!='authorization'}}
        write_immutable(path,direction)
        parsed['authorization']={'path':str(path),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        return {'schema_version':domain.VERSION+'_control','operation':'human_constraint',
                'parent_run_id':'parent','step_id':'step','decision':parsed}
    held=control.run(store,manifest(decision(kind='hold',ident='H1')),execute=True)
    replacement=decision(kind='interpretation',ident='H2')
    replacement['supersedes_constraint_ids']=[held['constraint_id']]
    direction=manifest(replacement)
    preview=control.run(store,direction)
    actual=control.run(store,direction,execute=True)
    assert actual==preview and actual['supersedes_constraint_ids']==[held['constraint_id']]
    facts=store.get_cvp_group('parent')['human_constraints']
    assert facts==[held,actual]
    assert held['kind']=='hold' and actual['approval_granted'] is False and actual['budget_reset'] is False
    assert control.run(store,direction,execute=True)==actual
    assert store.get_cvp_group('parent')['human_constraints']==facts


@pytest.mark.parametrize('qualification',[None,{'ready':False,'reason':'unreviewed'}])
def test_changed_non_target_member_requires_current_qualification_but_readonly_does_not(postgres_store,qualification):
    from backend.api.canonical_repository.cvp_management_authority import validate_claim_authority
    bundle={'claim_input':{'claims':[]},'claim_authority':[],
            'member_claim_authority':[{'claim_id':'member','qualification':qualification}], 'records':[]}
    changed={'viewpoint_claim_links':[{'claim_id':'member','effective_state':'active'}]}
    with postgres_store.connect() as conn,conn.cursor() as cursor:
        validate_claim_authority(cursor,bundle,package={})
        validate_claim_authority(cursor,bundle,package={'viewpoint_claim_links':[{'claim_id':'member','effective_state':'retired'}]})
        with pytest.raises(ValueError,match='needs_upstream: changed member'):
            validate_claim_authority(cursor,bundle,package=changed)


def test_unqualified_legacy_member_allows_only_mechanical_revision_repin(postgres_store):
    from backend.api.canonical_repository.cvp_management_authority import validate_claim_authority
    old={'viewpoint_claim_link_id':'link','claim_id':'member','viewpoint_id':'view',
         'effective_state':'active','pinned_claim_revision':3,'link_type':'supports',
         'validated_against_viewpoint_revision_id':'old-revision','revision':1}
    bundle={'claim_input':{'claims':[]},'claim_authority':[],
            'member_claim_authority':[{'claim_id':'member','qualification':None}],
            'records':[{'collection':'claims','object_id':'member','revision':3,
                        'content_sha256':'a'*64},
                       {'collection':'viewpoint_claim_links','object_id':'link','payload':old}]}
    repin={**old,'validated_against_viewpoint_revision_id':'new-revision','revision':2,
           'decision_id':'reviewed-decision'}
    with postgres_store.connect() as conn,conn.cursor() as cursor:
        validate_claim_authority(cursor,bundle,package={'viewpoint_claim_links':[repin]})
        with pytest.raises(ValueError,match='needs_upstream: changed member'):
            validate_claim_authority(cursor,bundle,package={'viewpoint_claim_links':[
                {**repin,'link_type':'equivalent_component'}]})


@pytest.mark.parametrize('checkpoint',['applied','audited'])
def test_changed_member_qualification_authenticates_saved_round_pins_and_current_objections(postgres_store,checkpoint):
    from backend.api.canonical_repository.cvp_management_authority import validate_claim_authority
    sha='a'*64
    binding={'path':'/isolated/upstream-readback.json','file_sha256':'b'*64}
    q={'ready':True,'run_id':'member-qualification','revision':3,'content_sha256':sha,
       'validation_kind':'opus_and_program_readback','independent_audit_complete':False,'audit':binding}
    round_={'kind':'claim_management_round','checkpoint':checkpoint,'outcome':'completed',
            'program_readback_complete':True,'revisit_required':False,
            'stage_results':{'readback':binding},
            'audited_pins':[{'collection':'claims','object_id':'member','pin':[3,sha,False]}]}
    seed_run(postgres_store,'member-qualification',round_)
    bundle={'claim_input':{'claims':[]},'claim_authority':[],
            'member_claim_authority':[{'claim_id':'member','qualification':q}],
            'records':[{'collection':'claims','object_id':'member','revision':3,'content_sha256':sha}]}
    changed={'viewpoint_claim_links':[{'claim_id':'member','effective_state':'active'}]}
    with postgres_store.connect() as conn,conn.cursor() as cursor:
        validate_claim_authority(cursor,bundle,package=changed)
        stale=deepcopy(bundle);stale['member_claim_authority'][0]['qualification']['content_sha256']='c'*64
        with pytest.raises(ValueError,match='exact current version'):
            validate_claim_authority(cursor,stale,package=changed)
    blocked={'kind':'claim_management_round','blocked_current_claim_ids':['member'],
             'materials':{'before':{'claims/member':{'revision':3,'content_sha256':sha}}}}
    seed_run(postgres_store,'member-objection',blocked)
    with postgres_store.connect() as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='formal upstream finding'):
            validate_claim_authority(cursor,bundle,package=changed)
