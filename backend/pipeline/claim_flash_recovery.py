"""Resume Claim stages from their existing receipts, never from a second review."""
from copy import deepcopy
import json
from pathlib import Path

from backend.pipeline import evidence_refresh as e, claim_human_source as human

VERSION='claim_flash_progress_v1'


def read(path):return json.loads(Path(path).read_text())


def result_ref(root,path):
    """Freeze the EO progress index; per-group review receipts stay strict."""
    root=Path(root);path=Path(path);value=read(path)
    if path.resolve()==(root/'evidence/result.json').resolve():
        path=root/'evidence/reports'/(e.digest(value)+'.json')
        e.save(path,value)
    return {'path':str(path.resolve()),'sha256':e.digest(value)}


def verify_pending_refs(root,saved):
    for ref in saved['evidence_refs']:
        if e.digest(read(ref['path']))==ref['sha256']:continue
        # Old exports bound this mutable index. Its exact original value is
        # embedded in the SHA-bound parent export; never trust a new index as
        # review approval. The runner still revalidates EO normally.
        old=saved.get('stages',{}).get('evidence')
        if (Path(ref['path']).resolve()==(Path(root)/'evidence/result.json').resolve()
                and saved['pending'][0]['layer']=='evidence' and old
                and e.digest(old)==ref['sha256']):
            e.save(Path(root)/'evidence/reports'/(ref['sha256']+'.json'),old)
            continue
        raise ValueError('pending Flash stage evidence changed')


def atomic_save(path,value):
    path=Path(path);temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2))
    temp.replace(path)


def lower_checkpoint(root,intent,stages,*,source_only=False):
    path=Path(root)/('source-layer-input.json' if source_only else 'claim-layer-input.json')
    if not path.exists():
        # Written once at the layer boundary, not another sermon snapshot per task.
        refs=[result_ref(root,p)
              for p in sorted(Path(root).rglob('result.json'))
              if p.parent!=Path(root) and 'claims' not in p.relative_to(root).parts
              and 'exports' not in p.relative_to(root).parts and 'postimages' not in p.relative_to(root).parts]
        value={'version':VERSION,'intent_sha256':e.digest(intent),
            'stages':{k:v for k,v in stages.items() if k!='claims'},'refs':refs,
            'before_sha256':e.digest(read(Path(root)/'before.json'))}
        value['checkpoint_sha256']=e.digest(value)
        atomic_save(path,value)
    value=read(path)
    if value['intent_sha256']!=e.digest(intent):raise ValueError('Flash progress intent changed')
    return e.digest(value)


def start_stage(root,intent,stages,active,lower_sha):
    refs={}
    for group,outputs in stages.get('claims',{}).items():
        refs[group]={}
        for task,result in outputs.items():
            if result.get('pending') or result.get('status') not in {'verified','applied','completed'}:continue
            path=Path(root)/'claims'/group/task/'result.json'
            # The stage just validated and saved its result. Re-read these
            # receipts only on recovery, not after every subsequent stage.
            refs[group][task]={'path':str(path.resolve()),'sha256':e.digest(result)}
    value={'version':VERSION,'intent_sha256':e.digest(intent),'lower_sha256':lower_sha,
           'claims':refs,'active':active}
    # The flow lock owns this file; replace atomically so interruption leaves
    # either the previous cursor or this one, never a partially written cursor.
    atomic_save(Path(root)/'claim-progress.json',value)


