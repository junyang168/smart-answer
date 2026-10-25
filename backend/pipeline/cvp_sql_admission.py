"""Explicit, bounded, once-only admission of saved CVP completions.

This is a migration, never routine startup. It streams physical hashes, parses
only small plans/receipts/audits, checks committed SQL history, and writes only
cvp_group run records when explicitly requested. No models, apply or snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, sha256_json, record_content_sha
from backend.pipeline.cvp_sql_commit import binding, check_binding, PROJECT
from backend.pipeline.viewpoint_cvp_campaign_runner import validate_schedule, validate_completed_seed
from backend.pipeline.viewpoint_resolution_runtime import write_immutable


class AdmissionReads:
    def __init__(self, *, max_bytes=32*1024**3, max_seconds=300):
        self.max_bytes=max_bytes; self.max_seconds=max_seconds
        self.started=time.perf_counter(); self.bytes=0; self.pins={}; self.documents={}

    def check_budget(self):
        if self.bytes > self.max_bytes or time.perf_counter()-self.started > self.max_seconds:
            raise ValueError("explicit admission budget exhausted; no normal startup fallback")

    def binding(self, path):
        path=Path(path).absolute(); key=str(path)
        if key in self.pins:return self.pins[key]
        before=path.stat(); digest=hashlib.sha256()
        with path.open('rb') as source:
            while chunk:=source.read(1024*1024):
                digest.update(chunk); self.bytes+=len(chunk); self.check_budget()
        after=path.stat()
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise ValueError("physical evidence changed during admission")
        value={"path":key,"file_sha256":digest.hexdigest()};self.pins[key]=value
        return value

    def read(self,path, *, max_json_bytes=8*1024**2):
        key=str(Path(path).absolute())
        if key not in self.documents:
            self.binding(key)
            if Path(key).name in {'before.json','frozen.json','readback.json'} or Path(key).stat().st_size > max_json_bytes:
                raise ValueError("admission must not parse historical whole Registry JSON")
            self.documents[key]=json.loads(Path(key).read_text())
        return self.documents[key]


def completion_change_sets(row, reads, store):
    """Read saved small commit evidence, not historical object snapshots."""
    ids=set();plans={}; queue=list(row['bindings']);seen=set()
    for pin in queue:
        if pin['path'] in seen:continue
        seen.add(pin['path'])
        if reads.binding(pin['path'])!=pin:raise ValueError('historical linked evidence SHA drift')
        name=Path(pin['path']).name
        if not any(word in name for word in ('plan','receipt','verified-result','component-completion','complete.json')):
            continue
        if Path(pin['path']).stat().st_size > 8*1024**2:continue
        document=reads.read(pin['path'])
        if document.get('artifact_sha256') and document['artifact_sha256']!=sha256_json({k:v for k,v in document.items() if k!='artifact_sha256'}):
            raise ValueError('historical completion/plan seal drift')
        children=document.get('authorized_link_corrections',[])
        if children:
            queue.extend(children)
        # A directly bound child completion carries the exact small plan/raw
        # bindings. Consume those bytes once; never reconstruct its parent.
        if name=='verified-result.json' and document.get('status')=='applied_readback_verified':
            queue.extend(document.get('bindings',[]))
        ids.update(re.findall(r'KCS-[0-9a-f]{20}',json.dumps(document)))
        if document.get('change_set_id') and 'operations' in document and 'fingerprint_sha256' in document:
            plans[document['change_set_id']]=document
    histories=[]
    for ident in sorted(ids):
        history=store.get_change_history(ident)
        if history is None or history['status']!='applied':
            # Retained failed/unknown intents are not committed history. They
            # remain pinned evidence, but cannot be used to count completion.
            continue
        plan=plans.get(ident)
        if plan:
            if history['fingerprint_sha256']!=plan['fingerprint_sha256'] or history['source_sha256']!=plan['source_sha256']:
                raise ValueError('saved plan differs from committed SQL ChangeSet: '+ident)
            business=history['operations'][:len(plan['operations'])]
            if len(business)!=len(plan['operations']):raise ValueError('SQL committed history incomplete')
            for expected,actual in zip(plan['operations'],business,strict=True):
                fields=('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
                if any(expected.get(k)!=actual[k] for k in fields):raise ValueError('SQL operation/history identity drift')
        for operation in history['operations']:
            if operation['after'] is None or record_content_sha(operation['after'])!=operation['after_sha256']:
                raise ValueError('SQL committed version missing or corrupted')
        histories.append({'change_set_id':ident,'fingerprint_sha256':history['fingerprint_sha256'],
                          'source_sha256':history['source_sha256'],'operation_count':len(history['operations']),
                          'history_sha256':sha256_json(history)})
    if not histories:raise ValueError('completion has no matching committed SQL history')
    return histories,[reads.pins[path] for path in sorted(seen)]


def adjudication_index(bindings, reads):
    """Index old once reservations from already-pinned small packets.

    An index does not declare an old verdict valid. Reuse still authenticates
    raw/model/source proof; absent/legacy proof must hold, never cause a new call.
    """
    from backend.pipeline.viewpoint_adjudication_history import target_identity
    result={}
    for pin in bindings:
        if Path(pin['path']).name!='packet.json':continue
        packet=reads.read(pin['path'])
        if packet.get('schema_version')!='wang_audit_source_adjudication_packet_v1':continue
        if packet.get('artifact_sha256')!=sha256_json({k:v for k,v in packet.items() if k!='artifact_sha256'}):
            raise ValueError('historical adjudication packet seal drift')
        root=str(Path(pin['path']).parent)
        for item in packet['items']:
            ident=target_identity(item)
            if ident in result and result[ident]['root']!=root:
                result[ident].setdefault('additional_roots',[]).append(root)
            else:result[ident]={'root':root,'packet':pin}
    return result


def preview(store, campaign_path, *, campaign_sha256, expected_seed_sha256, max_bytes=32*1024**3, max_seconds=300):
    reads=AdmissionReads(max_bytes=max_bytes,max_seconds=max_seconds)
    if reads.binding(campaign_path)['file_sha256']!=campaign_sha256:raise ValueError('campaign physical identity drift')
    config=reads.read(campaign_path)
    for name in ('schedule','completed_proofs','cvp_policy','semantic_contract'):
        if reads.binding(config[name]['path'])!=config[name]:raise ValueError('original migration input SHA drift')
    if config['completed_proofs']['file_sha256']!=expected_seed_sha256:raise ValueError('explicit owner checkpoint seed differs')
    schedule=reads.read(config['schedule']['path'])
    # The original #411 manifest contains all original Claim packets. It is an
    # explicit migration input, not a Registry snapshot or routine startup IO.
    manifest=reads.read(schedule['upstream_manifest_path'],max_json_bytes=128*1024**2)
    groups=validate_schedule(schedule,manifest)
    ordered=[{'sequence':r['sequence'],'group_key':r['group_key'],'claim_ids':groups[r['group_key']]['claim_ids']} for r in schedule['schedule']]
    seed=reads.read(config['completed_proofs']['path'])
    completed=[r['group_key'] for r in schedule['schedule'] if r['state']=='already_complete']
    validation=validate_completed_seed(seed,groups,completed,binding_verifier=reads.binding)
    # Explicit migration consumes the exact saved reviewed completion. It does
    # not re-run the old parent/child proof constructor or content models.
    certificates=[]
    for group,row in zip(ordered,seed['groups'],strict=False):
        if row['group_key']!=group['group_key']:raise ValueError('historical seed original order differs')
        histories,physical_bindings=completion_change_sets(row,reads,store)
        certificates.append({'group':group,'certificate':{
            'status':'verified_historical_completion','bindings':physical_bindings,
            'seed':config['completed_proofs'],'original_completion_sha256':sha256_json(row),
            'audit_path':row['audit_path'],'viewpoint_ids':row['viewpoint_ids'],
            'semantic_adjudications':adjudication_index(physical_bindings,reads),
            'change_set_ids':[h['change_set_id'] for h in histories],'history':histories,
            'revisit_required':bool(row.get('revisit_required',False)),
            'independent_audit_complete':row.get('independent_audit_complete',True)}})
    definition={'accepted_grouping_sha256':manifest['artifact_sha256'],'groups':ordered,
                'cvp_policy':config['cvp_policy'],'semantic_contract':config['semantic_contract'],
                'authorized_by':config['authorized_by'],'authorization':{'owner_campaign':reads.binding(campaign_path),'checkpoint_seed':config['completed_proofs']}}
    result={'schema_version':'cvp_sql_admission.v2','definition':definition,'completions':certificates,
            'metrics':{**validation,'physical_bytes':reads.bytes,'seconds':time.perf_counter()-reads.started,
                       'historical_whole_json_parses':0,'model_calls':0,'Registry_writes':0}}
    return {**result,'artifact_sha256':sha256_json(result)}


def admit(store,result):
    if result.get('artifact_sha256')!=sha256_json({k:v for k,v in result.items() if k!='artifact_sha256'}):
        raise ValueError('historical admission result seal differs')
    campaign=store.initialize_cvp_campaign(result['definition'])
    for row in result['completions']:
        store.admit_cvp_completion(campaign_sha256=campaign,group=row['group'],certificate=row['certificate'])
    return campaign


def resume_admission(store, path, *, preview_sha256, campaign_path, campaign_sha256, seed_sha256):
    """Explicit operator-pinned preview; revalidate only the unadmitted suffix.

    A completed SQL prefix is a validated migration checkpoint, not a reason
    to reopen its old archives. Remaining evidence and committed history must
    still match. This mode is never called by routine production startup.
    """
    reads=AdmissionReads()
    if reads.binding(path)['file_sha256']!=preview_sha256:
        raise ValueError('saved admission preview physical identity drift')
    result=reads.read(path)
    if (result.get('schema_version')!='cvp_sql_admission.v2' or
            result.get('artifact_sha256')!=sha256_json({k:v for k,v in result.items() if k!='artifact_sha256'})):
        raise ValueError('saved admission preview seal differs')
    definition=result['definition'];authorization=definition['authorization']
    if authorization['owner_campaign']!=reads.binding(campaign_path) or authorization['owner_campaign']['file_sha256']!=campaign_sha256:
        raise ValueError('saved admission campaign identity drift')
    if authorization['checkpoint_seed']['file_sha256']!=seed_sha256:
        raise ValueError('saved admission owner checkpoint differs')
    for pin in (authorization['checkpoint_seed'],definition['cvp_policy'],definition['semantic_contract']):
        if reads.binding(pin['path'])!=pin:raise ValueError('saved admission input identity drift')
    campaign=store.initialize_cvp_campaign(definition)
    progress=store.cvp_campaign_progress(campaign)
    originals=result['completions']
    if len(progress)>len(originals):raise ValueError('campaign already advanced beyond admission')
    for index,row in enumerate(originals):
        group,certificate=row['group'],row['certificate']
        if index<len(progress):
            saved=store.get_cvp_group(progress[index]['run_id'])
            if (progress[index]['group_key']!=group['group_key'] or saved.get('checkpoint')!='audited'
                    or saved.get('import_certificate')!=certificate):
                raise ValueError('saved SQL admission checkpoint differs')
            continue
        for pin in certificate['bindings']:
            if reads.binding(pin['path'])!=pin:raise ValueError('pending admission evidence SHA drift')
        for history in certificate['history']:
            actual=store.get_change_history(history['change_set_id'])
            if not actual or actual['status']!='applied' or sha256_json(actual)!=history['history_sha256']:
                raise ValueError('pending admission SQL history drift')
        store.admit_cvp_completion(campaign_sha256=campaign,group=group,certificate=certificate)
    return {'sql_campaign':campaign,'resumed_prefix':len(progress),'admitted_groups':len(originals),
            'physical_bytes':reads.bytes,'historical_whole_json_parses':0,'content_writes':0,'models':0}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--campaign-sha256',required=True)
    parser.add_argument('--seed-sha256',required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--saved-preview',type=Path)
    parser.add_argument('--preview-sha256')
    parser.add_argument('--database-url')
    parser.add_argument('--admit',action='store_true')
    args=parser.parse_args(argv);load_dotenv(PROJECT/'.env')
    store=PostgresKnowledgeStore(args.database_url)
    from backend.pipeline.source_reconciliation_runtime import maintenance_lock
    with maintenance_lock():
        if args.saved_preview:
            if not args.admit or not args.preview_sha256 or args.output:
                parser.error('saved-preview requires --admit/--preview-sha256 and no new output')
            print(json.dumps(resume_admission(store,args.saved_preview,preview_sha256=args.preview_sha256,
                campaign_path=args.campaign,campaign_sha256=args.campaign_sha256,seed_sha256=args.seed_sha256)))
            return
        if not args.output or args.preview_sha256:
            parser.error('new admission preview requires --output; no implicit migration')
        result=preview(store,args.campaign,campaign_sha256=args.campaign_sha256,expected_seed_sha256=args.seed_sha256)
        write_immutable(args.output,result)
        if args.admit:print(json.dumps({'sql_campaign':admit(store,result),'content_writes':0,'models':0}))
        else:print(json.dumps(result['metrics']))


if __name__=='__main__':main()
