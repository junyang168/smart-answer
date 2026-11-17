"""Grade a run's Claim outline against the owner's Claims in the golden cases.

Whether a generated Claim states the same judgement as an owner Claim is a
reading judgement, so it is made by a reviewer and saved as a key under
``argument_golden/<lecture>/outline/<run>.json``; this module only counts.
Each key entry quotes the generated statement, so a key never grades a run it
was not written for.

Verdicts per generated Claim:
  match              states the judgement of one owner Claim
  sub                a point he argued that the owner key does not list (allowed; the key may grow)
  question           a question or tension he raised, written as a Claim (he asserts nothing there)
  evidence_as_claim  a definition, reading or comparison he uses as Evidence, written as a Claim
  aside              taken from a 題外話
  added              says something he did not
Per owner Claim: covered by exactly one ``match`` (``missed`` if none, ``split`` if several).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

KINDS = {'match', 'sub', 'question', 'evidence_as_claim', 'aside', 'added'}
ERRORS = KINDS - {'match', 'sub'}


def grade_group(owner_claims, generated, key):
    """owner_claims: {CL: text}; generated: {id: statement}; key: {id: {kind, owner?, why}}."""
    rows = []
    unjudged = sorted(set(generated) - set(key))
    stale = sorted(k for k in key if k not in generated or key[k]['statement'] != generated[k])
    if unjudged or stale:
        raise ValueError(f'outline key does not fit this run: unjudged {unjudged}, stale {stale}')
    covered = {cl: [] for cl in owner_claims}
    for cid, entry in sorted(key.items()):
        if entry['kind'] not in KINDS:
            raise ValueError(f'{cid}: unknown kind {entry["kind"]}')
        if entry['kind'] == 'match':
            if entry.get('owner') not in owner_claims:
                raise ValueError(f'{cid}: match names no owner Claim')
            covered[entry['owner']].append(cid)
        if entry['kind'] in ERRORS:
            rows.append({'claim': cid, 'verdict': entry['kind'], 'why': entry['why']})
        elif entry['kind'] == 'sub':
            rows.append({'claim': cid, 'verdict': 'sub', 'why': entry['why']})
    for cl, ids in covered.items():
        if not ids:
            rows.append({'claim': cl, 'verdict': 'missed', 'why': owner_claims[cl]})
        elif len(ids) > 1:
            rows.append({'claim': cl, 'verdict': 'split', 'why': f'{owner_claims[cl]} ← {", ".join(ids)}'})
    correct = sum(1 for ids in covered.values() if len(ids) == 1)
    return {'owner_claims': len(owner_claims), 'covered_once': correct,
            'wrong_claims': sum(1 for r in rows if r['verdict'] in ERRORS), 'rows': rows}


def generated_claims(run_root, folder):
    result = json.loads((Path(run_root) / folder / 'creation' / 'creation-result.json').read_text())
    return {k: v['statement'] for k, v in result['claim_candidates'].items()}


def grade(cases, key, run_root):
    groups = {g['group']: g for g in cases['groups']}
    out = {}
    for gid, entry in key['groups'].items():
        out[gid] = grade_group(groups[gid]['claims'], generated_claims(run_root, entry['folder']), entry['claims'])
    return out


def main():
    p = argparse.ArgumentParser(description='Grade a run\'s Claim outline against the owner Claims')
    p.add_argument('--cases', required=True)
    p.add_argument('--key', required=True)
    p.add_argument('--run-root', required=True)
    a = p.parse_args()
    result = grade(json.loads(Path(a.cases).read_text()), json.loads(Path(a.key).read_text()), a.run_root)
    total = {k: sum(r[k] for r in result.values()) for k in ('owner_claims', 'covered_once', 'wrong_claims')}
    print(json.dumps({'total': total, 'groups': result}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
