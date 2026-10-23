"""Governed logical Claim withdrawal; no source deletion or model relabelling.

Exact human dispositions use the existing common SQL action/transaction. An
approved historical candidate is provenance, not a live membership assertion;
only its exact unchanged candidate_claim_ids reference may remain historical.
"""
import json
from pathlib import Path
from backend.api.canonical_repository.postgres_store import KNOWLEDGE_COLLECTIONS, canonical_json, record_content_sha, sha256_json
from backend.pipeline.cvp_sql_commit import binding, check_binding, pins, pin_rows, decoded_pins
from backend.pipeline.source_reconciliation_runner import read, body, write
from backend.pipeline.viewpoint_resolution_runtime import read_artifact

SCHEMA = 'wang_claim_sql_retirement_v1'
POLICY = Path(__file__).parent/'policies/wang_claim_retirement_v1.json'
HELD = {'human_hold', 'human_review_required', 'withheld_unreviewed', 'rejected', 'needs_review'}


def retirement_keys(material):
    return tuple(sorted([('claims', material['claim_id'])] + [('evidence_steps',i) for i in material['evidence_ids']] +
                        [('viewpoint_claim_links', i) for i in material['link_ids']]))


def validate_material(material, direction):
    policy=read_artifact(POLICY)
    if (policy.get('schema_version') != SCHEMA or policy.get('approval_mode') != 'human_exact_disposition'
            or policy.get('physical_delete') is not False or policy.get('source_mutations') != []):
        raise ValueError('Claim retirement policy does not preserve history/source')
    if (direction.get('schema_version') != SCHEMA or direction.get('authorized_by') != 'owner'
            or direction.get('human_exact_disposition') is not True
            or direction.get('disposition') != 'retire_claim_and_all_active_links'
            or direction.get('automatic_future_retirements_authorized') is not False
            or not direction.get('authorization_quote') or not direction.get('reason')):
        raise ValueError('Claim retirement requires exact human disposition, not automatic permission')
    for k in ('run_id', 'campaign_sha256', 'claim_id', 'replacement_claim_id', 'link_ids', 'evidence_ids', 'before_pins'):
        if direction.get(k) != material[k]: raise ValueError('Claim retirement scope/pin drift: '+k)
    records={(r['collection'], r['object_id']):r for r in material['records']}
    cid=material['claim_id']; replacement=material['replacement_claim_id']
    if cid == replacement or not material['link_ids'] or len(set(material['link_ids'])) != len(material['link_ids']):
        raise ValueError('retirement replacement/complete link set invalid')
    for row in records.values():
        p=row['payload']
        if p.get('review_status') in HELD or p.get('effective_state') == 'held':
            raise ValueError('Claim retirement cannot override hold')
    for key in [('claims',cid),('claims',replacement)]:
        row=records.get(key)
        if not row or row['retired'] or row['payload'].get('review_status') not in {'approved','ai_consensus_reviewed','system_approved','human_approved'}:
            raise ValueError('retirement requires active approved Claim and replacement')
    def own_ids(p):
        return {o.get('source_id') or o.get('transcript_id') for o in p.get('occurrences',[])}
    if not (own_ids(records['claims',cid]['payload']) & own_ids(records['claims',replacement]['payload']) - {None}):
        raise ValueError('replacement must preserve teaching in the same own source')
    actual=sorted(i for (c,i),r in records.items() if c=='viewpoint_claim_links' and not r['retired']
                  and r['payload']['claim_id']==cid and r['payload'].get('effective_state')=='active')
    if actual != material['link_ids']: raise ValueError('retirement omitted/added active Claim links')
    evidence=sorted(i for (c,i),r in records.items() if c=='evidence_steps' and not r['retired']
                    and cid in r['payload'].get('produced_claim_ids',[]))
    if evidence != material['evidence_ids'] or not evidence:
        raise ValueError('retirement omitted/added owned Evidence')
    for eid in evidence:
        if records['evidence_steps',eid]['payload'].get('produced_claim_ids') != [cid]:
            raise ValueError('shared Evidence requires independent ownership disposition')
    for lid in actual:
        old=records['viewpoint_claim_links',lid]['payload']; vid=old['viewpoint_id']
        if old.get('supporting_relation_ids'): raise ValueError('relation-bound withdrawal needs separate disposition')
        kept=[r['payload'] for (c,i),r in records.items() if c=='viewpoint_claim_links' and i not in actual
              and not r['retired'] and r['payload']['viewpoint_id']==vid
              and r['payload'].get('effective_state')=='active'
              and r['payload']['link_type'] in {'equivalent_full','equivalent_component'}]
        if not kept: raise ValueError('retirement would remove last equivalent member')
    for row in material['historical_candidates']:
        p=row['payload']; candidate=p['identity_candidate_id']
        decisions=[r['payload'] for (c,_),r in records.items() if c=='viewpoint_identity_decisions' and not r['retired']
                   and r['payload'].get('identity_candidate_id')==candidate]
        if (row != records.get(('viewpoint_identity_candidates',candidate)) or row['retired']
                or p.get('blocker_codes') or p.get('proposed_action') not in {'create_new','match_existing'}
                or cid not in p.get('candidate_claim_ids',[]) or len(decisions)!=1
                or decisions[0].get('approval_basis')!='dual_model_consensus'
                or decisions[0].get('review_status') not in {'approved','system_approved','human_approved'}
                or not decisions[0].get('review_artifact_sha256')):
            raise ValueError('candidate is not exact committed approved history')
    for key,pin in decoded_pins(material['read_pins']).items():
        row=records.get(key)
        if not row or (row['revision'],row['content_sha256'],row['retired']) != pin or record_content_sha(row['payload']) != pin[1]:
            raise ValueError('retirement material record/pin mismatch')
    targets=set(retirement_keys(material))|{('claims',replacement)}
    wanted=pin_rows({k:v for k,v in decoded_pins(material['read_pins']).items() if k in targets})
    if sorted(material['before_pins'],key=lambda p:(p['collection'],p['object_id'])) != wanted:
        raise ValueError('human disposition must pin entire retirement/replacement set')


