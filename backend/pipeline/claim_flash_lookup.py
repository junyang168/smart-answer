"""Whole-sermon semantic lookup; shared state, independent short questions, no GPT."""
import json
from pathlib import Path

from backend.pipeline import claim_flash_common as f, claim_flash_pairs as pairs, evidence_refresh as e
from backend.pipeline import claim_flash_questions as jevq

CONTRACT = 'claim_flash_orphan_lookup_v2'


def request(claims, records, remaining, labels):
    questions = {}
    for label in labels:
        excluded = [c for c in claims if c not in remaining[label]]
        questions.update({label+'_'+name:value for name,value in
                          jevq.lookup(label,excluded,remaining[label]).items()})
    return {'model': e.MODEL, 'state': {'claims': claims,
        'records': {k: records[k] for k in labels}}, 'questions': questions}


def size(req):
    return len(json.dumps(req, ensure_ascii=False).encode())


def claim_chunks(claims, records):
    """Preflight every complete record before ANY call; never truncate its sources."""
    chunks = []; chunk = {}
    largest = max(records, key=lambda r: size(request({}, records, {r: []}, [r])))
    for label, value in claims.items():
        trial = {**chunk, label: value}
        def fits(selected):
            return len(selected) <= 254 and size(request(selected, records,
                {largest: list(selected)}, [largest])) <= f.JEV_BYTES
        if chunk and not fits(trial):
            chunks.append(chunk); chunk = {}; trial = {label: value}
        if not fits(trial):
            raise ValueError('one complete orphan lookup exceeds Jev payload limit; no call made')
        chunk = trial
    if chunk:
        chunks.append(chunk)
    return chunks


def batches(claims, records, remaining, active):
    result = []; batch = []
    for label in active:
        if batch and size(request(claims, records, remaining, batch+[label])) > f.JEV_BYTES:
            result.append(batch); batch = []
        batch.append(label)
        if size(request(claims, records, remaining, batch)) > f.JEV_BYTES:
            raise ValueError('one complete orphan lookup exceeds Jev payload limit')
    if batch:
        result.append(batch)
    return result


def run(records, sources, orphan_keys, claim_keys, root, ports):
    """Return candidates, not approval. Each pass removes a distinct choice or stops."""
    root = Path(root)
    claim_ids = {f'C{i:02}': k for i, k in enumerate(sorted(claim_keys))}
    orphan_ids = {f'O{i:02}': k for i, k in enumerate(sorted(orphan_keys))}
    claims = {label: {field: records[k]['payload'].get(field)
              for field in ['statement', 'attribution']} for label, k in claim_ids.items()}
    material = {label: pairs.node(k, records, sources) for label, k in orphan_ids.items()}
    sid = next(iter(sources))
    intent = {'contract': CONTRACT, 'source_id': sid, 'source_sha256': sources[sid]['body_sha256'],
              'model': e.MODEL, 'rules': jevq.LOOKUP_RULE, 'match_rule': jevq.MATCH_RULE,
              'uncertainty_floor': jevq.LOOKUP_UNCERTAINTY_FLOOR, 'claim_keys': claim_ids,
              'orphan_keys': orphan_ids, 'claims': claims, 'records': material}
    e.save(root/'lookup-input.json', intent)
    chunks = claim_chunks(claims, material) if material else []
    proposed = []; pending = []; dispositions = []
    # These are routing candidates only. Final pair checks use the actual postimage.
    for chunk in chunks:
        remaining = {label: list(chunk) for label in material}
        active = list(material)
        while active:
            next_active = []
            for labels in batches(chunk, material, remaining, active):
                req = request(chunk, material, remaining, labels)
                answer = f.jev(root, ports, req['state'], req['questions'])
                for label in labels:
                    probability, selected = answer[label+'_match'], answer[label+'_claim']
                    key = orphan_ids[label]
                    row = {'object_key': key, 'match_probability': probability,
                           'claim_key': claim_ids[selected] if selected != 'none' else None,
                           'remaining_claim_keys': [claim_ids[x] for x in remaining[label]],
                           'request_sha256': e.digest(req)}
                    dispositions.append(row)
                    if selected != 'none':
                        proposed.append({'a': key, 'b': claim_ids[selected],
                            'reason': 'Jev 語義查找候選，尚待最終配對處置。'})
                        remaining[label].remove(selected)
                        if remaining[label]:
                            next_active.append(label)
                    elif probability > jevq.LOOKUP_UNCERTAINTY_FLOOR:
                        # Choice cannot return a list. An uncertain stop is not proof
                        # that every remaining Claim is unrelated; retain that scope.
                        pending.append({'object_key': key, 'reason': 'uncertain_orphan_lookup',
                            'remaining_claim_keys': row['remaining_claim_keys'],
                            'match_probability': probability})
            active = next_active
    if not claims:
        pending = [{'object_key': k, 'reason': 'no_existing_claim_for_orphan'} for k in orphan_keys]
    return {'source_id': sid, 'source_sha256': sources[sid]['body_sha256'],
            'lookup_contract': CONTRACT, 'orphan_keys': sorted(orphan_keys), 'pairs': proposed,
            'pending': pending, 'dispositions': dispositions,
            'input_sha256': e.digest(intent), 'reused_request_sha256': []}
