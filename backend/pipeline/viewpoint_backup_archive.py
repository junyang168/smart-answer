"""Owner-authorized #357 snapshots on Jun SSD, preserving old proof paths."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile

from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

BASE=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
LOCAL=BASE.parent/'viewpoint-backfill'
MOUNT=Path('/Volumes/Jun SSD')
DEST=MOUNT/'WangKnowledgePlatform/registry-backups/357'
UUID='8D2A5FB1-CC0A-4230-B338-188BF47B06F3'
NAME='smart_answer_knowledge.dump'
RECEIPT='ssd-archive-receipt.json'
SCOPE='357-owner-backup-scope.json'


def authorize_backup(path, proof):
    """Explicit owner-scoped migration of an old unlabelled backup; never glob it."""
    p=Path(path).absolute(); proof=Path(proof).absolute()
    if p.name!=NAME or p.parent.parent!=LOCAL or '358' in p.parent.name:
        raise ValueError('foreign backup')
    allowed=(BASE, BASE.parent/'source-reconciliation')
    if not any(proof.is_relative_to(root) for root in allowed) or '358' in str(proof):
        raise ValueError('foreign backup proof')
    value=json.loads(proof.read_text())
    dump=value.get('dump',value)
    if dump.get('path')!=str(p) or dump.get('file_sha256')!=digest(p):
        raise ValueError('exact backup binding missing or changed')
    if ('restore_list_exit_code' in value and value['restore_list_exit_code']!=0
            or 'restore_catalog_nonempty' in value and not value['restore_catalog_nonempty']
            or 'pg_restore_list_passed' in value and not value['pg_restore_list_passed']):
        raise ValueError('backup restore proof missing')
    catalog(p)  # Independently prove the current exact dump, including old receipt shapes.
    value={'task':'#357','original_path':str(p),'backup_sha256':digest(p),
           'proof_path':str(proof),'proof_sha256':digest(proof)}
    write_immutable(p.parent/SCOPE,{**value,'artifact_sha256':sha256_json(value)})


def explicit_scope(p):
    scope=p.parent/SCOPE
    if not scope.is_file():return False
    value=json.loads(scope.read_text()); body={k:v for k,v in value.items() if k!='artifact_sha256'}
    proof=Path(value.get('proof_path',''))
    if (value.get('artifact_sha256')!=sha256_json(body) or value.get('task')!='#357'
            or value.get('original_path')!=str(p) or not proof.is_file()
            or digest(proof)!=value.get('proof_sha256') or digest(p)!=value.get('backup_sha256')):
        raise ValueError('explicit backup scope drift')
    return True


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def verify_volume():
    if not os.path.ismount(MOUNT):raise ValueError('Jun SSD is not mounted; no internal fallback')
    info=plistlib.loads(subprocess.check_output(['diskutil','info','-plist',str(MOUNT)]))
    if info.get('VolumeUUID')!=UUID or info.get('MountPoint')!=str(MOUNT) or info.get('ReadOnlyVolume'):
        raise ValueError('external SSD identity/mount/read-write mismatch')
    return {'mount':str(MOUNT),'volume_uuid':UUID}


def eligible(path):
    p=Path(path).absolute()
    return (p.name==NAME and p.parent.parent==LOCAL and p.parent.name.startswith('registry-backup-')
        and '358' not in p.parent.name
        and (re.search(r'(?:^|-)357(?:-|$)',p.parent.name) is not None or explicit_scope(p)))


def create_backup(label, database_url):
    """#357 writes directly to the verified SSD, retaining its logical proof path."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}',label) or not re.search(r'(?:^|-)357(?:-|$)',label) or '358' in label:
        raise ValueError('exact #357 label required')
    from datetime import datetime,timezone
    verify_volume()
    name='registry-backup-'+datetime.now(timezone.utc).strftime('%Y-%m-%d')+'-'+label
    p=LOCAL/name/NAME; target=DEST/name/NAME
    if p.parent.exists() or target.parent.exists():raise ValueError('backup already attempted; no overwrite')
    if shutil.disk_usage(MOUNT).free<2*1024**3:raise ValueError('SSD backup reserve below 2GiB')
    target.parent.mkdir(parents=True,mode=0o700); p.parent.mkdir(parents=True,mode=0o700)
    temp=target.parent/(NAME+'.partial')
    # Failed files are preserved, not retried or removed by this function.
    subprocess.run(['pg_dump','--format=custom','--file='+str(temp),database_url],check=True,capture_output=True)
    if not temp.stat().st_size:raise ValueError('empty backup')
    cat=catalog(temp); sha=digest(temp); size=temp.stat().st_size
    with temp.open('rb') as f:os.fsync(f.fileno())
    verify_volume(); os.replace(temp,target)
    body={'schema_version':'wang_registry_backup_ssd_archive_v1','task':'#357',
        'original_path':str(p),'archive_path':str(target),'volume_uuid':UUID,
        'file_sha256':sha,'size':size,'restore_catalog_sha256':cat,
        'status':'copied_sha_and_restore_catalog_verified','registry_writes':0,'model_calls':0,
        'direct_ssd':True}
    receipt={**body,'artifact_sha256':sha256_json(body)}
    write_immutable(target.parent/RECEIPT,receipt);write_immutable(p.parent/RECEIPT,receipt)
    p.symlink_to(target)
    if logical_binding_path(p)!=p:raise ValueError('backup logical path verification failed')
    print(f'backup-registry: wrote {p} ({size} bytes, verified Jun SSD)',flush=True)
    return receipt


