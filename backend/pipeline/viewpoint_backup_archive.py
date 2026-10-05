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
        and re.search(r'(?:^|-)357(?:-|$)',p.parent.name) is not None and '358' not in p.parent.name)


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
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    if Path(__file__).resolve().parents[2].name!='wkp-357-worktree-per-session':
        raise ValueError('only #357 worktree may migrate these backups')
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock, (BASE/'registry-cvp.lock').open('a') as registry:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(registry,fcntl.LOCK_EX|fcntl.LOCK_NB)
        verify_volume();paths,kept=inventory()
        summary={'count':len(paths),'bytes':sum(p.stat().st_size for p in paths),
            'kept_local':[str(p) for p in kept],'archive_root':str(DEST),'volume_uuid':UUID}
        print(json.dumps(summary),flush=True)
        if not args.apply:return
        root=BASE/'registry-backup-ssd-migration-20261005-v1'
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
