#!/usr/bin/env python3
"""Deduplicate old Wang staging files using APFS copy-on-write clones.

Dry run by default. All paths and bytes remain available; clones retain separate
inodes, so subsequent writes cannot change another artifact. Run with writers
stopped. Only regular, singly linked files older than one day are considered.
"""
import argparse
import collections
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.name != 'staging' or root.parent.name != 'wang-knowledge-platform':
        parser.error('root must be wang-knowledge-platform/staging')
    if sys.platform != 'darwin':
        parser.error('requires macOS APFS clonefile')
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    clone = libc.clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    sizes = collections.defaultdict(list)
    cutoff = time.time() - 86400
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('node_modules', '.git', '.venv') and not (Path(base) / d).is_symlink()]
        for name in files:
            p = Path(base) / name
            s = p.lstat()
            if stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_size >= 1024**2 and s.st_mtime < cutoff:
                sizes[s.st_size].append(p)
    report = {'root': str(root), 'apply': args.apply, 'free_before': shutil.disk_usage(root).free, 'files': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    save()
    for size, paths in sizes.items():
        if len(paths) < 2:
            continue
        hashes = collections.defaultdict(list)
        for p in paths:
            before = identity(p)
            h = digest(p)
            if identity(p) != before:
                raise RuntimeError(f'file changed during scan: {p}')
            hashes[h].append((p, before))
        for h, copies in hashes.items():
            source, source_id = copies[0]
            for target, target_id in copies[1:]:
                entry = {'source': str(source), 'target': str(target), 'size': size, 'sha256_before': h, 'status': 'planned'}
                report['files'].append(entry)
                save()
                if not args.apply:
                    continue
                fd, name = tempfile.mkstemp(prefix='.apfs-compact-', dir=target.parent)
                os.close(fd)
                os.unlink(name)
                temp = Path(name)
                try:
                    if identity(source) != source_id or identity(target) != target_id:
                        raise RuntimeError('file changed before replacement')
                    if clone(os.fsencode(source), os.fsencode(temp), 0):
                        raise OSError(ctypes.get_errno(), 'clonefile failed')
                    shutil.copystat(target, temp)
                    if digest(temp) != h or digest(target) != h:
                        raise RuntimeError('content hash mismatch')
                    if identity(source) != source_id or identity(target) != target_id:
                        raise RuntimeError('file changed during verification')
                    os.replace(temp, target)
                    entry['sha256_after'] = digest(target)
                    if entry['sha256_after'] != h or target.stat().st_ino == source.stat().st_ino:
                        raise RuntimeError('post-replacement verification failed')
                    entry['status'] = 'verified'
                    save()
                finally:
                    temp.unlink(missing_ok=True)
    report['free_after'] = shutil.disk_usage(root).free
    report['duplicate_bytes'] = sum(f['size'] for f in report['files'])
    save()
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, indent=2))
    print(f"files: {len(report['files'])}; report: {args.report}")


if __name__ == '__main__':
    main()