def prepare_material(store, run_id, direction):
    group=store.get_cvp_group(run_id); cid=direction['claim_id']; replacement=direction['replacement_claim_id']
    if not group or group['campaign_sha256'] != direction['campaign_sha256']:
        raise ValueError('Claim retirement campaign/run mismatch')
    # Frozen original members require a separate explicit denominator/disposition
    # contract, never silent shrinking. This initial feature retires dependencies.
    if any(cid in g['claim_ids'] for g in store.cvp_campaign_progress(group['campaign_sha256'])):
        raise ValueError('original campaign Claim retirement needs explicit frozen-member disposition')
    links=store.claim_links([cid]); vids=sorted({l['viewpoint_id'] for l in links if l.get('effective_state')=='active'})
    records=store.read_cvp_context([cid,replacement],viewpoint_ids=vids)
    with store.connect() as conn, conn.cursor() as cursor:
        consumers=store._reference_consumers(cursor,[cid],sorted(KNOWLEDGE_COLLECTIONS))
    historical=[]
    allowed={'claims','evidence_steps','viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'}
    for col,oid,payload in consumers:
        if col not in allowed or (col=='claims' and oid!=cid):
            raise ValueError('unhandled live Claim consumer: '+col+'/'+oid)
        state=store.get_record_state(col,oid); records[col,oid]=state
        if col=='viewpoint_identity_candidates': historical.append({'collection':col,'object_id':oid,**state})
    evidence_ids=sorted(i for (col,i),r in records.items() if col=='evidence_steps' and not r['retired']
                        and cid in r['payload'].get('produced_claim_ids',[]))
    with store.connect() as conn,conn.cursor() as cursor:
        ev_consumers=store._reference_consumers(cursor,evidence_ids,sorted(KNOWLEDGE_COLLECTIONS))
    for col,oid,payload in ev_consumers:
        if not ((col=='claims' and oid==cid) or (col=='evidence_steps' and oid in evidence_ids)
                or (col=='viewpoint_claim_links' and payload.get('claim_id')==cid)):
            raise ValueError('unhandled live Evidence consumer: '+col+'/'+oid)
    sources=[]
    for (col,_),r in records.items():
        if col=='source_documents' and not r['retired']:
            s={'path':r['payload']['source_path'],'file_sha256':r['payload']['source_file_sha256']}
            check_binding(s); sources.append(s)
    material={'run_id':run_id,'campaign_sha256':group['campaign_sha256'],'claim_id':cid,
              'replacement_claim_id':replacement,'link_ids':sorted(l['viewpoint_claim_link_id'] for l in links if l.get('effective_state')=='active'),
              'evidence_ids':evidence_ids,'before_pins':direction['before_pins'],'read_pins':pin_rows(pins(records)),
              'historical_candidates':historical,'sources':sources,
              'records':[{'collection':c,'object_id':i,**r} for (c,i),r in sorted(records.items())]}
    for p in direction['before_pins']:
        if list(pins(records).get((p['collection'],p['object_id']),())) != p['pin']:
            raise ValueError('human retired Claim/link before-pin changed')
    validate_material(material,direction)
    return json.loads(canonical_json(material))


