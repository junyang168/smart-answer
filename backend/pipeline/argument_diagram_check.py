"""Grade a lecture's argument layer against the professor's own diagrams (gold keys).

Owner 2026-11-17: his SVG diagrams from other sermons are gold keys, used to
validate and calibrate the argument layer. A diagram shows the core structure of
a point he teaches in many sermons, so one key checks every lecture that teaches
it, and it was drawn by him: no one tuned the prompts on it.

For each key item the program proposes one verdict from the lecture's records in
the knowledge instance and from his transcript:

- ``claim``: a Claim holds every term group of the item;
- ``evidence_only``: only an Evidence step, Observation or Position does;
- ``missed``: no record does, but one paragraph of his transcript does;
- ``not_in_lecture``: his transcript never says it here (not an error).

``contradicted`` cannot be found by terms. Claude reads every proposal against
the records and his words and rules (``rulings``); the grade reports the ruling
where there is one and marks the rest ``unruled``. Only ``missed`` and
``contradicted`` count as errors. Reads only; no model call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

KEYS = Path(__file__).parent / 'argument_golden' / 'diagrams' / 'keys.json'
ERRORS = {'missed', 'contradicted'}
VERDICTS = {'claim', 'evidence_only', 'not_in_lecture', 'missed', 'contradicted'}


def holds(text: str, terms: list[list[str]]) -> bool:
    """Every term group has at least one of its spellings in the text."""
    return bool(text) and all(any(word in text for word in group) for group in terms)


def propose(item: dict, records: dict[str, list[dict]], paragraphs: list[str]) -> dict:
    terms = item['terms']
    claims = [r for r in records.get('claims', []) if holds(r.get('statement', ''), terms)]
    if claims:
        return {'verdict': 'claim', 'records': [r['claim_id'] for r in claims]}
    lower = []
    for collection, key, field in (('evidence_steps', 'evidence_step_id', 'statement'),
                                   ('observations', 'observation_id', 'statement'),
                                   ('position_nodes', 'position_id', 'title')):
        lower += [r[key] for r in records.get(collection, []) if holds(r.get(field, ''), terms)]
    if lower:
        return {'verdict': 'evidence_only', 'records': lower}
    said = [p for p in paragraphs if holds(p, terms)]
    if said:
        return {'verdict': 'missed', 'records': [], 'his_words': [p[:300] for p in said[:3]]}
    return {'verdict': 'not_in_lecture', 'records': []}


def check_diagrams(keys: dict, data_base_dir: Path) -> None:
    """A key is only as good as the diagram it was read from: refuse if a diagram changed."""
    for key in keys['keys']:
        path = Path(data_base_dir) / key['diagram']['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != key['diagram']['sha256']:
            raise ValueError(f"diagram for key {key['key']} changed since the owner confirmed it: {path}")


def grade(keys: dict, records: dict[str, list[dict]], paragraphs: list[str], rulings: dict | None = None) -> dict:
    rulings = rulings or {}
    items = []
    for key in keys['keys']:
        for item in key['items']:
            proposal = propose(item, records, paragraphs)
            ruling = rulings.get(item['id'])
            if ruling and ruling['verdict'] not in VERDICTS:
                raise ValueError(f"{item['id']}: unknown verdict {ruling['verdict']}")
            items.append({'id': item['id'], 'key': key['key'], 'statement': item['statement'],
                          'proposal': proposal, 'ruling': ruling,
                          'verdict': ruling['verdict'] if ruling else 'unruled'})
    ruled = [i for i in items if i['ruling']]
    return {'items': items,
            'errors': [i['id'] for i in ruled if i['verdict'] in ERRORS],
            'unruled': [i['id'] for i in items if not i['ruling']],
            'counted': sum(i['verdict'] not in ('not_in_lecture', 'unruled') for i in items)}


def _pairs(text: str) -> set[str]:
    text = ''.join(ch for ch in text if ch.strip() and ch not in '，。、；：？！「」『』（）…')
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _carriers(package: dict) -> list[tuple[str, str | None, set[str]]]:
    """Every (record, Claim it serves, his words) in a package: steps through the
    Claims they produce, Observations through their supports relations, a Claim's
    own words through the Claim itself."""
    words = {f['fragment_id']: _pairs(f['verbatim_excerpt']) for f in package.get('source_fragments', [])}
    out = []
    for step in package.get('evidence_steps', []):
        pairs = set().union(*(words.get(f, set()) for f in step['source_fragment_ids']))
        for claim in step.get('produced_claim_ids') or [None]:
            out.append((step['evidence_step_id'], claim, pairs))
    claim_ids = {c['claim_id'] for c in package.get('claims', [])}
    for row in package.get('observations', []):
        # An Observation serves a Claim through a supports relation, not a field of its own.
        pairs = set().union(*(words.get(f, set()) for f in row['source_fragment_ids']))
        served = [r['to_id'] for r in package.get('knowledge_relations', [])
                  if r['from_id'] == row['observation_id'] and r['relation_type'] == 'supports' and r['to_id'] in claim_ids]
        for claim in served or [None]:
            out.append((row['observation_id'], claim, pairs))
    for claim in package.get('claims', []):
        for occurrence in claim.get('occurrences', []):
            for anchor in occurrence.get('anchors', []):
                if not anchor.get('evidence_id') and not anchor.get('observation_id') and anchor.get('source_fragment_id') in words:
                    out.append(('own:' + anchor['source_fragment_id'], claim['claim_id'], words[anchor['source_fragment_id']]))
    return out


def compare_regeneration(gold: dict, new_package: dict, change_set_id: str, keys: dict | None = None,
                         held: float = 0.6) -> list[dict]:
    """Owner 2026-11-17: the diagram with its approved argument is a complete gold key.
    For each diagram element, his words that carry it in the approved records must be
    held again by the regenerated records, under one Claim. ``gold`` and ``new_package``
    are records by collection. Reports only; Claude compares the statements and rules."""
    keys = keys or json.loads(KEYS.read_text(encoding='utf-8'))
    gold_words = {f['fragment_id']: f['verbatim_excerpt'] for f in gold.get('source_fragments', [])}
    gold_steps = {s['evidence_step_id']: s for s in gold.get('evidence_steps', [])}
    gold_steps |= {o['observation_id']: o for o in gold.get('observations', [])}
    carriers = _carriers(new_package)
    statements = {c['claim_id']: c.get('statement') or c.get('title', '') for c in new_package.get('claims', [])}
    report = []
    for key in keys['keys']:
        for argument in key.get('arguments', []):
            if argument['change_set_id'] != change_set_id:
                continue
            for element in argument['elements']:
                pieces = []
                for rid in element['records']:
                    fids = gold_steps[rid]['source_fragment_ids'] if rid in gold_steps else [rid]
                    for fid in fids:
                        want = _pairs(gold_words[fid])
                        scored = [(len(want & got) / len(want), rec, claim) for rec, claim, got in carriers if want]
                        top = max((x[0] for x in scored), default=0.0)
                        # A step that serves several Claims is held by each of them.
                        hits = [x for x in scored if x[0] == top and top >= held]
                        pieces.append({'gold_record': rid, 'gold_fragment': fid, 'held': round(top, 2),
                                       'new_record': hits[0][1] if hits else None,
                                       'new_claims': sorted({x[2] for x in hits if x[2]})})
                claims = sorted({c for p in pieces for c in p['new_claims']})
                report.append({'key': key['key'], 'gold_claim': argument['claim_id'], 'diagram_text': element['diagram_text'],
                               'item': element['item'], 'pieces': pieces,
                               'not_held': [p['gold_record'] for p in pieces if not p['new_record']],
                               'new_claims': {c: statements.get(c, '') for c in claims}})
    return report


def transcript_paragraphs(path: Path) -> list[str]:
    script = json.loads(Path(path).read_text(encoding='utf-8'))['script']
    return [p.get('text', '') for p in script if p.get('type') != 'subtitle']


def main():
    from dotenv import load_dotenv
    import psycopg
    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.argument_review import database_url
    from backend.pipeline.chapter_slide_claims import instance_records
    p = argparse.ArgumentParser(description="Grade a lecture against the professor's diagrams")
    p.add_argument('--change-set', required=True, help="the lecture's ChangeSet in the knowledge instance")
    p.add_argument('--transcript', required=True, type=Path)
    p.add_argument('--rulings', type=Path, help="Claude's rulings: {item_id: {verdict, reason}}")
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    load_dotenv('.env')
    keys = json.loads(KEYS.read_text(encoding='utf-8'))
    check_diagrams(keys, Path(DATA_BASE_PATH))
    with psycopg.connect(database_url()) as conn:
        records, _ = instance_records(conn, a.change_set)
    rulings = json.loads(a.rulings.read_text(encoding='utf-8')) if a.rulings else None
    result = {'change_set_id': a.change_set, 'transcript': str(a.transcript),
              **grade(keys, records, transcript_paragraphs(a.transcript), rulings)}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
