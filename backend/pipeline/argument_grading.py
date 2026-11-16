"""Grade one generated argument layer against its golden cases.

The answers live in ``backend/pipeline/argument_golden/<lecture>/cases.json``;
see ``docs/wang-knowledge-platform/10-extraction/argument_layer_rebuild_design.md``
§0. A sentence is identified by its transcript script index and exact text, so
grading survives regrouping. Only ``owner`` (final) answers count; drafts are
graded separately so 同工 review can be compared without changing the verdict.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

RECORDS = (('evidence_steps', 'evidence_step_id'), ('observations', 'observation_id'),
           ('position_nodes', 'position_id'), ('questions', 'question_id'))
COVERED = 0.8      # a step is covered when one record holds this share of it
SUBSTANTIAL = 0.25 # a record touching less of a step than this is ignored there
FINAL = {'owner', 'final'}


def norm(text):
    return re.sub(r'[\s，。、！？：；「」『』（）,.!?:;"\'()…]', '', text or '')


def key(sentence):
    return (str(sentence['script_index']), norm(sentence['text']))


def fragment_sentences(fragment, script_index, sentences_by_index):
    """Sentences of the golden answer that this fragment's excerpt holds."""
    excerpt = norm(fragment.get('verbatim_excerpt'))
    if not excerpt:
        return set()
    out = set()
    for s in sentences_by_index.get(str(script_index), ()):
        text = s[1]
        if text and (text in excerpt or (len(excerpt) >= 6 and excerpt in text)):
            out.add(s)
    return out


def coverage(candidate, paragraph_index, sentences_by_index):
    """Map each record (and each Claim's own words) to the answer sentences it holds."""
    fragments = {f['fragment_id']: f for f in candidate.get('source_fragments', [])}

    def held(fragment_ids):
        out = set()
        for fid in fragment_ids:
            f = fragments.get(fid)
            if f is None:
                raise ValueError(f'record names a missing SourceFragment: {fid}')
            index = paragraph_index.get(f['paragraph_key'])
            if index is None:
                raise ValueError(f"SourceFragment {fid} names an unknown paragraph {f['paragraph_key']}")
            out |= fragment_sentences(f, index, sentences_by_index)
        return out

    records = {}
    for collection, id_field in RECORDS:
        for r in candidate.get(collection, []):
            ids = r.get('source_fragment_ids') or []
            if isinstance(ids, str):
                ids = json.loads(ids.replace("'", '"'))
            records[(collection, r[id_field])] = held(ids)
    own = {}
    for c in candidate.get('claims', []):
        ids = [a['source_fragment_id'] for o in c.get('occurrences', []) for a in o.get('anchors', [])
               if not a.get('evidence_id')]
        own[c['claim_id']] = held(ids)
    return records, own


