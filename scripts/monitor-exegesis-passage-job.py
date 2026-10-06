#!/usr/bin/env python3
"""Read-only foreground monitor for a detached #411 first-layer job."""
import argparse
import json
import os
from pathlib import Path
import time


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--once',action='store_true');p.add_argument('--interval',type=float,default=15);args=p.parse_args()
    if args.interval<=0:raise ValueError('positive monitor interval required')
    while True:
        path=args.root/'status.json'
        if path.exists():
            status=json.loads(path.read_text());pid=status['pid']
            try:os.kill(pid,0);alive=True
            except ProcessLookupError:alive=False
            report=status|dict(process_alive=alive,stage_elapsed_seconds=round(time.time()-status['updated_at']),output_root=str(args.root))
            print(json.dumps(report,ensure_ascii=False),flush=True)
            if args.once or status['stage'] in {'completed','completed_with_unresolved','failed'} or not alive:return
        elif args.once:
            print(json.dumps(dict(stage='not_started',output_root=str(args.root))),flush=True);return
        time.sleep(args.interval)
if __name__=='__main__':main()
