#!/usr/bin/env python3
"""Run the fixed master-data sample, retaining each raw attempt and outcome."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

SPEC = importlib.util.spec_from_file_location('quality_masterdata', Path(__file__).with_name('cvp-quality-masterdata-review.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
PF = M.PF


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    plan = json.loads((root / 'evaluation-plan.json').read_text())
    unique = {v['viewpoint_id']: v for v in plan['random_viewpoints']['selected'] + plan['risk_viewpoints']}
    # Cross-sermon reuse is the user's current priority; original samples stay fixed.
    ids = sorted(unique, key=lambda vid: (len(unique[vid]['identity_source_keys']) < 2, vid))
    work = root / 'masterdata-opus-evaluation-v1'
    work.mkdir(exist_ok=True)
    report = []
    pending = []
    for vid in ids:
        valid = [p for p in sorted(work.glob(vid + '-attempt-*/assessment.json'))
                 if not json.loads((p.parent / 'request.json').read_text()).get('neighbor_ids')]
        if valid:
            report.append({'viewpoint_id': vid, 'state': 'saved_semantic_assessment',
                           'assessment': str(valid[-1]), 'assessment_sha256': PF.digest(valid[-1])})
        else:
            pending.append(vid)
    print(json.dumps({'fixed_units': len(ids), 'saved_units': len(report), 'pending_units': len(pending),
                      'model': M.O.MODEL, 'effort': 'high', 'fixed_total_call_limit': None}), flush=True)
    def review(vid):
        command = [sys.executable, str(Path(M.__file__)), '--preflight', str(root), '--viewpoint', vid]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1000)
        log = {'viewpoint_id': vid, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        path = work / ('batch-log-' + PF.sha(log)[:20] + '.json')
        PF.write_new(path, log)
        valid = [p for p in sorted(work.glob(vid + '-attempt-*/assessment.json'))
                 if not json.loads((p.parent / 'request.json').read_text()).get('neighbor_ids')]
        return {'viewpoint_id': vid, 'state': 'answered' if result.returncode == 0 and valid else 'execution_incomplete',
                'log': str(path), 'assessment': str(valid[-1]) if valid else None,
                'assessment_sha256': PF.digest(valid[-1]) if valid else None}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(review, vid): vid for vid in pending}
        for future in as_completed(futures):
            row = future.result()
            report.append(row)
            print(json.dumps({'viewpoint': row['viewpoint_id'], 'state': row['state'],
                              'fixed_units_recorded': len(report)}), flush=True)
    summary = {'fixed_units': len(ids), 'records': sorted(report, key=lambda x: x['viewpoint_id']),
               'whole_quality_evaluation_complete': False, 'independent_from_original_generation': False,
               'registry_writes': 0, 'code_sha256': PF.digest(Path(__file__))}
    file = work / ('batch-' + PF.sha(summary)[:20] + '.json')
    PF.write_new(file, summary)
    print(json.dumps({'batch_summary': str(file)}), flush=True)


if __name__ == '__main__':
    main()
