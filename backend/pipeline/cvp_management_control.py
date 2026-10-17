"""Explicit human/independent-audit intake around the ordinary CVP2 action.

These operations never dispatch a model, approve an unknown candidate or alter
its original call budget. All physical authority is read before SQL admission.
"""
from __future__ import annotations

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository import cvp_management_store as persistence
from backend.api.canonical_repository.cvp_recall import seal
from backend.pipeline.cvp_sql_commit import check_binding
from backend.pipeline.viewpoint_resolution_runtime import read_artifact


def _bound(value):
    check_binding(value)
    return read_artifact(value['path'])


def run(store,manifest,*,execute=False):
    if manifest.get('schema_version')!=domain.VERSION+'_control':
        raise ValueError('explicit CVP2 control manifest required')
    operation=manifest.get('operation')
    if operation not in {'content_dispute','human_constraint','independent_audit','usage_admission'}:
        raise ValueError('unsupported CVP2 control operation')
    parent_id=manifest['parent_run_id'];parent=store.get_cvp_group(parent_id)
    if not parent or parent.get('kind')!='cvp_management_action':raise ValueError('original CVP2 task missing')
    if operation=='content_dispute':
        step_id=manifest['step_id'];step=store.get_cvp_group(step_id)
        if not step or step.get('parent_run_id')!=parent_id:raise ValueError('foreign CVP2 step')
        data=dict(manifest['inputs'])
        # The two judgment artifacts are the actual cached provider leaves,
        # not manually transcribed summaries of the disputed positions.
        data['candidate']=_bound(data['candidate'])
        for name in ('proposal_judgment','review_judgment'):
            data[name]={**data[name],'artifact':_bound(data[name]['artifact'])}
        result=domain.build_content_dispute(parent['request'],step['materials']['bundle'],**data,
            previous=step['materials']['previous'],prior_findings=step['materials']['prior_findings'])
        if execute:persistence.register_content_dispute(store,parent_id,step_id,dispute=result,inputs=data)
        return result
    if operation=='human_constraint':
        step_id=manifest['step_id'];step=store.get_cvp_group(step_id)
        if not step or step.get('parent_run_id')!=parent_id or not step.get('content_dispute'):
            raise ValueError('original source-grounded content dispute missing')
        decision=domain.HumanDecision.model_validate(manifest['decision']).model_dump(mode='json')
        direction=_bound(decision['authorization'])
        expected={'schema_version':domain.VERSION+'_human_direction','action_id':parent['action_id'],
            'dispute_sha256':step['content_dispute']['artifact_sha256'],
            'decision':{k:v for k,v in decision.items() if k!='authorization'}}
        if direction!=expected:raise ValueError('human direction does not bind this exact dispute and constraint')
        result=domain.validate_human_constraint(parent['request'],step['content_dispute'],decision,
            existing_constraints=[c for c in parent['human_constraints'] if c['group_key']==step['group_key']])
        if execute:persistence.register_human_constraint(store,parent_id,step_id,decision=decision)
        return result
    if operation=='independent_audit':
        audit=domain.QualityObservation.model_validate(manifest['observation']).model_dump(mode='json')
        if _bound(audit['report_binding'])!=audit['raw_report']:
            raise ValueError('independent finding differs from actual physical report')
        # The external audit admission binds its independently run reader,
        # cohort and sample. It grants no model calls or business writes here.
        admission=_bound(manifest['authorization'])
        if admission!={'schema_version':domain.VERSION+'_audit_direction','authorized_by':'owner',
                      'action_id':parent['action_id'],'observation':audit}:
            raise ValueError('independent audit intake lacks exact scope/reader authority')
        current=store.read_current_records([(p['collection'],p['object_id']) for p in audit['cohort_pins']])
        result=domain.register_quality_findings(audit,current)
        if execute:persistence.register_independent_audit(store,parent_id,observation=audit,current_records=current)
        return result
    if execute:raise ValueError('usage admission is read-only and never permits --execute')
    keys=manifest['target_keys']
    context=store.read_current_dependency_context([tuple(k.split('/',1)) for k in keys])
    viewpoint_ids=[oid for col,oid in context if col=='canonical_viewpoints']
    context.update(store.read_cvp_relations(viewpoint_ids=viewpoint_ids))
    current={key:row for key,row in context.items() if not row.get('retired') and not row.get('retired_at')}
    scope=_bound(manifest['scope_admission']) if manifest.get('scope_admission') else None
    if scope:
        expected={'schema_version':domain.VERSION+'_scope_direction','authorized_by':'owner',
            'scope':{k:v for k,v in scope.items() if k not in {'authorization','artifact_sha256'}}}
        if _bound(scope['authorization'])!=expected:raise ValueError('formal usage scope authority differs')
        for bound in scope['evidence_bindings']:check_binding(bound)
    dependencies=domain.usage_dependency_keys(keys,current)
    registration_keys={tuple(k.split('/',1)) for values in dependencies.values() for k in values}
    registrations=persistence.current_quality_findings(store,registration_keys)
    store.verify_current_read_pins({key:(row['revision'],row['content_sha256'],False) for key,row in current.items()})
    return domain.assess_usage_admission(keys,current,registrations,purpose=manifest['purpose'],scope_admission=scope)