def logical_binding_path(path):
    """Only a verified explicit archive may retain the original logical path."""
    p=Path(path).absolute()
    if not eligible(p) or not p.is_symlink():return p.resolve()
    receipt=p.parent/RECEIPT
    if not receipt.is_file():raise ValueError('backup archive symlink has no migration receipt')
    r=json.loads(receipt.read_text())
    if r.get('artifact_sha256')!=sha256_json({k:v for k,v in r.items() if k!='artifact_sha256'}):
        raise ValueError('backup archive receipt SHA drift')
    expected=DEST/p.parent.name/NAME
    if (r.get('original_path')!=str(p) or r.get('archive_path')!=str(expected)
            or r.get('volume_uuid')!=UUID or r.get('task')!='#357'
            or r.get('status')!='copied_sha_and_restore_catalog_verified'
            or p.resolve()!=expected or not expected.is_file()):
        raise ValueError('backup archive exact path/identity drift or disk missing')
    verify_volume()
    if expected.stat().st_size!=r['size'] or digest(expected)!=r['file_sha256']:
        raise ValueError('backup archive physical size/SHA drift')
    return p


def catalog(path):
    out=subprocess.check_output(['pg_restore','--list',str(path)])
    if not out.strip():raise ValueError('empty backup restore catalog')
    return hashlib.sha256(out).hexdigest()


