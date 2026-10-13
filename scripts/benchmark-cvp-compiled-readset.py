#!/usr/bin/env python3
"""Cold/warm N/H growth diagnostics for the projection STAGE only.

Synthetic completed anchors are labelled fixtures; no semantic approval is
created. This cannot satisfy the full startup/completion acceptance gate.
Archives are forbidden to open in each consumer process, not just uncounted.
Each expanded workload gets one new process, cold then warm in the same runtime.
"""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.pipeline import viewpoint_compiled_readset as p
from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import read,write,binding
from backend.pipeline.viewpoint_validation_context import (
    validation_runtime,ValidationContext,ROUTINE_VALIDATION_LIMITS)


def consumer(inputs_path):
    inputs=read(inputs_path)
    actual_open=Path.open
    forbidden={Path(inputs[name]['path']) for name in ('snapshot','completion')}
    def guarded_open(path,*args,**kwargs):
        if path in forbidden:
            raise ValueError('routine consumer opened N/H archive instead of projection')
        return actual_open(path,*args,**kwargs)
    Path.open=guarded_open
    results=[]
    try:
        with validation_runtime(limits=ROUTINE_VALIDATION_LIMITS):
            for mode in ('cold','warm'):
                start=time.perf_counter()
                with ValidationContext('projection stage '+mode) as ctx:
                    result=p.consume_projection(inputs['projection'],inputs['keys'],
                        completion_binding=inputs['completion'],snapshot_binding=inputs['snapshot'])
                if result['rows']!=inputs['expected_rows']:raise ValueError('projected actual row differs')
                results.append(dict(mode=mode,process_id=__import__('os').getpid(),
                    wall_seconds=time.perf_counter()-start,metrics=ctx.metrics,
                    peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    archive_opens=0,sql_payload_rows=0,approval_status=result['approval_status']))
    finally:Path.open=actual_open
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path)
    parser.add_argument('--consume',type=Path)
    args=parser.parse_args()
    if args.consume:
        print(json.dumps(consumer(args.consume)));return
    if args.output_root is None or not args.output_root.is_absolute() or args.output_root.exists():
        raise ValueError('new absolute diagnostic output root required; no overwrite/retry')
    root=args.output_root; root.mkdir(parents=True)
    payload={'claim_id':'C','revision':2,'statement':'Fixed exact 自身原文'}
    keys=['claims/C','claims/absent']
    results=[]
    for n in (1,10,100):
        for h in (1,10,100):
            task=root/f'n{n:03d}-h{h:03d}'
            source=task/'before.json'
            rows=[{'collection':'claims','object_id':'C','payload':payload}]
            rows += [{'collection':'claims','object_id':f'U{i}','payload':{
                'claim_id':f'U{i}','revision':1,'statement':'Unrelated text '*10}} for i in range(20*n)]
            write(source,{'records':rows})
            snapshot=binding(source);completion=task/'fixture-complete.json'
            write(completion,{'status':'complete','bindings':[snapshot],
                'synthetic_projection_fixture_not_admission':True,'unrelated_history':list(range(20*h))})
            complete=binding(completion)
            compiled=p.compile_projection(complete,source,keys,task/'projection.json')
            inputs={'projection':compiled['projection'],'keys':keys,'completion':complete,
                'snapshot':snapshot,'expected_rows':{'claims/C':payload}}
            write(task/'inputs.json',inputs)
            child=subprocess.run([sys.executable,__file__,'--consume',str(task/'inputs.json')],
                text=True,capture_output=True,timeout=10,check=True)
            outcomes=json.loads(child.stdout)
            results.append(dict(unrelated_records_scale=n,history_scale=h,
                readset_sha256=c.sha256_json(keys),projection_bytes=compiled['output_bytes'],modes=outcomes))
    base=results[0]
    for result in results:
        if result['projection_bytes']!=base['projection_bytes']:
            raise ValueError('projection size grew with unrelated history/records')
        for row,baseline in zip(result['modes'],base['modes']):
            if (row['metrics']['file_io_bytes']!=baseline['metrics']['file_io_bytes']
                    or row['metrics']['json_parses']!=baseline['metrics']['json_parses']
                    or row['metrics']['proof_runs']!=baseline['metrics']['proof_runs']
                    or row['wall_seconds']>5):
                raise ValueError('projection-stage fixed-readset growth budget failed')
    report=dict(status='projection_stage_growth_pass_not_full_admission',
        model_calls=0,registry_writes=0,production_resume_authorized=False,
        full_scalability_acceptance=False,workloads='synthetic projection fixtures, NOT approved epochs',
        actual_global_integrity_guard_executed=False,actual_full_epoch_guard_executed=False,
        results=results)
    write(root/'stage-diagnostics.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
