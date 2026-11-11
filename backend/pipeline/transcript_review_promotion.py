"""Verified file backup and explicit per-item Beta-to-production handoff.

No publication, review decisions, Claim writes or model calls. Existing public
publication and raw timeline remain unchanged. Replacing a modern draft retains
the old baseline in the verified backup and appends to its existing history.
"""
import argparse
from contextlib import ExitStack,contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil

from backend.api.sc_api.transcript_review import ReviewStore,read,atomic,file_hash,digest

SCHEMA='transcript-production-link.v1'


@contextmanager
def lock_file(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        yield


def inventory(root,item):
    files={}
    fixed=['config/sermon.json','config/config.json','logs/sermon_meta_audit.log']
    for folder in root.glob('script*'):
        if folder.is_dir():fixed.extend(str(p.relative_to(root)) for p in folder.glob(item+'.*') if p.is_file() and not p.name.endswith('.lock'))
    fixed.append('transcript_candidates/'+item+'.json')
    review=root/'transcript_review'/item
    if review.exists():fixed.extend(str(p.relative_to(root)) for p in review.rglob('*') if p.is_file() and p.name!='workflow.lock')
    for relative in sorted(set(fixed)):
        path=root/relative
        if path.is_file():files[relative]={'sha256':file_hash(path),'size':path.stat().st_size}
    return files


def backup(beta_root,production_root,item,output):
    beta_root=Path(beta_root).resolve();production_root=Path(production_root).resolve();output=Path(output).resolve()
    ReviewStore(beta_root).folder(item)
    if beta_root==production_root or any(output.is_relative_to(p) for p in (beta_root,production_root)):
        raise ValueError('backup must be independent and outside both data roots')
    intent={'item':item,'beta_root':str(beta_root),'production_root':str(production_root)}
    if (output/'backup.json').exists():
        result=read(output/'backup.json')
        if result['intent']!=intent:raise ValueError('backup intent changed')
        verify_backup(output,result)
        return result
    output.mkdir(parents=True,exist_ok=True);output.chmod(0o700)
    with ExitStack() as stack:
        stack.enter_context(lock_file(beta_root/'transcript_review'/item/'workflow.lock'))
        for folder in ('script_review','script_published'):
            stack.enter_context(lock_file(production_root/folder/('.'+item+'.json.lock')))
        snapshots={name:inventory(root,item) for name,root in [('beta',beta_root),('production',production_root)]}
        copied_by_sha={}  # Deduplicate within the backup, never hardlink live data.
        for name,root in [('beta',beta_root),('production',production_root)]:
            for relative,entry in snapshots[name].items():
                source=root/relative;target=output/name/relative;target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists() and file_hash(target)!=entry['sha256']:raise ValueError('existing backup differs; preserve it')
                if not target.exists():
                    duplicate=copied_by_sha.get(entry['sha256'])
                    if duplicate is not None:os.link(duplicate,target)
                    else:shutil.copy2(source,target)
                if file_hash(target)!=entry['sha256']:raise ValueError('backup copy checksum mismatch')
                copied_by_sha[entry['sha256']]=target
            if inventory(root,item)!=snapshots[name]:raise ValueError('source changed while backing up; no migration allowed')
        result={'schema_version':'transcript-promotion-backup.v1','intent':intent,'files':snapshots,
            'production_writes':0,'published':False,'claim_writes':0}
        atomic(output/'backup.json',result);verify_backup(output,result)
        return result


def verify_backup(output,result):
    for name,files in result['files'].items():
        for relative,entry in files.items():
            if file_hash(Path(output)/name/relative)!=entry['sha256']:raise ValueError('backup checksum verification failed')


def copy_atomic(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name('.'+target.name+'.promotion')
    shutil.copy2(source,temp)
    with temp.open('rb') as stream:os.fsync(stream.fileno())
    os.replace(temp,target)


def promote(beta_root,production_root,item,backup_root):
    beta_root=Path(beta_root).resolve();production_root=Path(production_root).resolve();backup_root=Path(backup_root).resolve()
    saved=read(backup_root/'backup.json')
    if not saved or saved['intent']!={'item':item,'beta_root':str(beta_root),'production_root':str(production_root)}:
        raise ValueError('exact verified backup is required before promotion')
    verify_backup(backup_root,saved)
    source=ReviewStore(beta_root,permission_root=production_root);target=ReviewStore(production_root)
    marker=source.folder(item)/'production-link.json';receipt_path=target.folder(item)/'beta-promotion.json'
    if marker.exists():
        link=read(marker)
        if link.get('schema_version')!=SCHEMA or link.get('receipt_sha256')!=file_hash(receipt_path):raise ValueError('existing promotion receipt differs')
        return read(receipt_path)
    with ExitStack() as stack:
        stack.enter_context(lock_file(source.folder(item)/'workflow.lock'))
        stack.enter_context(lock_file(target.folder(item)/'workflow.lock'))
        for folder in ('script_review','script_published'):
            stack.enter_context(lock_file(production_root/folder/('.'+item+'.json.lock')))
        stack.enter_context(lock_file(beta_root/'config/.sermon-review.lock'))
        stack.enter_context(lock_file(production_root/'config/.sermon-review.lock'))
        if list(source.folder(item).glob('operations/*.prepared.json')):raise ValueError('uncommitted Beta operation; recover before promotion')
        intent_path=backup_root/'promotion-intent.json';intent=read(intent_path)
        if intent and intent.get('backup_sha256')!=file_hash(backup_root/'backup.json'):raise ValueError('promotion intent backup changed')
        expected_context=saved['files']['beta'][str(Path('transcript_review')/item/'context.json')]['sha256']
        if target.enabled(item) and (not intent or file_hash(target.folder(item)/'context.json')!=expected_context):
            raise ValueError('production already has a different review draft; never overwrite its history')
        # The backup is the CAS baseline. Shared catalog changes on unrelated
        # lectures survive; only this row is compared and replaced.
        for name,root in [('beta',beta_root),('production',production_root)]:
            for relative,entry in saved['files'][name].items():
                if relative.startswith('config/') or relative.startswith('logs/'):continue
                allowed={entry['sha256']}
                if name=='production' and intent and relative in {'script_review/'+item+'.json','script_patched/'+item+'.json'}:
                    if relative in saved['files']['beta']:allowed.add(saved['files']['beta'][relative]['sha256'])
                if file_hash(root/relative) not in allowed:raise ValueError('item changed after backup')
        beta_rows,beta_meta=source.meta(item);prod_rows,prod_meta=target.meta(item)
        old_prod=next(x for x in read(backup_root/'production/config/sermon.json') if x['item']==item)
        merged={**old_prod,**{k:beta_meta.get(k) for k in ('assigned_to','assigned_to_name','assigned_to_date','assignment_epoch')}}
        if old_prod.get('status')!='published':merged['status']=beta_meta.get('status','assigned')
        for name,meta in [('beta',beta_meta),('production',prod_meta)]:
            baseline=next(x for x in read(backup_root/name/'config/sermon.json') if x['item']==item)
            if meta!=baseline and not (name=='production' and intent and meta==merged):raise ValueError('ownership/catalog row changed after backup')
        rows,context=source.load(item)
        source.require(beta_meta['assigned_to'],item,write=True,epoch=beta_meta.get('assignment_epoch'))
        for asset in context['visual_assets'].values():
            if file_hash(beta_root/asset['image'])!=asset['image_sha256']:raise ValueError('visual asset checksum mismatch')
        if file_hash(source.folder(item)/'source.mp4')!=context['source_video_sha256']:raise ValueError('source media checksum mismatch')
        expected_intent={'item':item,'backup_sha256':file_hash(backup_root/'backup.json'),
            'beta_root':str(beta_root),'production_root':str(production_root),'merged_metadata':merged}
        if intent and intent!=expected_intent:raise ValueError('promotion intent changed')
        if not intent:atomic(intent_path,expected_intent)
        # Copy every history/media object except the live workflow lock. Keep
        # production's existing lock inode so waiting requests cannot bypass it.
        relative_folder=Path('transcript_review')/item
        for relative,entry in saved['files']['beta'].items():
            rel=Path(relative)
            if not rel.is_relative_to(relative_folder) or rel.name=='context.json':continue
            dest=production_root/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists() and file_hash(dest)!=entry['sha256']:raise ValueError('production history path collision')
            if not dest.exists():copy_atomic(beta_root/rel,dest)
            if file_hash(dest)!=entry['sha256']:raise ValueError('production copy checksum mismatch')
        # Preserve public publication and raw timeline. The editor's baseline
        # follows its migrated draft; the old baseline remains in the backup.
        script=production_root/'script_review'/f'{item}.json';script.parent.mkdir(parents=True,exist_ok=True)
        copy_atomic(beta_root/'script_review'/script.name,script)
        baseline=beta_root/'script_patched'/script.name
        if baseline.exists():copy_atomic(baseline,production_root/'script_patched'/script.name)
        copied=read(source.folder(item)/'context.json');atomic(target.folder(item)/'context.json',copied)
        atomic(production_root/'config/sermon.json',[merged if x['item']==item else x for x in prod_rows])
        imported_rows,imported_context=target.load(item)
        if imported_rows!=rows or imported_context!=context:raise ValueError('production import readback mismatch')
        value={'schema_version':'transcript-production-promotion.v1','item':item,'beta_root':str(beta_root),
            'production_root':str(production_root),'backup_root':str(backup_root),'backup_sha256':file_hash(backup_root/'backup.json'),
            'revision':context['revision'],'script_sha256':file_hash(script),'context_sha256':file_hash(target.folder(item)/'context.json'),
            'assigned_to':merged['assigned_to'],'published_file_unchanged':file_hash(production_root/'script_published'/f'{item}.json')==saved['files']['production'].get('script_published/'+item+'.json',{}).get('sha256'),
            'review_decisions_added':0,'claim_writes':0,'published_by_migration':False}
        atomic(receipt_path,value)
        atomic(marker,{'schema_version':SCHEMA,'item':item,'production_root':str(production_root),
            'receipt_sha256':file_hash(receipt_path)})
        return value


def replace_review(beta_root, production_root, item, backup_root, merge_run, *, actor):
    """Install a merged incoming draft as a new revision of an existing editor.

    History and current ownership stay in production. Publication is untouched.
    Both sides must use the same verified original media; no timeline guessing.
    """
    from copy import deepcopy
    from backend.pipeline import transcript_three_way_merge as merger
    from backend.pipeline import transcript_claim_migration as migration
    beta_root=Path(beta_root).resolve();production_root=Path(production_root).resolve()
    backup_root=Path(backup_root).resolve();merge_run=Path(merge_run).resolve()
    if beta_root==production_root:raise ValueError('replacement needs a separate incoming review workspace')
    saved=read(backup_root/'backup.json')
    if not saved or saved['intent']!={'item':item,'beta_root':str(beta_root),'production_root':str(production_root)}:
        raise ValueError('exact verified backup required')
    verify_backup(backup_root,saved)
    source=ReviewStore(beta_root,permission_root=production_root);target=ReviewStore(production_root)
    manifest=migration.read(merge_run/'manifest.json')
    if manifest['intent']['item']!=item:raise ValueError('merge belongs to a different lecture')
    for bound in [manifest['intent']['implementation'],*manifest['intent']['files'].values(),*manifest['outputs'].values()]:
        migration.cm.source.verify_file(bound)
    report=migration.read(merge_run/'comparison.json')
    if not report['merge_complete']:raise ValueError('merge conflicts remain')
    expected=deepcopy(report['script'])
    for row in expected:
        if str(row['index']) in report['changed_paragraph_ids']:row['user_id']=actor
    intent_path=backup_root/'replacement-intent.json'
    with ExitStack() as stack:
        for store in sorted([source,target],key=lambda store:str(store.root)):
            stack.enter_context(store.lock(item))
        src_rows,src_context=source.load(item);dst_rows,dst_context=target.load(item)
        _,source_meta=source.meta(item);_,target_meta=target.meta(item)
        source.require(actor,item,write=True,epoch=source_meta.get('assignment_epoch'))
        target.require(actor,item,write=True,epoch=target_meta.get('assignment_epoch'))
        if src_rows!=expected:raise ValueError('incoming editor differs from exact merged result')
        intent=read(intent_path)
        if intent:
            if intent['actor']!=actor or intent['merge_sha256']!=file_hash(merge_run/'manifest.json'):
                raise ValueError('replacement intent changed')
            receipt=target.receipt(item,intent['request']['operation_id'],actor,intent['request'])
            if receipt:
                if intent['request']['epoch']!=target_meta['assignment_epoch']:
                    raise ValueError('replacement ownership epoch changed')
                actual_rows,actual_context=target.load(item)
                if actual_rows!=expected or actual_context!={**src_context,'revision':receipt['revision'],'script_sha256':digest(expected)}:
                    raise ValueError('production moved beyond saved replacement')
                migration.immutable(backup_root/'replacement.json',{'revision':receipt['revision'],
                    'operation_id':intent['request']['operation_id'],'published_by_migration':False,'claim_writes':0,
                    'ownership_preserved':True,'historical_revisions_preserved':True})
                return receipt
        if merger.rows(migration.read(manifest['intent']['files']['existing']['path']))!=dst_rows:
            raise ValueError('merge existing side is not the current production draft')
        # Freeze both exact backups before any target write. Shared config is not
        # replaced; its current per-item ownership is still checked below.
        for name,root in [('beta',beta_root),('production',production_root)]:
            for relative,entry in saved['files'][name].items():
                if relative.startswith(('config/','logs/')):continue
                if file_hash(root/relative)!=entry['sha256']:raise ValueError('draft changed after replacement backup')
            old=next(row for row in read(backup_root/name/'config/sermon.json') if row['item']==item)
            current=source_meta if name=='beta' else target_meta
            if old!=current:raise ValueError('ownership changed after replacement backup')
        if (src_context['source_video_sha256']!=dst_context['source_video_sha256']
                or file_hash(source.folder(item)/'source.mp4')!=src_context['source_video_sha256']
                or file_hash(target.folder(item)/'source.mp4')!=dst_context['source_video_sha256']):
            raise ValueError('replacement media identity differs; explicit media reconciliation required')
        request={'version':digest(target.document_value(dst_rows,dst_context)),
                 'epoch':target_meta['assignment_epoch'],
                 'operation_id':'replace-merged-'+file_hash(merge_run/'manifest.json')[:32],
                 'merge_plan':migration.binding(merge_run/'manifest.json'),
                 'backup':migration.binding(backup_root/'backup.json'),
                 'operations':[{'kind':'adopt_merged_transcript','id':str(row['index'])} for row in src_rows]}
        planned={'actor':actor,'merge_sha256':file_hash(merge_run/'manifest.json'),'request':request}
        if intent and intent!=planned:raise ValueError('replacement preimage changed')
        migration.immutable(intent_path,planned)
        for asset in src_context['visual_assets'].values():
            relative=asset['image'];src=beta_root/relative;dst=production_root/relative
            if not src.resolve().is_relative_to(source.folder(item).resolve()) or not dst.resolve().is_relative_to(target.folder(item).resolve()):
                raise ValueError('asset path escapes lecture')
            if file_hash(src)!=asset['image_sha256']:raise ValueError('incoming asset drift')
            if dst.exists() and file_hash(dst)!=asset['image_sha256']:raise ValueError('asset identity collision')
            if not dst.exists():copy_atomic(src,dst)
        context=deepcopy(src_context)
        context.update(revision=dst_context['revision'],script_sha256=dst_context['script_sha256'])
        baseline=read(beta_root/'script_patched'/f'{item}.json')
        if baseline is None:raise ValueError('incoming pre-edit baseline missing')
        receipt=target.commit(item,actor,request['operation_id'],request,src_rows,context,
            'adopt_merged_transcript',extra={production_root/'script_patched'/f'{item}.json':baseline})
        actual_rows,actual_context=target.load(item)
        if actual_rows!=expected or actual_context!={**src_context,'revision':receipt['revision'],'script_sha256':digest(expected)}:
            raise ValueError('replacement readback differs')
        migration.immutable(backup_root/'replacement.json',{'revision':receipt['revision'],
            'operation_id':request['operation_id'],'published_by_migration':False,'claim_writes':0,
            'ownership_preserved':True,'historical_revisions_preserved':True})
        return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['backup','promote','replace']);p.add_argument('--beta-root',type=Path,required=True);p.add_argument('--production-root',type=Path,required=True);p.add_argument('--item',required=True);p.add_argument('--backup-root',type=Path,required=True)
    p.add_argument('--merge-run',type=Path);p.add_argument('--actor')
    a=p.parse_args()
    if a.action=='replace':
        if not a.merge_run or not a.actor:p.error('replace requires merge-run and actor')
        result=replace_review(a.beta_root,a.production_root,a.item,a.backup_root,a.merge_run,actor=a.actor)
    else:result=backup(a.beta_root,a.production_root,a.item,a.backup_root) if a.action=='backup' else promote(a.beta_root,a.production_root,a.item,a.backup_root)
    print(json.dumps({'action':a.action,'item':a.item,'backup_root':str(a.backup_root),'file_count':sum(map(len,result.get('files',{}).values())) if a.action=='backup' else None,'revision':result.get('revision')},ensure_ascii=False))

if __name__=='__main__':main()
