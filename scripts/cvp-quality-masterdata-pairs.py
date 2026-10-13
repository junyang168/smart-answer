#!/usr/bin/env python3
"""Evaluate nominated same-identity and distinct-control pairs against full sources."""
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
    materials = root / 'masterdata-extended-material-v1'
    manifest = json.loads((materials / 'manifest.json').read_text())
    nomination_file = Path(manifest['candidate_file'])
    if PF.digest(nomination_file) != manifest['candidate_file_sha256']:
        raise ValueError('nominations changed')
    pairs = json.loads(nomination_file.read_text())['pairs']
    complete = {e['file'].removesuffix('.forward.json') for e in manifest['entries']}
    work = root / 'masterdata-opus-evaluation-v1'
    records, pending = [], []
    for pair in pairs:
        left, right = pair['left_viewpoint_id'], pair['right_viewpoint_id']
        if not {left, right}.issubset(complete):
            records.append({'pair': pair, 'state': 'source_provenance_not_assessable', 'retained_in_denominator': True})
            continue
        saved = [p for p in sorted(work.glob(left + '-attempt-*/assessment.json'))
                 if json.loads((p.parent / 'request.json').read_text()).get('neighbor_ids') == [right]]
        if saved:
            records.append({'pair': pair, 'state': 'saved_pair_assessment', 'assessment': str(saved[-1]),
                            'assessment_sha256': PF.digest(saved[-1])})
        else:
            pending.append(pair)
    print(json.dumps({'fixed_candidate_pairs': len(pairs), 'existing_or_restricted': len(records), 'pending': len(pending)}), flush=True)
    def review(pair):
        left, right = pair['left_viewpoint_id'], pair['right_viewpoint_id']
        command = [sys.executable, str(Path(M.__file__)), '--preflight', str(root), '--viewpoint', left,
                   '--neighbor', right, '--material-dir', str(materials)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1000)
        log = {'left': left, 'right': right, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        file = work / ('pair-log-' + PF.sha(log)[:20] + '.json')
        PF.write_new(file, log)
        saved = [p for p in sorted(work.glob(left + '-attempt-*/assessment.json'))
                 if json.loads((p.parent / 'request.json').read_text()).get('neighbor_ids') == [right]]
        return {'pair': pair, 'state': 'answered' if saved else 'execution_incomplete', 'log': str(file),
                'assessment': str(saved[-1]) if saved else None,
                'assessment_sha256': PF.digest(saved[-1]) if saved else None}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(review, p) for p in pending]
        for future in as_completed(futures):
            row = future.result()
            records.append(row)
            print(json.dumps({'left': row['pair']['left_viewpoint_id'], 'right': row['pair']['right_viewpoint_id'],
                              'state': row['state'], 'fixed_pairs_recorded': len(records)}), flush=True)
    summary = {'candidate_file_sha256': PF.digest(nomination_file), 'fixed_pair_denominator': len(pairs),
        'records': records, 'not_a_population_recall_estimate': True, 'independent_from_original_generation': False,
        'registry_writes': 0, 'code_sha256': PF.digest(Path(__file__))}
    file = work / ('pairs-' + PF.sha(summary)[:20] + '.json')
    PF.write_new(file, summary)
    print(json.dumps({'pair_summary': str(file)}), flush=True)


if __name__ == '__main__':
    main()
