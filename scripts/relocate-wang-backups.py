#!/usr/bin/env python3
"""Move Wang .dump backups to a mounted external volume, preserving symlinks.

Dry run by default. Copy, fsync, verify SHA-256 and pg_restore readability before
atomically replacing each source with a symlink. Stop backup writers first.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def signature(p):
    s = p.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('volume', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root, volume = args.root.resolve(), args.volume.resolve()
    if root.name != 'staging' or root.parent.name != 'wang-knowledge-platform':
        parser.error('root must be wang-knowledge-platform/staging')
    if not os.path.ismount(volume) or volume.stat().st_dev == root.stat().st_dev:
        parser.error('destination must be a mounted separate volume')
    dest_root = volume/'Wang Knowledge Platform'/'database-backups'/'staging'
    sources = sorted(p for p in root.rglob('*.dump') if p.is_file() and not p.is_symlink())
    required = sum(p.stat().st_size for p in sources)
    if shutil.disk_usage(volume).free < required + 1024**3:
        parser.error('insufficient destination free space')
    report = {'destination': str(dest_root), 'apply': args.apply, 'free_before': shutil.disk_usage(root).free, 'files': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    save()
    for source in sources:
        target = dest_root/source.relative_to(root)
        entry = {'source': str(source), 'destination': str(target), 'bytes': source.stat().st_size, 'status': 'planned'}
        report['files'].append(entry)
        save()
        if not args.apply:
            continue
        before = signature(source)
        expected = digest(source)
        entry['sha256_before'] = expected
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='.relocate-', dir=target.parent) as d:
                temp = Path(d)/source.name
                subprocess.run(['/usr/bin/ditto', '--preserveHFSCompression', str(source), str(temp)], check=True)
                with temp.open('rb') as f:
                    os.fsync(f.fileno())
                if digest(temp) != expected or signature(source) != before:
                    raise RuntimeError(f'file changed during copy: {source}')
                os.rename(temp, target)
        if digest(target) != expected or signature(source) != before:
            raise RuntimeError(f'destination mismatch or changed source: {source}')
        subprocess.run(['pg_restore', '--list', str(target)], check=True, stdout=subprocess.DEVNULL)
        entry['sha256_after'] = digest(target)
        entry['status'] = 'copied-and-verified'
        save()
        fd, name = tempfile.mkstemp(prefix='.backup-link-', dir=source.parent)
        os.close(fd)
        os.unlink(name)
        link = Path(name)
        try:
            link.symlink_to(target)
            if signature(source) != before:
                raise RuntimeError(f'source changed before removal: {source}')
            os.replace(link, source)
            if digest(source) != expected:
                raise RuntimeError(f'symlink readback mismatch: {source}')
            entry['status'] = 'relocated-and-verified'
            save()
        finally:
            link.unlink(missing_ok=True)
    report['free_after'] = shutil.disk_usage(root).free
    report['count'] = len(report['files'])
    save()
    if args.apply:
        shutil.copy2(args.report, dest_root.parent/args.report.name)
    print(json.dumps({k:v for k,v in report.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
