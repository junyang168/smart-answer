"""Exact owner-directed logical withdrawal of one erroneous Evidence relation.

No model is needed to execute the human decision. Payloads and pending source /
CVP obligations remain intact; all writes use the common SQL transaction.
"""
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.source_reconciliation_runner import read, body, write
from backend.pipeline.viewpoint_resolution_runtime import read_artifact

SCHEMA = 'wang_claim_relation_retirement_v1'


def validate_material(material, direction):
    if direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k!='artifact_sha256'}):
        raise ValueError('relation retirement direction seal drift')
    if (direction.get('schema_version') != SCHEMA or direction.get('authorized_by') != 'owner'
            or direction.get('disposition') != 'logical_retirement_only'
            or not direction.get('authorization_quote') or direction.get('new_model_calls') != 0
            or direction.get('material_sha256') != sha256_json(material)):
        raise ValueError('relation retirement lacks exact human scope')
    relation = material['before_relation']
    if (relation['relation_id'] != direction['relation_id']
            or material['run_id'] != direction['run_id']
            or material['campaign_sha256'] != direction['campaign_sha256']
            or relation.get('relation_type') != 'supports'):
        raise ValueError('relation retirement scope mismatch')
    index={(r['collection'],r['object_id']):r for r in material['records']}
    if len(index)!=len(material['records']):raise ValueError('duplicate retirement context')
    if index['knowledge_relations',relation['relation_id']]['payload'] != relation:
        raise ValueError('relation retirement preimage mismatch')
    for endpoint in (relation['from_id'],relation['to_id']):
        if ('evidence_steps',endpoint) not in index:raise ValueError('missing retirement endpoint')
        if index['evidence_steps',endpoint]['retired']:raise ValueError('retired withdrawal endpoint')
    if index['knowledge_relations',relation['relation_id']]['retired']:
        raise ValueError('retirement target already retired in initial material')
    for row in index.values():
        p=row['payload']
        if not row['retired'] and (p.get('review_status') in {'human_hold','rejected','needs_review'} or p.get('effective_state')=='held'):
            raise ValueError('retirement cannot waive retirement/hold')
    from backend.pipeline.cvp_sql_flow import check_binding
    for source in material['sources']:check_binding(source)


def prepare_material(store, run_id, relation_id):
    from backend.pipeline.cvp_sql_flow import pins, pin_rows, check_binding
    group=store.get_cvp_group(run_id)
    row=store.get_record_state('knowledge_relations',relation_id)
    if not group or not row or row['retired']:raise ValueError('missing current retirement target/group')
    pending=group.get('reviewed',{}).get('evidence',{}).get('pending_cvp_management_record_keys',[])
    if 'knowledge_relations/'+relation_id not in pending:
        raise ValueError('relation is outside original pending management scope')
    records=store.read_affected_context([('knowledge_relations',relation_id)])
    sources=[]
    for (col,oid),r in sorted(records.items()):
        if col=='source_documents' and not r['retired']:
            p=r['payload'];s={'path':p['source_path'],'file_sha256':p['source_file_sha256']}
            check_binding(s);sources.append(s)
    if not sources:raise ValueError('relation retirement lacks physical sources')
    return {'run_id':run_id,'campaign_sha256':group['campaign_sha256'],
            'before_relation':row['payload'],'read_pins':pin_rows(pins(records)),
            'records':[{'collection':c,'object_id':i,**{k:r[k] for k in ('payload','revision','content_sha256','retired')}}
                       for (c,i),r in sorted(records.items())],
            'sources':sources,'pending_cvp_claim_ids':group.get('reviewed',{}).get('evidence',{}).get('pending_cvp_claim_ids',group['claim_ids']),
            'pending_cvp_management_record_keys':pending,
            'viewpoint_ids':group.get('reviewed',{}).get('evidence',{}).get('viewpoint_ids',[]),
            'disposition':group.get('reviewed',{}).get('evidence',{}).get('disposition')}


def package_for(material, authorization, root):
    from backend.pipeline.cvp_sql_flow import binding
    return {'package_id':'CLAIM-RELATION-RETIRE-'+sha256_json(material)[:24],
            'source_repair_application':{'schema_version':SCHEMA,'authorization':authorization,
                                         'materials':binding(root/'materials.json')}}


def validate_package(package):
    from backend.pipeline.cvp_sql_flow import check_binding
    app=package['source_repair_application']
    check_binding(app['authorization']);check_binding(app['materials'])
    material=body(read(Path(app['materials']['path'])))
    validate_material(material,read_artifact(app['authorization']['path']))
    if package!=package_for(material,app['authorization'],Path(app['materials']['path']).parent):
        raise ValueError('extra relation retirement package fields')
    return (('knowledge_relations',material['before_relation']['relation_id']),)


def execute(store, run_id, *, authorization, root, ports):
    from backend.pipeline.cvp_sql_flow import check_binding, decoded_pins, commit_package, verified_group_backup
    from backend.pipeline.cvp_sql_actions import resume_bound_action
    check_binding(authorization);direction=read_artifact(authorization['path'])
    if direction['run_id']!=run_id:raise ValueError('foreign retirement run')
    aid='CVPA-'+sha256_json({'kind':SCHEMA,'direction':authorization})
    root=Path(root)/aid
    store.bind_cvp_execution_root(run_id,identity=aid,root=root)
    old=store.get_cvp_action(run_id,aid)
    if old and old['authorization']!=authorization:raise ValueError('retirement authority drift')
    resumed=resume_bound_action(store,run_id,aid)
    if resumed:return resumed
    material=old['materials'] if old else prepare_material(store,run_id,direction['relation_id'])
    validate_material(material,direction)
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,decoded_pins(material['read_pins']))
    action=store.prepare_cvp_action(run_id,action_id=aid,kind='relation_retirement',authorization=authorization,materials=material)
    write(root/'materials.json',material)
    package=package_for(material,authorization,root);validate_package(package)
    plan=store.plan_package(package,source_kind=SCHEMA,transactional=True)
    if len(plan.operations)!=1 or plan.operations[0].operation!='retire':
        raise ValueError('relation retirement must contain exactly one withdrawal')
    result=commit_package(store,run_id,package=package,approval={'human_exact_disposition':authorization},
        authorization=authorization,backup=verified_group_backup(root,package,ports),
        sources=material['sources'],read_pins=decoded_pins(material['read_pins']),
        actor='owner',reason='exact owner logical retirement of erroneous Evidence support relation',
        source_kind=SCHEMA,action=action,
        viewpoint_ids=material.get('viewpoint_ids',[]),disposition=material.get('disposition'),
        pending_cvp_claim_ids=material['pending_cvp_claim_ids'],
        pending_cvp_management_record_keys=material['pending_cvp_management_record_keys'])
    return {'group_complete':False,**result}
