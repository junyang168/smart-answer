"""Apply the owner's rulings to one lecture's generated argument layer, without a model call.

After the eval set passes, a lecture's own remaining errors are corrected directly
(argument_shape_eval_set.md §五 step 5) instead of re-running GPT. Each correction
names the owner's ruling. The program writes a corrected copy beside the candidate
and a receipt with both SHAs; the GPT candidate itself is never changed, and no
reader-visible text is written by hand: a correction only removes a record, or
moves a record GPT wrote to the kind the owner ruled, keeping its text.

Operations:
- ``drop_claim``: the Claim is not his point of its own (its content is Evidence for
  another Claim that the steps already support). The Claim and every reference to
  it go; the steps keep supporting the other Claims they named.
- ``position_to_question``: what GPT wrote as a view someone held is a question
  someone asked. The Position becomes a Question with the same id, text and
  fragments, answered by ``answer_claim_id``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _full(candidate, short):
    ids = [c['claim_id'] for c in candidate['claims']] + [p['position_id'] for p in candidate['position_nodes']]
    found = [i for i in ids if i.endswith('-' + short)]
    if len(found) != 1:
        raise ValueError(f'{short}: expected one record, found {len(found)}')
    return found[0]


def _strip(value, gone):
    """Remove an id from every list in a record; return how many were removed."""
    removed = 0
    for key, v in list(value.items()):
        if isinstance(v, list) and gone in v:
            value[key] = [x for x in v if x != gone]
            removed += 1
    return removed


def drop_claim(candidate, short):
    cid = _full(candidate, short)
    before = len(candidate['claims'])
    candidate['claims'] = [c for c in candidate['claims'] if c['claim_id'] != cid]
    relations = [r for r in candidate['knowledge_relations'] if cid in (r['from_id'], r['to_id'])]
    candidate['knowledge_relations'] = [r for r in candidate['knowledge_relations'] if r not in relations]
    candidate['claim_relations'] = [r for r in candidate['claim_relations'] if cid not in json.dumps(r)]
    touched = 0
    for kind in ('evidence_steps', 'observations', 'questions', 'position_nodes', 'claims'):
        for record in candidate[kind]:
            touched += _strip(record, cid)
    if cid in json.dumps(candidate, ensure_ascii=False):
        raise ValueError(f'{cid} is still referenced after the drop')
    return {'removed_claims': before - len(candidate['claims']), 'removed_relations': [r['relation_id'] for r in relations],
            'lists_updated': touched}


def position_to_question(candidate, short, answer_claim_id, questioner='audience'):
    pid = _full(candidate, short)
    answer = _full(candidate, answer_claim_id)
    position = next(p for p in candidate['position_nodes'] if p['position_id'] == pid)
    candidate['position_nodes'] = [p for p in candidate['position_nodes'] if p['position_id'] != pid]
    candidate['questions'].append({'question_id': pid, 'text': position['title'], 'questioner': questioner,
                                   'question_type': 'open_question', 'answer_state': 'answered',
                                   'answer_claim_ids': [answer], 'source_fragment_ids': position['source_fragment_ids'],
                                   'review_status': position.get('review_status', 'candidate')})
    touched = sum(_strip(c, pid) for c in candidate['claims'])
    relations = [r for r in candidate['knowledge_relations'] if pid in (r['from_id'], r['to_id'])]
    candidate['knowledge_relations'] = [r for r in candidate['knowledge_relations'] if r not in relations]
    return {'question_id': pid, 'answer_claim_id': answer, 'opposed_lists_updated': touched,
            'removed_relations': [r['relation_id'] for r in relations]}


OPERATIONS = {'drop_claim': drop_claim, 'position_to_question': position_to_question}


def apply(candidate_path, corrections_path, out_path):
    raw = Path(candidate_path).read_bytes()
    spec_raw = Path(corrections_path).read_bytes()
    candidate = json.loads(raw)
    spec = json.loads(spec_raw)
    done = []
    for c in spec['corrections']:
        args = {k: v for k, v in c.items() if k not in ('op', 'ruling')}
        done.append({**c, 'result': OPERATIONS[c['op']](candidate, **args)})
    out = json.dumps(candidate, ensure_ascii=False, indent=1).encode()
    Path(out_path).write_bytes(out)
    receipt = {'candidate': str(candidate_path), 'candidate_sha256': sha(raw), 'corrections': str(corrections_path),
               'corrections_sha256': sha(spec_raw), 'corrected': str(out_path), 'corrected_sha256': sha(out),
               'applied': done, 'model_calls': 0}
    Path(out_path).with_suffix('.receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=1))
    return receipt


def main():
    p = argparse.ArgumentParser(description="Apply the owner's rulings to a generated argument layer")
    p.add_argument('--candidate', required=True)
    p.add_argument('--corrections', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    print(json.dumps(apply(a.candidate, a.corrections, a.out), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
