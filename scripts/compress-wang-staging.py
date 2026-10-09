#!/usr/bin/env python3
"""Transparently compress old Wang staging artifacts on macOS APFS.

Preserves paths, bytes and metadata. Dry run by default. Stop staging writers
before applying. Files under one day old, symlinks and hardlinks are excluded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def signature(path):
    s = path.stat()
    return s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    if sys.platform != 'darwin' or root.name != 'staging' or root.parent.name != 'wang-knowledge-platform':
        p.error('requires macOS and a wang-knowledge-platform/staging directory')
    report = {'apply': args.apply, 'free_before': shutil.disk_usage(root).free, 'files': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    save()
    cutoff = time.time() - 86400
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('node_modules', '.git', '.venv') and not d.startswith('.compress-') and not (Path(base)/d).is_symlink()]
        for name in files:
            path = Path(base)/name
            s = path.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size < 1024**2 or s.st_mtime >= cutoff or s.st_flags & 32:
                continue
            if path.suffix not in ('.json', '.jsonl', '.ndjson', '.md', '.txt', '.dump', '.log'):
                continue
            entry = {'path': str(path), 'bytes': s.st_size, 'allocated_before': s.st_blocks*512, 'status': 'planned'}
            report['files'].append(entry)
            save()
            if not args.apply:
                continue
            before = signature(path)
            expected = sha(path)
            if signature(path) != before:
                raise RuntimeError(f'file changed during hash: {path}')
            entry['sha256_before'] = expected
            with tempfile.TemporaryDirectory(prefix='.compress-', dir=path.parent) as d:
                dest = Path(d)/name
                subprocess.run(['/usr/bin/ditto', '--hfsCompression', str(path), str(dest)], check=True)
                entry['allocated_after'] = dest.stat().st_blocks*512
                if entry['allocated_after'] >= entry['allocated_before']:
                    entry['status'] = 'no-saving'
                else:
                    if sha(dest) != expected or signature(path) != before:
                        raise RuntimeError(f'content changed: {path}')
                    ds = dest.stat()
                    if (ds.st_mode, ds.st_uid, ds.st_gid, ds.st_mtime_ns) != (s.st_mode, s.st_uid, s.st_gid, s.st_mtime_ns):
                        raise RuntimeError(f'metadata mismatch: {path}')
                    os.replace(dest, path)
                    entry['sha256_after'] = sha(path)
                    if entry['sha256_after'] != expected:
                        raise RuntimeError(f'post-replacement hash mismatch: {path}')
                    entry['status'] = 'verified'
            save()
    report['free_after'] = shutil.disk_usage(root).free
    report['verified'] = sum(e['status'] == 'verified' for e in report['files'])
    save()
    print(json.dumps({k:v for k,v in report.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
