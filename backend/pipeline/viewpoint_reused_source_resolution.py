"""Consume an exact unchanged subset of saved GPT rejected findings, no new call."""
from pathlib import Path
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA='wang_exact_reused_gpt_source_resolution_v1'
PROJECTION_POLICY=Path(__file__).parent/'policies/wang_g035_rejected_finding_projection_v1.json'

def exact_item(old,current,projection=None):
    if old==current:return True
    if not old or projection is None:return False
    old_row,new_row=old['audit_finding'],current['audit_finding']
    if (sha256_json(old_row)!=projection['old_finding_sha256'] or
            sha256_json(new_row)!=projection['new_finding_sha256'] or
            set(old_row['claim_ids_in_question'])-set(new_row['claim_ids_in_question'])!={projection['removed_claim_id']}):
        return False
    return {k:v for k,v in old.items() if k!='audit_finding'}=={k:v for k,v in current.items() if k!='audit_finding'}

def verify(audit, resolution):
    from backend.pipeline.viewpoint_audit_owner_resolution import binding, verify_sha, audit_semantic_sha, GPT_POLICY
    from backend.pipeline.viewpoint_audit_source_adjudication import validate_findings
    verify_sha(resolution)
    if (resolution['schema_version']!=SCHEMA or resolution['audit_semantic_sha256']!=audit_semantic_sha(audit)
            or resolution.get('master_data_mutations')!=0 or resolution.get('new_model_calls')!=0):
        raise ValueError('reused resolution identity drift')
    bound={b['path']:b for b in resolution['bindings']}
    for b in bound.values():
        if binding(b['path'])!=b: raise ValueError('reused source/raw/proof drift')
    def read(key):
        path=resolution[key]
        if path not in bound: raise ValueError('reused resolution missing binding')
        return read_artifact(Path(path))
    old,raw,handoff,current=(read(k) for k in ('prior_packet_path','prior_raw_path','prior_handoff_path','current_packet_path'))
    for a in (old,raw,handoff,current):verify_sha(a)
    policy=read_artifact(GPT_POLICY)
    if (bound.get(str(GPT_POLICY.resolve()))!=binding(GPT_POLICY) or raw['model_id']!=policy['model']
            or raw['backend']!=policy['backend'] or raw['request_payload_sha256']!=sha256_json(old)
            or raw['response_sha256']!=sha256_json(raw['response'])
            or handoff['packet_sha256']!=old['artifact_sha256'] or handoff.get('maximum_model_calls')!=1):
        raise ValueError('saved GPT backend/input provenance drift')
    findings=validate_findings(raw['response'],old)
    actions=handoff['actions']
    if len(actions)!=len(findings) or {a['item_id'] for a in actions}!=set(findings) or any(a['gpt']!=findings[a['item_id']] or a['verdict']!=a['gpt']['verdict'] for a in actions):
        raise ValueError('saved full mixed handoff drift')
    index={i['item_id']:i for i in old['items']}
    projection=None
    if resolution.get('projection_policy_path'):
        projection=read('projection_policy_path')
        if projection!=read_artifact(PROJECTION_POLICY):raise ValueError('finding projection policy drift')
        repair=Path(projection['repair_root'])
        def repair_read(name):
            path=str((repair/name).resolve())
            if path not in bound:raise ValueError('missing exact applied projection proof')
            return read_artifact(Path(path))
        packet=repair_read('packet.json'); review=repair_read('review-report.json')
        receipt=repair_read('apply-receipt.json'); verified=repair_read('verified-result.json')
        if (len(packet['targets'])!=1 or packet['targets'][0]['before_link']['viewpoint_claim_link_id']!=projection['repaired_link_id']
                or packet['targets'][0]['claim']['claim_id']!=projection['removed_claim_id']
                or packet['targets'][0]['after_link']['link_type']!='supports' or review['approved'] is not True
                or receipt['change_set_id']!=projection['change_set_id'] or verified['change_set_id']!=projection['change_set_id']
                or verified['status']!='applied_readback_verified' or verified['claim_evidence_source_writes']!=0):
            raise ValueError('finding subset removal is not the exact reviewed applied repair')
        if any(not v['approved'] or v['problems'] for r in review['reviews'].values() for v in r['verdicts']):
            raise ValueError('projection repair lacked approval')
    if bound.get(old['audit_binding']['path'])!=old['audit_binding']:
        raise ValueError('saved original audit missing binding')
    if not current['items']:raise ValueError('empty reused semantic population')
    for item in current['items']:
        if not exact_item(index.get(item['item_id']),item,projection) or findings[item['item_id']]['verdict']!='audit_objection_rejected':
            raise ValueError('changed/upheld/unresolved item cannot reuse rejection')
        layer,identity,_=item['item_id'].split(':',2)
        key='claim_id' if layer=='3' else 'viewpoint_id'
        rows=[r for r in audit['layers'][layer]['results'] if r[key]==identity]
        if len(rows)!=1 or rows[0]!=item['audit_finding']:
            raise ValueError('current actual finding drift')
        for s in item['sources']:
            if bound.get(s['path'])!={'path':s['path'],'file_sha256':s['file_sha256']}:
                raise ValueError('missing own physical source')
    if current['audit_binding']['path'] not in bound or current['audit_binding']!=bound[current['audit_binding']['path']]:
        raise ValueError('current audit missing binding')
    from backend.pipeline.viewpoint_audit_source_adjudication import disputes
    expected={f'{layer}:{row[key]}:{cid}' for layer,key,row in disputes(audit)
              for cid in ([row[key]] if layer=='3' else row.get('claim_ids_in_question',[]))}
    if expected!={i['item_id'] for i in current['items']}:raise ValueError('reused population incomplete')
    return {layer:{row[key] for l,key,row in disputes(audit) if l==layer} for layer in ('3','4')}

