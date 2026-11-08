"""Bounded same-sermon source lookup. Candidates are not semantic approval."""
from copy import deepcopy
from pathlib import Path
import json

from backend.pipeline import claim_flash_common as f, evidence_refresh as e
from backend.pipeline import claim_flash_questions as q
from backend.pipeline.transcript_evidence_migration import literal_layout, script_layout

CONTRACT = 'claim_flash_source_lookup_v1'


class Locator:
    """Build normalization once per paragraph, never once per Evidence/paragraph pair."""
    def __init__(self, sources):
        if len(sources) != 1:
            raise ValueError('source lookup requires exactly one sermon')
        self.sid, self.source = next(iter(sources.items()))
        self.paragraphs = self.source['paragraphs']
        self.layouts = {pk: [(p['text'], list(range(len(p['text'])))),
                             literal_layout(p['text']), script_layout(p['text'])]
                        for pk, p in self.paragraphs.items()}

    def unique(self, quote):
        if not quote or not any(ch.isalnum() for ch in quote):
            return None
        for mode, needle in enumerate([quote, literal_layout(quote)[0], script_layout(quote)[0]]):
            if not needle:
                continue
            hits = []
            for pk, layouts in self.layouts.items():
                text, offsets = layouts[mode]
                start = text.find(needle)
                if start >= 0:
                    if text.find(needle, start+1) >= 0:
                        hits.extend([None, None])
                    else:
                        hits.append({'paragraph_key': pk, 'quote': self.paragraphs[pk]['text'][
                            offsets[start]:offsets[start+len(needle)-1]+1]})
            if len(hits) == 1:
                return hits[0]
            if hits:
                return None  # Do not select one of several literal occurrences.
        return None

    def prepare(self, key, records, sources):
        """Recover review material, not a source-migration verdict or content approval."""
        p = records[key]['payload']; ids = e.evidence_fragment_ids(p)
        rows = [records.get('source_fragments/'+fid) for fid in ids]
        if (not rows or any(not r or r.get('retired') for r in rows)
                or any(r['payload']['source_id'] != self.sid for r in rows)):
            raise ValueError('missing, retired or foreign source fragment; cannot infer its authority')
        valid = []; nearby = set(); exact = []; historical = []; semantic = False
        keys = list(self.paragraphs)
        for fid, row in zip(ids, rows):
            frag = row['payload']; quote = frag['verbatim_excerpt']
            historical.append(quote)
            if frag['paragraph_key'] in keys:
                n = keys.index(frag['paragraph_key']);nearby.update(keys[max(0,n-2):n+3])
            try:
                one = {**p, 'source_fragment_id': None, 'source_fragment_ids': [fid]}
                v = e.material({**one, 'evidence_step_id': key}, records, sources)
                valid.extend(v['fragments'])
                exact.append({'paragraph_key': frag['paragraph_key'], 'quote': quote})
            except (ValueError, KeyError):
                found = self.unique(quote)
                if found:
                    nearby.add(found['paragraph_key']);exact.append(found)
                else:
                    semantic = True
                # Deleted occurrences are never revived by a literal match elsewhere.
                if frag.get('anchor_state') == 'source_deleted':
                    semantic = True
        material = {'evidence_id': key, 'statement': p.get('statement', ''),
            'speaker': p.get('speaker'), 'stance': p.get('stance'), 'fragments': valid,
            'original_text_excerpts': [x['quote'] for x in valid],
            'historical_quotes': historical, 'binding_requires_repair': True,
            'nearby_paragraphs': [{'source_id': self.sid, 'paragraph_key': pk, **self.paragraphs[pk]}
                                  for pk in keys if pk in nearby]}
        return material, None if semantic else exact


def needs_lookup(answers):
    return (answers.get('record_fidelity') in {'unsupported', 'changed', 'contradicted', 'uncertain'}
            or any(k.startswith('component_') and v != 'supported' for k, v in answers.items()))