def assert_owned_changes(store,root,before,records):
    """Crash recovery only: every advanced row must be an approved local commit."""
    def pin(row):return tuple(row.get(k) for k in ('revision','content_sha256','retired')) if row else None
    changed={key for key in before.keys()|records.keys() if pin(before.get(key))!=pin(records.get(key))}
    if not changed:return
    commits={}
    for path in Path(root).rglob('commit-intent.json'):
        intent=read(path);verification=path.parent/'verification.json'
        if not verification.exists():continue
        value=read(verification);history=store.get_change_history(intent['change_set_id'])
        if not history or history['status']!='applied':continue
        if (history['fingerprint_sha256']!=intent['fingerprint_sha256'] or history['source_kind']!=value['version']
                or Path(history['metadata'].get('verification_path','')).resolve()!=verification.resolve()):
            raise ValueError('Flash recovery SQL receipt differs')
        commits[history['change_set_id']]=value
    with store.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        for collection in sorted({k.split('/',1)[0] for k in changed}):
            ids=[k.split('/',1)[1] for k in changed if k.startswith(collection+'/')]
            rows=conn.execute('''SELECT o.object_id,o.revision,o.content_sha256,o.payload,
                    o.retired_at IS NOT NULL,v.change_set_id
                FROM wang_knowledge.objects o JOIN wang_knowledge.object_versions v
                  ON v.collection=o.collection AND v.object_id=o.object_id AND v.revision=o.revision
                WHERE o.collection=%s AND o.object_id=ANY(%s)''',(collection,ids)).fetchall()
            if len(rows)!=len(ids):raise ValueError('Flash recovery lost a changed row')
            for oid,revision,sha,payload,retired,change_set in rows:
                key=collection+'/'+oid;value=commits.get(change_set)
                if value is None:raise ValueError('Flash recovery found an outside write: '+key)
                if key in records and pin(records[key])!=(revision,sha,retired):
                    raise ValueError('Flash recovery row changed during inspection: '+key)
                if retired:
                    if key not in value.get('retiring_keys',[]):raise ValueError('Flash recovery retirement lacks its receipt')
                else:
                    expected={**value['updates'],**value.get('new_fragments',{})}.get(key)
                    if not expected or any(payload.get(k)!=v for k,v in expected.items() if k!='revision'):
                        raise ValueError('Flash recovery postimage differs: '+key)


def restore(store,root,intent,records,sources,*,source_only=False):
    root=Path(root)
    lower=read(root/('source-layer-input.json' if source_only else 'claim-layer-input.json'))
    progress=read(root/'claim-progress.json') if not source_only and (root/'claim-progress.json').exists() else None
    if (lower['version']!=VERSION or lower['intent_sha256']!=e.digest(intent)
            or progress is not None and (progress['version']!=VERSION
                or progress['intent_sha256']!=e.digest(intent) or progress['lower_sha256']!=e.digest(lower))):
        raise ValueError('Flash recovery progress binding differs')
    if (source_only or 'checkpoint_sha256' in lower) and lower.get('checkpoint_sha256')!=e.digest(
            {k:v for k,v in lower.items() if k!='checkpoint_sha256'}):
        raise ValueError('Flash layer checkpoint changed')
    before=read(root/'before.json')
    if lower.get('before_sha256') and lower['before_sha256']!=e.digest(before):
        raise ValueError('Flash layer original input changed')
    stages=deepcopy(lower['stages']);stages['claims']={};guards=[]
    def verified(ref,*,collect_guard=True):
        path=Path(ref['path']).resolve()
        if not path.is_relative_to(root.resolve()):raise ValueError('Flash recovery receipt is outside the operation')
        value=read(path)
        if e.digest(value)!=ref['sha256']:raise ValueError('Flash recovery stage evidence changed')
        if collect_guard and value.get('human_source_guard'):guards.append(value['human_source_guard'])
        return value
    for ref in lower['refs']:verified(ref,collect_guard=False)
    # Old, superseded group attempts remain immutable audit evidence. Only
    # the accepted lower-layer results define the current interpretation guard.
    active_lower=list(lower['stages'].values())+lower['stages'].get('evidence',{}).get('groups',[])
    guards.extend(value['human_source_guard'] for value in active_lower
                  if isinstance(value,dict) and value.get('human_source_guard'))
    for group,outputs in (progress or {}).get('claims',{}).items():
        stages['claims'][group]={}
        for task,ref in outputs.items():
            if Path(ref['path']).resolve()!=(root/'claims'/group/task/'result.json').resolve():
                raise ValueError('Flash recovery stage path differs')
            value=verified(ref)
            if value.get('pending') or value.get('status') not in {'verified','applied','completed'}:
                raise ValueError('Flash recovery cannot skip an unfinished stage')
            stages['claims'][group][task]=value
    # Human decisions can advance without changing any knowledge-object row.
    current=store.read_human_source_decisions(list(sources))
    for guard in guards:
        if human.scoped(current,guard['paragraphs'])!=guard['entries']:
            raise ValueError('human source decisions changed during Flash recovery')
    assert_owned_changes(store,root,before,records)
    return stages
