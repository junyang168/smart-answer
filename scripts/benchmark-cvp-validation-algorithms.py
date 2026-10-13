#!/usr/bin/env python3
"""Bounded offline diagnostics; NEVER a scalability release acceptance.

Each scale runs in a new process with a wall timeout. These are full-snapshot
compatibility algorithms: growth is expected and explicitly disqualifies them
as the eventual fixed-readset routine path. No models, SQL, backup or queue.
"""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runtime as runtime


def old_fingerprint(states):
    cols=sorted(set(c.KNOWLEDGE_COLLECTIONS)|{c.split_key(k)[0] for k in states})
    return c.sha256_json({col:sorted(c.sha256_json(p) for k,p in states.items()
                                   if c.split_key(k)[0]==col) for col in cols})


def timed(fn):
    started=time.perf_counter();result=fn()
    return result,round(time.perf_counter()-started,6)


def child(scale):
    # Unrelated rows grow, but all writes and matching dependencies stay fixed.
    before={f'claims/U{i}':{'statement':'Unrelated exact text '*8,'revision':1} for i in range(1000*scale)}
    for i in range(8):
        before[f'product_dependencies/D{i}']={'dependency_id':f'D{i}','claim_id':'target','revision':1,
            'dependency_manifest':[{'collection':'claims','record_id':f'C{j}'} for j in range(32)]}
    plan={'change_set_id':'KCS-diagnostic-only','operations':[
        dict(collection='claims',object_id=f'C{i}',before_revision=1,after_revision=2) for i in range(32)]}
    old,cold_old=timed(lambda:old_fingerprint(before))
    new,cold_new=timed(lambda:c.fingerprint(before))
    assert old==new
    warm,warm_new=timed(lambda:c.fingerprint(before));assert warm==old
    # Structural tests independently prove one pass (including all causes).
    result,invalidations=timed(lambda:runtime.expected_invalidations(before,before,plan))
    assert len(result['product_dependencies/D0']['invalidation_event_ids'])==32
    assert before['product_dependencies/D0']['revision']==1
    return dict(scale=scale,records=len(before),writes=32,process_id=__import__('os').getpid(),
        fingerprint_first_old_seconds=cold_old,fingerprint_first_new_seconds=cold_new,
        fingerprint_repeat_new_seconds=warm_new,invalidation_new_seconds=invalidations,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        full_cut_complexity='O(N+R+affected_edges), NOT fixed-D routine acceptance')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child-scale',type=int,choices=(1,10,100))
    args=parser.parse_args()
    if args.child_scale:
        print(json.dumps(child(args.child_scale)));return
    results=[]
    for scale in (1,10,100):
        result=subprocess.run([sys.executable,__file__,'--child-scale',str(scale)],
            text=True,capture_output=True,timeout=20,check=True)
        results.append(json.loads(result.stdout))
    print(json.dumps(dict(status='diagnostic_only',production_resume_authorized=False,
        full_scalability_acceptance=False,model_calls=0,registry_writes=0,results=results),indent=2))


if __name__=='__main__':main()
