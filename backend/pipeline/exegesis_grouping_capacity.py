"""Read-only L0 whole-book packet capacity experiment; never authorizes grouping.

Candidate book associations are disclosed, not promoted to primary ownership.
No model calls, summaries or master-data mutations; scoped context is explicitly disclosed.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from backend.pipeline.exegesis_intelligent_grouping_job import checked, payload_for, unit_schema, PROMPTS, verify_files
from backend.pipeline.exegesis_grouping_packet import unpack
from backend.pipeline.exegesis_grouping_transport import serialize_request, write_new


def measure(preparation, sources, root, executable, max_bytes):
    verify_files(sources)
    books = defaultdict(list)
    unassigned = []
    for row in preparation['rows']:
        keys = [row['primary']] if row['primary'] else row['role_decision'].get('interpreted_passage_keys', [])
        if not keys: unassigned.append(row['claim']['claim_id'])
        for book in {key.split('.')[0] for key in keys}:
            # Preparation history is not runtime Claim content; preserve bindings externally.
            claim = dict(row['claim'], ownership=dict(primary=row['primary'], secondary=row['secondary_relations'] or [],
                preparation_status=row['preparation_status'], candidate_passage_keys=keys))
            books[book].append(claim)
    root.mkdir(parents=True, exist_ok=False)
    results = []
    for book, claims in sorted(books.items()):
        payload = payload_for(claims, sources, scope_label=book)
        request = serialize_request(provider='gpt', executable=executable, model='gpt-6-sol', effort='high',
            prompt=(PROMPTS / 'exegesis_passage_unit_planning.md').read_text(), payload=payload,
            schema=unit_schema(), directory=root / book)
        if unpack(request['wire_payload']) != payload:
            raise ValueError('capacity packet lost material')
        original = len(json.dumps(payload, ensure_ascii=False, indent=2).encode())
        results.append(dict(book=book, candidate_claim_count=len(claims), uncompressed_payload_bytes=original,
            compressed_payload_bytes=len(request['body'].encode()), complete_request_bytes=request['size'],
            limit=max_bytes, exceeds_limit=request['size'] > max_bytes, lossless_roundtrip=True))
    return write_new(root / 'report.json', dict(schema_version='wang_exegesis_book_packet_capacity_v1',
        preparation_sha256=preparation['artifact_sha256'], model='gpt-6-sol', executable=executable,
        grouping_authorized=False, candidate_associations_not_reviewed_primary=True, books=results,
        unassigned_claim_ids=unassigned, model_calls=0, svg_excluded_from_model_input=True, source_context_policy='evidence_anchors_plus_two_physical_neighbors_v1', complete_source_sent=False,
        total_exegesis=3843, explanation='Exact GPT generation request bytes; reviewer/proposal capacity must also be checked before execution. Byte fit alone does not establish model token capacity.'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'sources', 'output-root'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--executable', required=True)
    p.add_argument('--max-request-bytes', type=int, default=500000)
    args = p.parse_args()
    report = measure(checked(args.preparation), checked(args.sources)['sources'], args.output_root,
                     args.executable, args.max_request_bytes)
    print(json.dumps({'books': len(report['books']), 'over_limit': sum(r['exceeds_limit'] for r in report['books']),
                      'matthew': next(r for r in report['books'] if r['book'] == 'Matt')}, ensure_ascii=False))


if __name__ == '__main__': main()