def create(audit_path, prior_root, output, store):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    from backend.pipeline.viewpoint_audit_owner_resolution import binding, audit_semantic_sha, GPT_POLICY
    from backend.pipeline.viewpoint_g035_temporary_audit import validate
    audit=read_artifact(audit_path); packet=build_packet(audit_path,store)
    packet_path=output.parent/'reused-current-packet.json';write_immutable(packet_path,packet)
    paths={'prior_packet_path':prior_root/'packet.json','prior_raw_path':prior_root/'raw-gpt.json',
           'prior_handoff_path':prior_root/'gpt-final-handoff.json','current_packet_path':packet_path}
    old=read_artifact(paths['prior_packet_path'])
    sources={s['path']:{'path':s['path'],'file_sha256':s['file_sha256']} for i in packet['items'] for s in i['sources']}
    body={'schema_version':SCHEMA,'audit_semantic_sha256':audit_semantic_sha(audit),'master_data_mutations':0,
          'new_model_calls':0,'bindings':[binding(p) for p in [*paths.values(),audit_path,GPT_POLICY,Path(old['audit_binding']['path'])]]+list(sources.values())+validate(audit),
          **{k:str(p.resolve()) for k,p in paths.items()}}
    old_items={i['item_id']:i for i in old['items']}
    if any(old_items.get(i['item_id'])!=i for i in packet['items']):
        projection=read_artifact(PROJECTION_POLICY)
        if not all(exact_item(old_items.get(i['item_id']),i,projection) for i in packet['items']):
            raise ValueError('changed/upheld item outside exact finding projection')
        repair=Path(projection['repair_root'])
        body['projection_policy_path']=str(PROJECTION_POLICY.resolve())
        body['bindings'] += [binding(PROJECTION_POLICY)]+[binding(repair/n) for n in
            ('packet.json','raw-gpt.json','raw-opus.json','review-report.json','effective-package.json','dry-run-plan.json','backup.json','apply-receipt.json','verified-result.json')]
    resolution=body|{'artifact_sha256':sha256_json(body)};verify(audit,resolution)
    if build_packet(audit_path,store)!=packet:raise ValueError('current reused packet drift')
    write_immutable(output,resolution);return resolution
