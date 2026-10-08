"""SQL checkpoint, isolation and retired-entry contracts; no providers."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from backend.api.canonical_repository.postgres_store import ChangeSetConflict,canonical_json,record_content_sha
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.tests.test_cvp_sql_transaction_postgres import task,apply
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def test_latest_sql_group_does_not_embed_historical_action_bodies(postgres_store):
    store=postgres_store;plan,context=task(store);apply(store,plan,context)
    prior=deepcopy(store.get_cvp_group(context.run_id))
    payload={**plan.operations[0].payload,'title':'a bounded later revision'}
    package={'schema_version':'wang_shared_knowledge_v1.3','package_id':'later','source_documents':[payload]}
    next_plan=store.plan_package(package,transactional=True)
    action=store.prepare_cvp_action(context.run_id,action_id='later',kind='source_claim_revision',
        authorization={'fixture':True},materials={'fixture':'scoped current materials'})
    evidence=deepcopy(prior['reviewed']['evidence']);evidence['package']={'payload':package}
    evidence['action']=action
    key=('source_documents',plan.operations[0].object_id)
    row=store.get_record_state(*key);pin=(row['revision'],row['content_sha256'],False)
    evidence['read_pins']=[{'collection':key[0],'object_id':key[1],'pin':list(pin)}]
    digest=store.review_cvp_group(context.run_id,plan=next_plan,evidence=evidence)
    apply(store,next_plan,TransactionValidation(context.run_id,digest,{key:pin}))
    saved=store.get_cvp_group(context.run_id)
    assert 'prior_actions' not in saved and 'actions' not in saved
    assert saved['reviewed']['plan']['change_set_id']==next_plan.change_set_id
    # A historical lost reply uses one indexed review row, never the whole chain.
    assert apply(store,plan,context)['status']=='already_applied'
    assert store.get_cvp_action(context.run_id,'later')==action


def test_raw_owner_direction_cannot_expand_scope_before_model():
    from backend.tests.test_owner_approved_link_correction import fixture
    from backend.pipeline.cvp_sql_actions import validate_owner_link_direction
    from backend.api.canonical_repository.postgres_store import sha256_json
    *_,packet=fixture();packet['adjudication_packet_sha256']='a'*64
    direction={'schema_version':'cvp_sql_owner_link_direction.v2','run_id':'fixture-run','link_id':'L',
        'adjudication_packet_sha256':'a'*64,'before_link_sha256':sha256_json(packet['before_link']),
        'after_link_sha256':sha256_json(packet['after_link']),'authorization_quote':'fixture-only exact direction',
        'human_content_approval':False,'automatic_future_repairs_authorized':False}
    validate_owner_link_direction(direction,packet,'fixture-run')
    for field in ('run_id','link_id','before_link_sha256','after_link_sha256','adjudication_packet_sha256'):
        with pytest.raises(ValueError):validate_owner_link_direction({**direction,field:'wrong'},packet,'fixture-run')


def test_component_disposition_merge_preserves_untouched_claims():
    from backend.pipeline.cvp_sql_actions import merge_dispositions
    old={'claims':[{'claim_id':'A','components':[{'disposition':'deferred'}]},
                   {'claim_id':'B','components':[{'disposition':'supports_existing'}]}]}
    new={'claims':[{'claim_id':'A','components':[{'disposition':'new_viewpoint'}]}]}
    merged=merge_dispositions(old,new)
    assert [r['claim_id'] for r in merged['claims']]==['A','B']
    assert merged['claims'][1]==old['claims'][1]
    assert len(merged['source_disposition_sha256s'])==2


def test_mechanical_audit_reciprocity_count_is_not_a_pass():
    from backend.pipeline.cvp_sql_flow import validate_transaction_audit
    audit={'meta':{'registry_read_scope':{'kind':'sql_transaction_group','claim_ids':['A'],'viewpoint_ids':[],
        'complete_target_members':True}},'layers':{'1':{'counts':{'pass':1}},'2':{'claim_evidence_reciprocity':{'error_count':1}}}}
    with pytest.raises(ValueError,match='reciprocity'):validate_transaction_audit(audit,['A'],[])


@pytest.mark.parametrize('entry',['campaign','batch','source','owner-link','deferred','resume','prepared'])
def test_old_runtime_rejected_before_any_data_or_model_effect(entry):
    from backend.pipeline.cvp_retired_runtime import legacy_runtime_is_retired
    if not legacy_runtime_is_retired():pytest.skip('specific owner #357 retirement boundary')
    from backend.pipeline import viewpoint_cvp_campaign_runner as campaign,viewpoint_batch_resolution_runner as batch
    from backend.pipeline import source_reconciliation_runner as source,owner_approved_link_correction as link
    from backend.pipeline import deferred_claim_cvp_continuation as deferred,viewpoint_cvp_recovery_executor as recovery
    functions={'campaign':lambda:campaign.execute(None),'batch':lambda:batch.execute(None),
        'source':lambda:source.main(),'owner-link':lambda:link.execute(None,None,None),
        'deferred':lambda:deferred.run(None,None,None),'resume':lambda:recovery.resume_after_repair(None,None,None,None),
        'prepared':lambda:recovery.start_prepared_campaign(None,None,None,None,None,None,None)}
    with pytest.raises(RuntimeError,match='runtime is retired'):functions[entry]()


@pytest.mark.parametrize('module,function',[["viewpoint_authorized_source_repair_357","main"],["viewpoint_audit_link_repair","run"],["viewpoint_applied_link_repair_continuation","run"],["viewpoint_g037_contextual_continuation","run"],["viewpoint_g037_contextual_conflict","run"],["viewpoint_g041_source_epoch_continuation","run"],["viewpoint_g041_transcript_repair","run"],["viewpoint_g041_qualified_transcript_repair","run"],["viewpoint_g052_new_cvp_repair","complete_saved_audit"],["viewpoint_g052_new_cvp_repair","run"],["viewpoint_g034_theme_support_repair","run"],["viewpoint_g034_prayer_link_review","run"],["viewpoint_g033_relation_review","run"],["viewpoint_g035_temporary_audit","continue_g035"],["viewpoint_s201129_source_repair","main"],["viewpoint_s201129_source_continuation","run"],["viewpoint_s201129_link_support_continuation","run"],["viewpoint_s201129_link_support_review","run"],["viewpoint_s201129_link_support_apply","run"],["viewpoint_357_completion_link_repair","main"],["viewpoint_357_claim_qualifier_repair","run_main"],["viewpoint_357_scope_revision","run"],["viewpoint_357_g010_link_revision","run"],["viewpoint_357_cross_partition_support","main"],["deferred_claim_repair_runner","main"],["viewpoint_cvp_preflight_runner","main"],["viewpoint_cvp_recovery_supervisor","watch"],["viewpoint_cvp_recovery_executor","execute"]])
def test_old_special_case_and_watch_entries_cannot_bypass_retirement(module,function):
    import importlib
    import inspect
    target=getattr(importlib.import_module('backend.pipeline.'+module),function)
    # Even unusable input must encounter retirement first, before paths,
    # credentials, locks, providers, snapshots or database effects.
    kwargs={name:None for name,param in inspect.signature(target).parameters.items()
            if param.default is inspect.Parameter.empty}
    with pytest.raises(RuntimeError,match='runtime is retired'):
        target(**kwargs)
