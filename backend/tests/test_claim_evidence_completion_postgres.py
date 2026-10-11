"""Isolated SQL joint Evidence update/fragment create/edge retirement atomicity.

Content authority is tested separately with exact saved inputs. This synthetic
fixture exercises the actual common package planner and RETURNING/history path.
"""
from copy import deepcopy
import uuid
import pytest
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha


@pytest.mark.parametrize('late_failure',[False,True])
def test_joint_source_write_and_relation_retirement_are_one_sql_transaction(postgres_store,monkeypatch,late_failure):
    from backend.pipeline import cvp_sql_actions
    s=postgres_store
    evidence={'evidence_step_id':'E-synthetic','statement':'professor text','revision':1,'review_status':'candidate',
              'support_eligibility':'withheld_missing_anchor','anchor_quality':'missing'}
    relation={'relation_id':'R-synthetic','from_id':'E-synthetic','to_id':'E-synthetic-target',
              'relation_type':'supports','revision':1,'review_status':'candidate'}
    target={**evidence,'evidence_step_id':'E-synthetic-target'}
    for col,oid,p in [('evidence_steps','E-synthetic',evidence),('evidence_steps','E-synthetic-target',target),
                      ('knowledge_relations','R-synthetic',relation)]:
        with s.connect() as conn:
            conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES (%s,%s,1,'candidate','internal',%s,%s::jsonb)",
                         (col,oid,record_content_sha(p),canonical_json(p)))
    with s.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,review_status,revision,payload) VALUES ('knowledge_relations','R-synthetic','E-synthetic','E-synthetic-target','supports','candidate',1,%s::jsonb)",
                     (canonical_json(relation),))
    # The real package planner must obtain retirements from its content validator,
    # not from an operator's unbound retiring_keys argument.
    monkeypatch.setattr(cvp_sql_actions,'validate_source_package',lambda package:(('knowledge_relations','R-synthetic'),))
    changed={**evidence,'revision':2,'anchor_quality':'verified_candidate','support_eligibility':'eligible_with_label'}
    package={'package_id':'joint-synthetic','evidence_steps':[changed],
             'source_repair_application':{'schema_version':'wang_source_sql_reconciliation_v2'}}
    with pytest.raises(ValueError,match='exceeds'):
        s.plan_package(package,source_kind='joint-test',transactional=True,retiring_keys=[('evidence_steps','E-synthetic')])
    plan=s.plan_package(package,source_kind='joint-test',transactional=True)
    assert {(o.collection,o.object_id,o.operation) for o in plan.operations}=={
        ('evidence_steps','E-synthetic','update'),('knowledge_relations','R-synthetic','retire')}
    before=deepcopy(s.get_record_state('evidence_steps','E-synthetic'))
    campaign=s.initialize_cvp_campaign({'accepted_grouping_sha256':uuid.uuid4().hex*2,
        'groups':[{'sequence':1,'group_key':'joint','claim_ids':['CL-fixture']}]})
    run=s.prepare_cvp_group(campaign_sha256=campaign,group_key='joint',sequence=1,
                            claim_ids=['CL-fixture'],materials={'fixture':'isolated joint SQL'})
    evidence_receipt={'package':{'payload':package},'approval':{'fixture':True},
                      'authorization':{'fixture':True},'backup':{'fixture':True},'sources':['synthetic-source'],'read_pins':[]}
    from backend.pipeline.cvp_sql_flow import pin_rows
    read_pins={(o.collection,o.object_id):(o.before_revision,o.before_sha256,False) for o in plan.operations}
    target_state=s.get_record_state('evidence_steps','E-synthetic-target')
    read_pins['evidence_steps','E-synthetic-target']=(target_state['revision'],target_state['content_sha256'],False)
    evidence_receipt['read_pins']=pin_rows(read_pins)
    reviewed=s.review_cvp_group(run,plan=plan,evidence=evidence_receipt)
    from backend.api.canonical_repository.transaction_validation import TransactionValidation
    context=TransactionValidation(run,reviewed,read_pins)
    metadata={'reason':'isolated complete Evidence transaction','actor':'pytest'}
    if late_failure:
        def fail(*args,**kwargs):raise ValueError('late joint validation failure')
        monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',fail)
        with pytest.raises(ValueError,match='late joint'):s.apply_plan(plan,transaction_context=context,metadata=metadata)
        assert s.get_record_state('evidence_steps','E-synthetic')==before
        assert not s.get_record_state('knowledge_relations','R-synthetic')['retired']
    else:
        s.apply_plan(plan,transaction_context=context,metadata=metadata)
        after=s.get_record_state('knowledge_relations','R-synthetic')
        assert after['retired'] and after['content_sha256']==record_content_sha(relation)
        assert s.get_record_state('evidence_steps','E-synthetic')['payload']['support_eligibility']=='eligible_with_label'
        assert len(s.get_change_history(plan.change_set_id)['operations'])==2
        s.apply_plan(plan,transaction_context=context,metadata=metadata)
        assert len(s.get_change_history(plan.change_set_id)['operations'])==2
