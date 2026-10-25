"""A later legitimate action must not erase an earlier no-change judgment."""
import json
import os
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest
from backend.api.canonical_repository.postgres_store import canonical_json,ChangeSetConflict
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline import cvp_retained_impact_resolution as human
from backend.tests.test_cvp_sql_transaction_postgres import task,apply
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def prepare_case(store,tmp_path,monkeypatch):
    original=store.review_cvp_group
    bound={}
    def first(run,*,plan,evidence):
        hp=tmp_path/'human.json';hp.write_text(json.dumps({'group_id':run,'human_exact_disposition':True,
            'decision':'retain_existing_contextual_equivalence_unchanged','items':[{'link_id':'L'}]}))
        sp=tmp_path/'scope.json';sp.write_text(json.dumps({'human_disposition':binding(hp),'human_retained_link_ids':['L']}))
        bound.update(binding(sp));evidence=deepcopy(evidence);evidence['approval']['retained_write_scope']=dict(bound)
        return original(run,plan=plan,evidence=evidence)
    monkeypatch.setattr(store,'review_cvp_group',first)
    plan,context=task(store)
    store.apply_plan(plan,transaction_context=context,metadata={'reason':'isolated human committed scope',
        'actor':'pytest','approval':store.get_cvp_group(context.run_id)['reviewed']['evidence']['approval']})
    monkeypatch.setattr(store,'review_cvp_group',original)
    prior=store.get_cvp_group(context.run_id)
    payload={**plan.operations[0].payload,'title':'another lawful action'}
    package={'schema_version':'wang_shared_knowledge_v1.3','package_id':'next','source_documents':[payload]}
    next_plan=store.plan_package(package,transactional=True)
    action=store.prepare_cvp_action(context.run_id,action_id='next',kind='source_claim_revision',
        authorization={'isolated_test_only':True},materials={'fixture':'scoped exact new action'})
    evidence=deepcopy(prior['reviewed']['evidence']);evidence.update(package={'payload':package},
        action=action,approval={'new_action_only':True})
    key=('source_documents',plan.operations[0].object_id);row=store.get_record_state(*key)
    pin=(row['revision'],row['content_sha256'],False)
    evidence['read_pins']=[{'collection':key[0],'object_id':key[1],'pin':list(pin)}]
    digest=store.review_cvp_group(context.run_id,plan=next_plan,evidence=evidence)
    apply(store,next_plan,TransactionValidation(context.run_id,digest,{key:pin}))
    return context.run_id,bound,plan.change_set_id,next_plan,evidence


def test_actual_later_review_preserves_original_sql_human_protection(postgres_store,tmp_path,monkeypatch):
    s=postgres_store;run,b,_,plan,e=prepare_case(s,tmp_path,monkeypatch)
    value=s.get_cvp_group(run)
    assert 'retained_write_scope' not in value['reviewed']['evidence']['approval']
    assert s.get_cvp_retained_scope_bindings(run)==[b]
    assert human.protected_link_ids(value,s)=={'L'}
    human.guard_operations(value,[SimpleNamespace(collection='viewpoint_claim_links',object_id='OTHER')],store=s)
    with pytest.raises(ValueError,match='NO-CHANGE'):
        human.guard_operations(value,[SimpleNamespace(collection='viewpoint_claim_links',object_id='L')],store=s)
    # The same protection is enforced before another SQL review can be saved.
    forbidden=replace(plan,operations=(replace(plan.operations[0],collection='viewpoint_claim_links',object_id='L'),))
    before=deepcopy(value)
    with pytest.raises(ValueError,match='NO-CHANGE'):s.review_cvp_group(run,plan=forbidden,evidence=e)
    assert s.get_cvp_group(run)==before


@pytest.mark.parametrize('fault',['status','fingerprint','source','run','review-sha','approval','group-membership','archive-id'])
def test_archived_human_approval_never_survives_foreign_or_broken_commit(postgres_store,tmp_path,monkeypatch,fault):
    s=postgres_store;run,b,cid,_,_=prepare_case(s,tmp_path,monkeypatch)
    with s.connect() as c:
        if fault=='status':c.execute("UPDATE wang_knowledge.change_sets SET status='rejected' WHERE change_set_id=%s",(cid,))
        elif fault in {'fingerprint','source'}:
            col='fingerprint_sha256' if fault=='fingerprint' else 'source_sha256'
            c.execute('UPDATE wang_knowledge.change_sets SET '+col+'=%s WHERE change_set_id=%s',('f'*64,cid))
        elif fault in {'run','review-sha','approval'}:
            path={'run':'{run_id}','review-sha':'{reviewed_sha256}','approval':'{approval,retained_write_scope}'}[fault]
            c.execute("UPDATE wang_knowledge.change_sets SET metadata=jsonb_set(metadata,%s,'\"foreign\"') WHERE change_set_id=%s",(path,cid))
        elif fault=='group-membership':c.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,'{change_set_ids}','[]') WHERE run_id=%s",(run,))
        elif fault=='archive-id':c.execute("UPDATE wang_knowledge.pipeline_runs SET run_id='foreign' WHERE metadata->>'kind'='cvp_review_archive' AND metadata->>'parent_run_id'=%s",(run,))
    with pytest.raises(ChangeSetConflict):s.get_cvp_retained_scope_bindings(run)


def test_archive_scope_selection_is_exact_group_not_a_campaign_waiver(postgres_store,tmp_path,monkeypatch):
    s=postgres_store;run,b,_,_,_=prepare_case(s,tmp_path,monkeypatch)
    other,_=task(s)
    # The first group alone owns this interpretation; unrelated groups do not inherit it.
    assert s.get_cvp_retained_scope_bindings('missing-group')==[]


@pytest.mark.skipif(os.environ.get('WKP_G107_COMMITTED_HUMAN_SCOPE')!='1',reason='explicit actual committed SQL scope readonly')
def test_actual_committed_human_scope_survives_two_other_reviewed_actions():
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.cvp_sql_flow import check_binding
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            c=super().connect();c.execute('SET TRANSACTION READ ONLY');return c
    s=ReadOnly();run='CVPG-dc7a68b9483efcd5e134b8162cf1753ad8943916b84379ea19863c57488ed446'
    before=s.get_cvp_group(run)
    assert before['checkpoint']=='audited' and before['status']=='succeeded'
    assert 'retained_write_scope' not in before['reviewed']['evidence']['approval']
    scopes=s.get_cvp_retained_scope_bindings(run);assert len(scopes)==1
    check_binding(scopes[0]);scope=read_artifact(scopes[0]['path']);check_binding(scope['human_disposition'])
    check_binding(scope['retained_boundary']);check_binding(scope['original_final_review'])
    direction=read_artifact(scope['human_disposition']['path'])
    assert human.protected_link_ids(before,s)=={'VCL-6d1c026e928f08a2469c'}
    for i in direction['items']:
        state=s.get_record_state('viewpoint_claim_links',i['link_id'])
        assert state['revision']==i['storage_revision'] and state['content_sha256']==i['content_sha256']
        assert state['payload']['link_type']==i['link_type']
    for p in scope['human_retained_claim_pins']:
        state=s.get_record_state(p['collection'],p['object_id'])
        assert (state['revision'],state['content_sha256'],state['retired'])==tuple(p['pin'])
    with pytest.raises(ValueError,match='NO-CHANGE'):
        human.guard_operations(before,[SimpleNamespace(collection='viewpoint_claim_links',object_id=direction['items'][0]['link_id'])],store=s)
    assert s.get_cvp_group(run)==before
    print({'actual_committed_scope_authenticated':True,'archived_protection':True,'models':0,'writes':0})
