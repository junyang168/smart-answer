"""Gate for an editorial diagram: every box held by a Claim (#812, solution doc §6).

An editorial diagram (C: 登山寶訓的文學結構) is drawn by an editor from his sermons
and the 母本. It is not his source, so it cannot be a SourceFragment; it cites
Claims instead. Its record lists each box of the SVG, the Claims (with revision)
that say it, and Claude's ruling that the box does not go against them.

The gate, with no model call:

- ``block``  -- the SVG changed since the record, a box has no Claim, a box of the
  SVG is not in the record (or the reverse), a Claim is gone, retired or no longer
  in the lecture version that holds its lecture, a box is unruled, or a ruling says
  the box goes against its Claim;
- ``flag_recheck`` -- nothing blocks, but a linked Claim has a newer revision than
  the one ruled on: shown, labelled 待重核, until Claude rules again;
- ``pass``.

Whether a box agrees with its Claim is a reading judgement (§6.2 rule 2); the
program does not guess it from words. It binds the ruling to the box text and the
Claim revisions, so changing either one undoes the ruling.

"Approved" (§6.2 rule 1): the store has no per-Claim approval yet; every Claim of
the six lectures is ``candidate``. Until it does, a Claim counts when it is live in
the lecture version that currently holds its lecture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.pipeline.svg_diagram import read

RECORDS = Path(__file__).parent / 'argument_golden' / 'editorial'
VERDICTS = {'holds', 'contradicts'}
DEAD = ('superseded', 'retired', 'rejected')


def gate(record: dict, svg: bytes, current) -> dict:
    """``svg`` is the file's bytes; ``current(claim_id)`` -> {'revision', 'live', 'lecture_held'} or None."""
    block, recheck = [], []
    if hashlib.sha256(svg).hexdigest() != record['svg']['sha256']:
        block.append('the SVG changed since this record was made')
    drawn = Counter(read(svg.decode('utf-8'))['texts'])
    listed = Counter(box['text'] for box in record['boxes'])
    for text in sorted((drawn - listed).elements()):
        block.append(f'box 「{text}」 is drawn but not in the record (no Claim holds it)')
    for text in sorted((listed - drawn).elements()):
        block.append(f'box 「{text}」 is in the record but not drawn')
    for box in record['boxes']:
        name = f"「{box['text']}」"
        if not box.get('claims'):
            block.append(f'{name} has no Claim')
        for link in box.get('claims', []):
            now = current(link['claim_id'])
            if now is None:
                block.append(f"{name}: {link['claim_id']} is not in the store")
            elif not now['live']:
                block.append(f"{name}: {link['claim_id']} is retired or rejected")
            elif not now['lecture_held']:
                block.append(f"{name}: {link['claim_id']} is not in the lecture version that holds its lecture")
            elif now['revision'] != link['revision']:
                recheck.append(f"{name}: {link['claim_id']} is at revision {now['revision']}, ruled on {link['revision']}")
        ruling = box.get('ruling')
        if not ruling or ruling.get('verdict') not in VERDICTS or not str(ruling.get('reason', '')).strip():
            block.append(f'{name} has no ruling')
        elif ruling['verdict'] == 'contradicts':
            block.append(f"{name} goes against its Claim: {ruling['reason']}")
    status = 'block' if block else 'flag_recheck' if recheck else 'pass'
    return {'status': status, 'block': block, 'recheck': recheck}


def store_lookup(conn):
    """Current state of a Claim in the knowledge instance; primary-key and indexed reads only."""
    def current(claim_id: str):
        row = conn.execute("""SELECT revision, review_status, retired_at FROM wang_knowledge.objects
            WHERE collection='claims' AND object_id=%s""", (claim_id,)).fetchone()
        if not row:
            return None
        revision, status, retired = row
        change_set = conn.execute("""SELECT change_set_id FROM wang_knowledge.object_versions
            WHERE collection='claims' AND object_id=%s AND revision=%s""", (claim_id, revision)).fetchone()
        held = change_set and conn.execute("""SELECT 1 FROM wang_knowledge.object_versions v
            JOIN wang_knowledge.objects o USING (collection, object_id, revision)
            WHERE v.change_set_id=%s AND v.collection='source_documents' AND o.retired_at IS NULL""",
            (change_set[0],)).fetchone()
        return {'revision': revision, 'live': retired is None and status not in DEAD, 'lecture_held': bool(held)}
    return current


def main():
    from dotenv import load_dotenv
    import psycopg
    from backend.pipeline.argument_review import database_url
    p = argparse.ArgumentParser(description='Gate an editorial diagram against the Claims it cites')
    p.add_argument('--record', type=Path, required=True)
    p.add_argument('--data-base-dir', type=Path, required=True)
    a = p.parse_args()
    load_dotenv('.env')
    record = json.loads(a.record.read_text(encoding='utf-8'))
    svg = (a.data_base_dir / record['svg']['path']).read_bytes()
    with psycopg.connect(database_url()) as conn:
        result = gate(record, svg, store_lookup(conn))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    raise SystemExit(0 if result['status'] != 'block' else 1)


if __name__ == '__main__':
    main()