def request(paragraphs, queries, remaining, labels):
    return {'model': e.MODEL,
            'state': {'current_paragraphs': paragraphs, 'records': {k: queries[k] for k in labels}},
            'questions': {label: q.source_location(label, remaining[label]) for label in labels}}


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def lookup(jobs, sources, root, ports):
    """Shared paragraphs, batched questions, exhaustive distinct-choice continuation."""
    root = Path(root)
    if not jobs:
        return {}
    if len(sources) != 1:
        raise ValueError('source lookup cannot cross sermons')
    sid, source = next(iter(sources.items()))
    labels = {f'E{i:03}': job for i, job in enumerate(jobs)}
    queries = {}
    for label, job in labels.items():
        material = job['material']
        parts = job.get('components') or []
        answers = job.get('verdict', {})
        missing = [item for i, item in enumerate(parts) if answers.get('component_'+str(i)) != 'supported']
        queries[label] = {'statement': job['record'].get('statement', ''),
            'components_to_locate': missing,
            'historical_quotes': material.get('historical_quotes', [x['quote'] for x in material['fragments']])}
    paragraphs = {pk: p['text'] for pk, p in source['paragraphs'].items() if p.get('text', '').strip()}
    e.save(root/'input.json', {'contract': CONTRACT, 'source_id': sid,
        'source_sha256': source['body_sha256'], 'jobs': jobs, 'paragraphs': paragraphs})
    # Preflight every source block and every complete query before the first call.
    blocks = []; block = {}
    def fits(value):
        return len(value) <= 253 and all(size(request(value, queries, {k:list(value)}, [k])) <= f.JEV_BYTES for k in labels)
    for pk, text in paragraphs.items():
        trial = {**block, pk: text}
        if block and not fits(trial):
            blocks.append(block);block = {};trial = {pk:text}
        if not fits(trial):
            raise ValueError('complete source paragraph and lookup query exceed Jev limit; no call made')
        block = trial
    if block:
        blocks.append(block)
    found = {k: set() for k in labels};uncertain = {k: [] for k in labels};trace = []
    for block in blocks:
        remaining = {k: list(block) for k in labels}
        active = list(labels)
        while active:
            batches = []; batch = []
            for label in active:
                if batch and size(request(block, queries, remaining, batch+[label])) > f.JEV_BYTES:
                    batches.append(batch);batch=[]
                batch.append(label)
            if batch:
                batches.append(batch)
            next_active = []
            for batch in batches:
                req = request(block, queries, remaining, batch)
                answers = f.jev(root, ports, req['state'], req['questions'])
                for label, selected in answers.items():
                    trace.append({'object_key': labels[label]['job_id'], 'selected': selected,
                                  'request_sha256': e.digest(req)})
                    if selected == 'uncertain':
                        uncertain[label].append(list(remaining[label]))
                    elif selected != 'none':
                        found[label].add(selected);remaining[label].remove(selected)
                        if remaining[label]:
                            next_active.append(label)
            active = next_active
    results = {}
    for label, job in labels.items():
        paragraphs_found = [pk for pk in paragraphs if pk in found[label]]
        material = deepcopy(job['material'])
        existing = {p['paragraph_key']: p for p in material['nearby_paragraphs']}
        keys = list(source['paragraphs'])
        for pk in paragraphs_found:
            n = keys.index(pk)
            for nearby in keys[max(0,n-1):n+2]:
                existing[nearby] = {'source_id': sid, 'paragraph_key': nearby, **source['paragraphs'][nearby]}
        material['nearby_paragraphs'] = [existing[pk] for pk in keys if pk in existing]
        material['source_lookup'] = {'candidate_paragraph_keys': paragraphs_found,
            'uncertain_paragraph_blocks': uncertain[label], 'absence_is_not_proof': True,
            'source_sha256': source['body_sha256']}
        results[job['job_id']] = material
    e.save(root/'result.json', {'contract': CONTRACT, 'trace': trace, 'materials': results})
    return results