def package_for(material, authorization, root):
    return {'package_id':'CLAIM-RETIRE-'+sha256_json(material)[:24],
            'source_repair_application':{'schema_version':SCHEMA,'policy':binding(POLICY),'authorization':authorization,'materials':binding(root/'materials.json')}}


def validate_package(package):
    app=package['source_repair_application'];check_binding(app['policy']);check_binding(app['authorization']);check_binding(app['materials'])
    m=body(read(Path(app['materials']['path'])));d=read_artifact(app['authorization']['path'])
    validate_material(m,d)
    for s in m['sources']:check_binding(s)
    if package != package_for(m,app['authorization'],Path(app['materials']['path']).parent):
        raise ValueError('Claim retirement package exceeds approved write set')
    return retirement_keys(m)


def historical_reference_rows(package):
    validate_package(package)
    return body(read(Path(package['source_repair_application']['materials']['path'])))['historical_candidates']


def execute(store, run_id, *, authorization, root, ports):
    from backend.pipeline.cvp_sql_actions import resume_bound_action
    from backend.pipeline.cvp_sql_commit import commit_package, verified_group_backup
    check_binding(authorization);d=read_artifact(authorization['path'])
    aid='CVPA-'+sha256_json({'kind':SCHEMA,'claim_id':d['claim_id'],'before_pins':d['before_pins']})
    root=Path(root)/aid;store.bind_cvp_execution_root(run_id,identity=aid,root=root)
    prior=store.get_cvp_action(run_id,aid)
    if prior and prior['authorization']!=authorization:raise ValueError('Claim retirement authority drift')
    resumed=resume_bound_action(store,run_id,aid)
    if resumed:return resumed
    m=prior['materials'] if prior else prepare_material(store,run_id,d);validate_material(m,d)
    action=store.prepare_cvp_action(run_id,action_id=aid,kind='claim_retirement',authorization=authorization,materials=m)
    write(root/'materials.json',m);package=package_for(m,authorization,root);validate_package(package)
    plan=store.plan_package(package,source_kind=SCHEMA,transactional=True)
    if {(o.collection,o.object_id) for o in plan.operations}!=set(retirement_keys(m)) or any(o.operation!='retire' for o in plan.operations):
        raise ValueError('Claim withdrawal must be exact retire-only set')
    e=store.get_cvp_group(run_id)['reviewed']['evidence']
    return commit_package(store,run_id,package=package,approval={'human_exact_disposition':authorization},
        authorization=authorization,backup=verified_group_backup(root,package,ports),sources=m['sources'],
        read_pins=decoded_pins(m['read_pins']),reason=d['reason'],actor='owner',source_kind=SCHEMA,
        viewpoint_ids=e['viewpoint_ids'],disposition=e.get('disposition'),action=action)
