"""Shared-context Questions remain answerable by newly extracted Claims."""
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm,claim_management_intake as intake,claim_management_batch as batch
from backend.tests.test_claim_management_intake import claims
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres

@pytest.mark.parametrize('answer_ids',[[],['D']])
def test_creation_gets_link_only_authority_for_shared_question(claims,answer_ids):
 store,root=claims
 store.apply_plan(store.plan_package({'package_id':'question-frontier','questions':[{'question_id':'Q','text':'甲是假設嗎？','questioner':'professor','answer_state':'answered' if answer_ids else 'unanswered','answer_claim_ids':answer_ids,'source_fragment_ids':['FC']}]}))
 policy=root.parent/'bounded-policy.json';v=json.loads(Path('backend/pipeline/policies/wang_claim_management_bounded_unit_v1.json').read_text());v['models']['opus']['backend']='claude_subscription';v.pop('api_spend',None);policy.write_text(json.dumps(v))
 before=store.get_record_state('questions','Q');out=intake.prepare(['C'],root,instruction='Full same-source update',groups=[['C']],store=store,policy=policy)
 _,jobs=batch.load(out['manifest']['path'],root/'batch',store=store);request,_=next(iter(jobs.values()));p={x.key:set(x.fields) for x in request.permissions}
 assert p['questions/Q']=={'answer_claim_ids'}
 assert 'claims/D' not in p
 context=store.read_claim_context([('claims','C')]);states={cm.source.key(*k):v for k,v in context.items()};goal=cm.original_goal(request,states)
 assert not cm.unlinkable_questions(request,states)
 assert store.get_record_state('questions','Q')==before

@pytest.mark.parametrize('held',[False,True])
def test_unrelated_questions_stay_read_only_and_protected_questions_stop(claims,held):
 store,root=claims
 store.apply_plan(store.plan_package({'package_id':'question-scope-negative','questions':[{'question_id':'Q','text':'甲是假設嗎？','questioner':'professor','answer_state':'unanswered','answer_claim_ids':[],'source_fragment_ids':['FC' if held else 'FD']}]}))
 if held:store.record_review('questions','Q',decision='human_review_required',reviewer_kind='human',reviewer_id='owner')
 policy=root.parent/'bounded-policy.json';v=json.loads(Path('backend/pipeline/policies/wang_claim_management_bounded_unit_v1.json').read_text());v['models']['opus']['backend']='claude_subscription';v.pop('api_spend',None);policy.write_text(json.dumps(v))
 before=store.get_record_state('questions','Q')
 if held:
  with pytest.raises(ValueError,match='protected Question'):intake.prepare(['C'],root,instruction='Full update',groups=[['C']],store=store,policy=policy)
  assert not (root/'manifest.json').exists()
 else:
  out=intake.prepare(['C'],root,instruction='Full update',groups=[['C']],store=store,policy=policy);_,jobs=batch.load(out['manifest']['path'],root/'batch',store=store);request,_=next(iter(jobs.values()));assert all(p.key!='questions/Q' for p in request.permissions)
 assert store.get_record_state('questions','Q')==before


def test_sampling_write_authority_covers_each_explicit_group_member(claims):
    from backend.tests.test_claim_metadata_projection import seed_marker
    from backend.pipeline.claim_metadata_projection import compile_projection
    store,root=claims
    old=seed_marker(store,None)
    policy=root.parent/'bounded-policy.json'
    v=json.loads(Path('backend/pipeline/policies/wang_claim_management_bounded_unit_v1.json').read_text())
    v['models']['opus']['backend']='claude_subscription';v.pop('api_spend',None);policy.write_text(json.dumps(v))
    prepared=intake.prepare(['D','C'],root,instruction='Refresh each explicit member',groups=[['D','C']],store=store,policy=policy)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,_=next(iter(jobs.values()))
    assert any(p.key=='claims/C' and 'statement' in p.fields for p in request.permissions)
    records=store.read_claim_context([('claims','D'),('claims','C')]);before={cm.source.key(*k):v for k,v in records.items()}
    with store.connect() as conn,conn.cursor() as cursor:
        projection=compile_projection(cursor,request,before,cm.Policy.model_validate(v))
    assert 'claims/C' in projection['sampling_markers']
    assert store.get_record_state('claims','C')==old
