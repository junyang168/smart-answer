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
- ``restate_claim``: the owner rules a Claim's sentence missed his own summary of it.
  The only hand-written text this module allows, and only with the owner's ruling:
  ``statement`` replaces the Claim's sentence (the old one is kept in the receipt),
  and the fragment where he says that summary (``basis_fragment_id``, already cut
  from his transcript) is added as one more place he says the Claim.
"""
from __future__ import annotations

import argparse
import copy
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


def restate_claim(candidate, short, statement, basis_fragment_id):
    cid = _full(candidate, short)
    claim = next(c for c in candidate['claims'] if c['claim_id'] == cid)
    fragment = next((f for f in candidate['source_fragments'] if f['fragment_id'] == basis_fragment_id), None)
    if fragment is None:
        raise ValueError(f'{basis_fragment_id}: not a fragment of this lecture')
    if not statement.strip():
        raise ValueError('restate_claim needs a statement')
    before = claim['title']
    claim['title'] = statement
    anchors = claim['occurrences'][0]['anchors']
    if all(a.get('source_fragment_id') != basis_fragment_id for a in anchors):
        anchors.append({'source_fragment_id': basis_fragment_id, 'paragraph_key': fragment['paragraph_key'],
                        'media_time': fragment.get('media_time'), 'media_end_time': fragment.get('media_end_time'),
                        'proposed_highlight': {'text': fragment['verbatim_excerpt'], 'status': 'proposed'}})
    return {'claim_id': cid, 'before': before, 'after': statement, 'basis': fragment['verbatim_excerpt']}


OPERATIONS = {'drop_claim': drop_claim, 'position_to_question': position_to_question, 'restate_claim': restate_claim}


def corrected(candidate, spec):
    """Pure correction replay, shared by the writer and the loader."""
    candidate = copy.deepcopy(candidate)
    done = []
    for c in spec['corrections']:
        if c['op'] == 'restate_claim' and not c.get('ruling', '').strip():
            raise ValueError('restate_claim writes reader-visible text: it needs the owner ruling')
        args = {k: v for k, v in c.items() if k not in ('op', 'ruling')}
        done.append({**c, 'result': OPERATIONS[c['op']](candidate, **args)})
    return json.dumps(candidate, ensure_ascii=False, indent=1).encode(), done


def verify(candidate_raw, receipt):
    """Prove the postimage comes from this original and this correction spec, without model calls."""
    if receipt.get('corrected_sha256') != sha(candidate_raw):
        raise ValueError('corrections receipt does not bind this candidate file')
    try:
        raw = Path(receipt['candidate']).read_bytes()
        spec_raw = Path(receipt['corrections']).read_bytes()
    except (KeyError, OSError) as exc:
        raise ValueError('corrections receipt needs its saved original and correction spec') from exc
    if sha(raw) != receipt.get('candidate_sha256') or sha(spec_raw) != receipt.get('corrections_sha256'):
        raise ValueError('corrections receipt does not bind its original candidate and correction spec')
    expected, done = corrected(json.loads(raw), json.loads(spec_raw))
    if expected != candidate_raw or done != receipt.get('applied'):
        raise ValueError('candidate or corrections receipt differs from the recorded corrections')
    return raw


def apply(candidate_path, corrections_path, out_path):
    candidate_path, corrections_path, out_path = (Path(p).resolve() for p in (candidate_path, corrections_path, out_path))
    raw = candidate_path.read_bytes()
    spec_raw = corrections_path.read_bytes()
    out, done = corrected(json.loads(raw), json.loads(spec_raw))
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
