"""Preserve completed unchanged targets during an irrelevant role-rule rollout.

No reason matching, semantic adjudication or new model call. Both actual reports
remain immutable. A derived complete frame consumes exact current provider cache
and the immediately recorded SQL completion, never arbitrary favorable history.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
from backend.pipeline.viewpoint_audit_owner_resolution import binding,verify_sha
from backend.pipeline.viewpoint_semantic_contract import COMPONENT_ROLES,RELATION_ROLES,relation_rules,CONTRACT_PATH,COMPONENT_PATH,RELATION_PATH

KEY='unchanged_relation_profile_reuse'
SCHEMA='wang_unchanged_relation_profile_audit_reuse_v1'


def _entries(current,prior):
    from dotenv import dotenv_values
    from backend.pipeline.viewpoint_content_blocked_fallback import capture,KEY as FALLBACK
    if (current['meta'].get('semantic_contract_version')!=RELATION_ROLES
            or prior['meta'].get('semantic_contract_version')!=COMPONENT_ROLES
            or prior['meta'].get(KEY) or prior['meta'].get(FALLBACK)
            or current['meta'].get(FALLBACK)
            or current['meta'].get('model')!='gemini-3.8-flash'
            or prior['meta'].get('model')!='gemini-3.8-flash'):
        return []
    for report in (current,prior):
        if (report['meta'].get('semantic_contract_file_sha256')!=hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
                or report['meta'].get('component_role_contract_file_sha256')!=hashlib.sha256(COMPONENT_PATH.read_bytes()).hexdigest()):
            raise ValueError('unchanged role profile original contract physical drift')
    if current['meta'].get('relation_role_contract_file_sha256')!=hashlib.sha256(RELATION_PATH.read_bytes()).hexdigest():
        raise ValueError('current role disclosure physical drift')
    if current['meta']['registry_read_scope']['claim_ids']!=prior['meta']['registry_read_scope']['claim_ids']:
        raise ValueError('role profile reuse changed original group Claim population')
    pins={(p['collection'],p['object_id']):p['pin'] for p in prior['meta']['registry_read_scope']['read_pins']}
    old={r['viewpoint_id']:r for r in prior['layers']['4']['results']}
    settings=dotenv_values(Path(__file__).resolve().parents[2]/'.env')
    cache_root=Path(settings['DATA_BASE_DIR'])/'wang-knowledge-platform/staging/reports/library-audit/.judgements'
    result=[]
    for row in current['layers']['4']['results']:
        vid=row['viewpoint_id'];previous=old.get(vid)
        if not previous or previous.get('revision_id')!=row.get('revision_id'):continue
        scope={'kind':'sql_transaction_group','complete_target_members':True,
               'claim_ids':[],'viewpoint_ids':[vid]}
        prompt,schema,sources,actual,links=capture('4',vid,semantic_contract_version=COMPONENT_ROLES,
            sql_scope=scope,with_pins=True)
        if (not links or any(l.get('link_type') not in {'equivalent_full','equivalent_component','supports'}
                or l.get('review_status') not in {'approved','system_approved','human_approved'} for l in links)
                or not actual or any(pins.get((p['collection'],p['object_id']))!=p['pin'] for p in actual)):
            continue
        full,new_schema,new_sources=capture('4',vid,semantic_contract_version=RELATION_ROLES,sql_scope=scope)
        if (full.replace('\n\n'+relation_rules(),'')!=prompt or new_schema!=schema or new_sources!=sources):
            raise ValueError('role rollout changed actual semantic input beyond neutral role disclosure')
        new_cache=cache_root/(hashlib.sha256(f'gemini-3.8-flash\n{full}'.encode()).hexdigest()+'.json')
        if not new_cache.exists() or any(row.get(k)!=v for k,v in read_artifact(new_cache).items()
                                        if k!='provider_content_block'):
            raise ValueError('original new role report lacks its actual complete provider response')
        cache=cache_root/(hashlib.sha256(f'gemini-3.8-flash\n{prompt}'.encode()).hexdigest()+'.json')
        if not cache.exists():continue
        response=read_artifact(cache)
        if any(previous.get(k)!=v for k,v in response.items() if k!='provider_content_block'):continue
        if any(previous.get(k)!=row.get(k) for k in ('viewpoint_id','revision_id','core_proposition',
                'linked_claims','linked_connections','member_claims')):continue
        result.append({'viewpoint_id':vid,'version':COMPONENT_ROLES,'provider_cache':binding(cache),'original_provider_cache':binding(new_cache),
            'sources':sources,'read_pins':actual,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'original_completed_result':previous})
    return result


def _derive(original,proof,manifest_binding):
    value=deepcopy(original)
    rows={e['viewpoint_id']:e['original_completed_result'] for e in proof['entries']}
    layer=value['layers']['4']
    layer['results']=[deepcopy(rows.get(r['viewpoint_id'],r)) for r in layer['results']]
    for verdict in ('same','disputed','blocked','model_error','no_source_text'):
        key={'model_error':'model_errors'}.get(verdict,verdict)
        if key in layer:layer[key]=sum(r.get('verdict')==verdict for r in layer['results'])
    value['meta'][KEY]=manifest_binding
    return value


def prepare(store,run_id,audit_path,root):
    original=read_artifact(audit_path)
    if original['meta'].get('semantic_contract_version')!=RELATION_ROLES:return audit_path
    saved=store.get_cvp_group(run_id)
    old_binding=saved.get('completion',{}).get('audit')
    if not old_binding:return audit_path
    if binding(old_binding['path'])!=old_binding:raise ValueError('recorded old audit completion physical drift')
    prior=read_artifact(old_binding['path'])
    entries=_entries(original,prior)
    if not entries:return audit_path
    body={'schema_version':SCHEMA,'run_id':run_id,'campaign_sha256':saved['campaign_sha256'],
        'original_report':binding(audit_path),'prior_completion_audit':old_binding,'entries':entries,
        'new_model_calls':0,'master_data_mutations':0,'original_provider_reports_preserved':True}
    proof={**body,'artifact_sha256':sha256_json(body)}
    root=Path(root);manifest=root/'manifest.json';write_immutable(manifest,proof)
    assessment=_derive(original,proof,binding(manifest))
    validate(assessment)
    output=root/'audit.json';write_immutable(output,assessment)
    return output


def _proof(audit):
    pin=audit['meta'][KEY]
    if binding(pin['path'])!=pin:raise ValueError('retained role profile manifest physical drift')
    proof=read_artifact(pin['path']);verify_sha(proof)
    if (proof.get('schema_version')!=SCHEMA or proof.get('new_model_calls')!=0
            or proof.get('master_data_mutations')!=0 or proof.get('original_provider_reports_preserved') is not True
            or not proof.get('entries')):raise ValueError('invalid unchanged role profile accounting')
    for p in (proof['original_report'],proof['prior_completion_audit']):
        if binding(p['path'])!=p:raise ValueError('actual audit report physical drift')
    return proof


def profile(audit,identity,default):
    proof=_proof(audit)
    found=[e for e in proof['entries'] if e['viewpoint_id']==identity]
    if len(found)>1:raise ValueError('duplicate unchanged role target')
    return found[0]['version'] if found else default


def validate(audit):
    proof=_proof(audit)
    original=read_artifact(proof['original_report']['path'])
    prior=read_artifact(proof['prior_completion_audit']['path'])
    entries=_entries(original,prior)
    if entries!=proof['entries'] or audit!=_derive(original,proof,audit['meta'][KEY]):
        raise ValueError('changed target/pins/source/provider response or selectively altered role accounting')
    return [audit['meta'][KEY],proof['original_report'],proof['prior_completion_audit'],
        *[p for e in entries for p in [e['provider_cache'],e['original_provider_cache'],*e['sources']]]]


def validate_sql_parent(audit,saved,audit_binding,*,proof=None):
    """Original recorded completion, or exact already-applied new completion only."""
    if proof is None:proof=_proof(audit)
    recorded=saved.get('completion',{}).get('audit')
    if (proof['run_id']!=saved['run_id'] or proof['campaign_sha256']!=saved['campaign_sha256']
            or recorded not in (proof['prior_completion_audit'],audit_binding)):
        raise ValueError('unchanged role profile does not belong to exact recorded SQL completion')