def grade_group(group, records, own):
    """One verdict per answer item, plus records that hold only non-argument sentences."""
    items = group['items']
    sets = {it['id']: {key(s) for s in it['sentences']} for it in items}
    by_text = {norm(s['text']): key(s) for it in items for s in it['sentences']}
    variants = {it['id']: [sets[it['id']]] + [{by_text[norm(t)] for t in alt} for alt in it.get('accept', [])]
                for it in items}
    owned = set().union(*own.values()) if own else set()
    results = []
    touching = defaultdict(set)  # record -> step items it substantially covers
    for it in items:
        want = sets[it['id']]
        verdict, detail = 'correct', ''
        if it['kind'] == 'step':
            hits = {rid: cov & want for rid, cov in records.items()
                    if len(cov & want) >= max(1, SUBSTANTIAL * len(want))}
            for rid in hits:
                touching[rid].add(it['id'])
            if not hits:
                if it.get('optional'):
                    verdict = 'correct'
                elif 'claim_text' in it.get('accept_kind', []) and want <= owned:
                    verdict = 'correct'
                else:
                    verdict, detail = 'missed', 'no record holds this step'
            elif len(hits) > 1:
                verdict, detail = 'split', f'{len(hits)} records: ' + ', '.join(r[1] for r in sorted(hits))
            else:
                (rid, got), = hits.items()
                best = max(len(records[rid] & v) / len(v) for v in variants[it['id']])
                if best < COVERED:
                    verdict, detail = 'partial', f'{rid[1]} holds {len(got)}/{len(want)} sentences'
        elif it['kind'] == 'claim_text':
            in_records = {s for cov in records.values() for s in cov}
            loose = want - owned - (in_records if 'step' in it.get('accept_kind', []) else set())
            if loose:
                verdict, detail = 'missed', f'{len(loose)}/{len(want)} sentences are not a Claim\'s own words'
        elif it['kind'] == 'aside':
            accepted = set().union(*variants[it['id']])
            leaked = {rid[1] for rid, cov in records.items() if cov & want and cov <= accepted}
            leaked |= {rid[1] for rid, cov in records.items() if len(cov & want) >= max(1, SUBSTANTIAL * len(want))}
            if leaked:
                verdict, detail = 'aside_as_argument', 'held by ' + ', '.join(sorted(leaked))
        results.append({'group': group['group'], 'item': it['id'], 'kind': it['kind'], 'status': it['status'],
                        'verdict': verdict, 'detail': detail,
                        'first': it['sentences'][0]['text'][:30]})
    merges = {it['id']: set(it.get('accept_merge', [])) for it in items}
    for rid, ids in touching.items():
        ids = set(ids)
        if len(ids) > 1 and not any(ids <= {i} | merges[i] for i in ids):
            results.append({'group': group['group'], 'item': '+'.join(sorted(ids)), 'kind': 'step',
                            'status': min(next(i['status'] for i in items if i['id'] == x) for x in ids),
                            'verdict': 'merged', 'detail': f'{rid[1]} holds {len(ids)} steps', 'first': ''})
    non_argument = set().union(*(sets[it['id']] for it in items if it['kind'] in ('other', 'aside'))) if items else set()
    argument = set().union(*(sets[it['id']] for it in items if it['kind'] in ('step', 'claim_text'))) if items else set()
    for rid, cov in records.items():
        if cov and cov <= non_argument and not cov & argument:
            results.append({'group': group['group'], 'item': rid[1], 'kind': 'record', 'status': 'owner',
                            'verdict': 'extra', 'detail': 'record holds only asides or filler', 'first': ''})
    return results


def paragraph_indexes(candidate, source_path=None):
    from backend.pipeline import evidence_refresh as e
    import hashlib
    doc = candidate['source_documents'][0]
    path = Path(source_path or doc['source_path'])
    snap = e.source_snapshot({'path': str(path), 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {k: str(p.get('index', p.get('source_index'))) for k, p in snap['paragraphs'].items()}


def grade(cases, group_id, candidate, source_path=None):
    group = next(g for g in cases['groups'] if g['group'] == group_id)
    by_index = defaultdict(set)
    for it in group['items']:
        for s in it['sentences']:
            by_index[str(s['script_index'])].add(key(s))
    records, own = coverage(candidate, paragraph_indexes(candidate, source_path), by_index)
    return grade_group(group, records, own)


def summary(results):
    out = {}
    for scope, keep in (('final', lambda r: r['status'] in FINAL), ('draft', lambda r: r['status'] not in FINAL)):
        rows = [r for r in results if keep(r)]
        counts = defaultdict(int)
        for r in rows:
            counts[r['verdict']] += 1
        out[scope] = dict(counts)
    return out


def main():
    p = argparse.ArgumentParser(description='Grade a generated argument layer against golden cases')
    p.add_argument('--cases', required=True)
    p.add_argument('--group', required=True)
    p.add_argument('--candidate', required=True)
    p.add_argument('--source-file', help='Transcript file when the candidate\'s recorded path has moved')
    a = p.parse_args()
    results = grade(json.loads(Path(a.cases).read_text()), a.group, json.loads(Path(a.candidate).read_text()),
                    a.source_file)
    print(json.dumps({'summary': summary(results), 'errors': [r for r in results if r['verdict'] != 'correct']},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
