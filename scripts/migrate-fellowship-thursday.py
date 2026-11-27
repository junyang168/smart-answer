#!/usr/bin/env python3
"""Preview/apply the approved Friday-to-Thursday transition; preserve history."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.fellowship_timing import SCHEDULE_CHANGED_ON
from backend.sunday_data_lock import sunday_data_lock

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data-root', type=Path, required=True)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
os.environ['DATA_BASE_DIR'] = str(args.data_root)
with sunday_data_lock():
    path = args.data_root / 'config/fellowship.json'
    original = path.read_bytes()
    entries = json.loads(original)
    moved = {}
    for row in entries:
        day = datetime.strptime(row['date'], '%m/%d/%Y').date()
        if day < SCHEDULE_CHANGED_ON or day.weekday() != 4:
            continue
        target = day - timedelta(days=1)
        if row.get('emailSubject') or row.get('emailBodyHtml'):
            raise RuntimeError('Review the saved future email before migrating its date')
        moved[day.isoformat()] = target.isoformat()
        row['date'] = target.strftime('%m/%d/%Y')
    if len({row['date'] for row in entries}) != len(entries):
        raise RuntimeError('New date conflicts with another fellowship record')
    print(json.dumps({'dates':moved, 'apply':args.apply}, ensure_ascii=False))
    if not args.apply or not moved:
        sys.exit(0)
    folder = args.data_root / 'notification' / ('fellowship-thursday-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    folder.mkdir(parents=True,mode=0o700)
    (folder/'fellowship.json').write_bytes(original)
    with sqlite3.connect(args.data_root/'sunday-assistant/assistant.sqlite3') as db:
        with sqlite3.connect(folder/'assistant.sqlite3') as backup:
            db.backup(backup)
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM outbox WHERE status IN ('queued','sending') AND key LIKE 'fellowship:%'").fetchone():
            raise RuntimeError('Pending fellowship outbox needs reconciliation first')
        for old,new in moved.items():
            if db.execute('SELECT 1 FROM fellowship_changes WHERE target_day=?',(old,)).fetchone():
                raise RuntimeError('Future message changes need reconciliation first')
            receipt=args.data_root/'notification/fellowship-delivery'/f'{old}.json'
            if receipt.exists():
                raise RuntimeError('Future email receipt needs reconciliation first')
            db.execute('UPDATE fellowship_cases SET target_day=? WHERE target_day=?',(new,old))
        temporary=path.with_suffix('.thursday.tmp')
        try:
            temporary.write_text(json.dumps(entries,ensure_ascii=False,indent=2)+'\n')
            temporary.replace(path)
            db.commit()
        except BaseException:
            db.rollback()
            temporary.write_bytes(original)
            temporary.replace(path)
            raise
    (folder/'migration.json').write_text(json.dumps({'dates':moved},indent=2)+'\n')
    print('Verified backup:',folder)