def migrate_one(p):
    p=Path(p).absolute()
    if not eligible(p) or p.parent.is_symlink() or p.is_symlink() or not p.is_file():raise ValueError('outside exact #357 regular backup scope')
    verify_volume()
    before=p.stat();sha=digest(p)
    target=DEST/p.parent.name/NAME
    if target.parent.exists() or (p.parent/RECEIPT).exists():
        raise ValueError('archive target/receipt already exists; never overwrite or retry')
    target.parent.mkdir(parents=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix='.copy-',dir=target.parent)
    os.close(fd);temp=Path(tmp)
    try:
        shutil.copy2(p,temp)
        with temp.open('rb') as f:os.fsync(f.fileno())
        if temp.stat().st_size!=before.st_size or digest(temp)!=sha:
            raise ValueError('SSD copy SHA mismatch; local backup retained')
        cat=catalog(temp)
        verify_volume()
        now=p.stat()
        if (now.st_ino,now.st_size,now.st_mtime_ns)!=(before.st_ino,before.st_size,before.st_mtime_ns) or digest(p)!=sha:
            raise ValueError('local backup changed during copy; no replacement')
        os.replace(temp,target)
        body={'schema_version':'wang_registry_backup_ssd_archive_v1','task':'#357',
            'original_path':str(p),'archive_path':str(target),'volume_uuid':UUID,
            'file_sha256':sha,'size':before.st_size,'restore_catalog_sha256':cat,
            'status':'copied_sha_and_restore_catalog_verified','registry_writes':0,'model_calls':0}
        receipt={**body,'artifact_sha256':sha256_json(body)}
        write_immutable(target.parent/RECEIPT,receipt)
        write_immutable(p.parent/RECEIPT,receipt)
        link=p.parent/(NAME+'.ssd-link')
        if link.exists() or link.is_symlink():raise ValueError('replacement pointer already exists')
        link.symlink_to(target)
        # The verified SSD copy is durable before the local inode is released.
        os.replace(link,p)
        if logical_binding_path(p)!=p:raise ValueError('old logical proof path not preserved')
        write_immutable(p.parent/'ssd-archive-cutover.json',{'receipt_sha256':receipt['artifact_sha256'],
            'original_path_readback_sha256':digest(p),'local_bytes_released':before.st_size})
        return receipt
    finally:
        if temp.exists():temp.unlink()


def inventory():
    paths=[p for p in LOCAL.glob('registry-backup-*/'+NAME) if eligible(p) and p.is_file() and not p.is_symlink()]
    paths.sort(key=lambda p:(p.stat().st_mtime_ns,str(p)),reverse=True)
    return paths[2:],paths[:2]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    parser.add_argument('--create-label');parser.add_argument('--migration-root',type=Path)
    parser.add_argument('--backup-proof',type=Path,action='append',default=[]);args=parser.parse_args()
    if Path(__file__).resolve().parents[2].name!='wkp-357-worktree-per-session':
        raise ValueError('only #357 worktree may migrate these backups')
    if args.create_label:
        create_backup(args.create_label,os.environ['KNOWLEDGE_DATABASE_URL']);return
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock, (BASE/'registry-cvp.lock').open('a') as registry:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(registry,fcntl.LOCK_EX|fcntl.LOCK_NB)
        verify_volume()
        if args.backup_proof:
            if not args.apply or not args.migration_root:raise ValueError('explicit new owner migration root required')
            for proof in args.backup_proof:
                value=json.loads(proof.read_text())
                authorize_backup(value.get('dump',value)['path'],proof)
        paths,kept=inventory()
        if args.migration_root:paths+=kept;kept=[]
        summary={'count':len(paths),'bytes':sum(p.stat().st_size for p in paths),
            'kept_local':[str(p) for p in kept],'archive_root':str(DEST),'volume_uuid':UUID}
        print(json.dumps(summary),flush=True)
        if not args.apply:return
        root=args.migration_root or BASE/'registry-backup-ssd-migration-20261005-v1'
        if root.parent!=BASE or not root.name.startswith('registry-backup-ssd-'):
            raise ValueError('independent #357 migration root required')
        if root.exists():raise ValueError('migration already attempted; no silent restart')
        root.mkdir();write_immutable(root/'plan.json',{**summary,'paths':[str(p) for p in paths]})
        results=[]
        try:
            for p in paths:
                results.append(migrate_one(p));print(f'archived {len(results)}/{len(paths)} {p.parent.name}',flush=True)
            write_immutable(root/'result.json',{'status':'all_selected_backups_verified_and_relocated',
                'count':len(results),'bytes_released':sum(r['size'] for r in results),'receipts':results,
                'kept_local':[str(p) for p in kept],'registry_writes':0,'model_calls':0})
        except Exception as exc:
            write_immutable(root/'failure.json',{'error':str(exc),'completed':len(results),'local_data_not_deleted_unverified':True})
            raise


if __name__=='__main__':main()
