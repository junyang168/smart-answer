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


def diagrams_for(change_set_id: str, data_base_dir: Path, keys: dict | None = None) -> dict[str, dict]:
    """His diagrams linked to this lecture's approved Claims, by full Claim ID, for slide generation.

    Each diagram is refused if it changed since the owner confirmed it. An element keeps
    the records that carry it in this lecture; one with none is not taught here."""
    keys = keys or json.loads(KEYS.read_text(encoding='utf-8'))
    linked = {}
    for key in keys['keys']:
        for argument in key.get('arguments', []):
            if argument['change_set_id'] != change_set_id:
                continue
            check_diagrams({'keys': [key]}, data_base_dir)
            linked.setdefault(argument['claim_id'], []).append(
                {'key': key['key'], 'title': key['title'], 'path': key['diagram']['path'],
                 'sha256': key['diagram']['sha256'], 'elements': argument['elements']})
    return linked


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
