"""Automatic, read-only provenance tracing before escalating evidence holds.

A Claim pass is not an EvidenceStep eligibility release. This diagnostic never
grants citation authority, rewrites historical answers, or resumes production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, _normalize_records, record_content_sha,
)
from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from backend.pipeline.viewpoint_deferred_source_check import build_packet


def bound_file(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError('historical input missing or not absolute')
    raw = path.read_bytes()
    return json.loads(raw), {'path': str(path.resolve()),
                            'file_sha256': hashlib.sha256(raw).hexdigest()}


def read_history(store, evidence_id):
    # One stable, read-only transaction. No model, inferred approval or mutation.
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cursor.execute('''SELECT payload,revision,content_sha256,retired_at IS NOT NULL
            FROM wang_knowledge.objects WHERE collection='evidence_steps' AND object_id=%s''',
            (evidence_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError('held evidence missing')
        current = dict(zip(('payload', 'revision', 'content_sha256', 'retired'), row))
        cursor.execute('''SELECT v.revision,v.content_sha256,v.payload,v.change_set_id,
            c.source_kind,c.source_sha256,c.status,c.metadata
            FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id)
            WHERE v.collection='evidence_steps' AND v.object_id=%s ORDER BY v.revision''',
            (evidence_id,))
        keys = ('revision', 'content_sha256', 'payload', 'change_set_id', 'source_kind',
                'source_sha256', 'status', 'metadata')
        versions = [dict(zip(keys, row)) for row in cursor.fetchall()]
        cursor.execute('''SELECT review_event_id,object_revision,reviewer_kind,reviewer_id,
            decision,reason,artifact FROM wang_knowledge.review_events
            WHERE collection='evidence_steps' AND object_id=%s ORDER BY created_at,review_event_id''',
            (evidence_id,))
        keys = ('review_event_id', 'object_revision', 'reviewer_kind', 'reviewer_id',
                'decision', 'reason', 'artifact')
        events = [dict(zip(keys, row)) for row in cursor.fetchall()]
    return current, versions, events


def trace_evidence(evidence_id, claim_id, current, versions, events):
    if current['retired'] or not versions:
        raise ValueError('retired evidence or missing applied history')
    if [v['revision'] for v in versions] != list(range(1, current['revision'] + 1)):
        raise ValueError('missing or ambiguous evidence version history')
    for version in versions:
        if version['status'] != 'applied' or record_content_sha(version['payload']) != version['content_sha256']:
            raise ValueError('unapplied or altered evidence history')
    if (versions[-1]['payload'] != current['payload']
            or versions[-1]['content_sha256'] != current['content_sha256']):
        raise ValueError('current evidence differs from latest applied version')
    if current['payload']['evidence_step_id'] != evidence_id:
        raise ValueError('foreign evidence identity')
    if claim_id not in current['payload'].get('produced_claim_ids', []):
        raise ValueError('foreign Claim membership')
    origins = []
    related_reviews = {}
    for version in versions:
        input_path = version.get('metadata', {}).get('input_path')
        if not input_path:
            origins.append({'revision': version['revision'], 'change_set_id': version['change_set_id'],
                            'status': 'missing_physical_input'})
            continue
        package, package_binding = bound_file(input_path)
        if sha256_json(package) != version['source_sha256']:
            raise ValueError('applied input package SHA mismatch')
        normalized, _ = _normalize_records(package)
        raw = normalized.get('evidence_steps', {}).get(evidence_id)
        if raw is None:
            raise ValueError('applied package has no exact evidence')
        prior = EvidenceStepRecord.model_validate(raw).model_dump(mode='json')
        if record_content_sha(prior) != version['content_sha256']:
            raise ValueError('applied package evidence differs from recorded version')
        origins.append({'revision': version['revision'], 'change_set_id': version['change_set_id'],
            'source_kind': version['source_kind'], 'source_sha256': version['source_sha256'],
            'input_binding': package_binding, 'status': 'exact_applied_input_verified',
            'support_eligibility': prior['support_eligibility'], 'review_status': prior['review_status']})
        # Discovery only within the batch named by the actual applied input.
        # A physically bound Claim review is reported, never treated as evidence approval.
        review_root = Path(input_path).parent.parent / 'reviews'
        for path in sorted(review_root.rglob('*.independent-review.json')):
            review, review_binding = bound_file(path)
            rows = [r for r in review.get('claim_reviews', []) if r.get('claim_id') == claim_id]
            if not rows:
                continue
            if len(rows) != 1:
                raise ValueError('ambiguous original Claim review')
            source = review.get('source', {})
            reviewed_input, input_binding = bound_file(source.get('package_path', ''))
            if input_binding['file_sha256'] != source.get('package_sha256'):
                related_reviews.setdefault(review_binding['file_sha256'], {
                    'review_binding': review_binding, 'input_binding': input_binding,
                    'expected_input_sha256': source.get('package_sha256'),
                    'claim_review': rows[0], 'reviewer': review.get('reviewer', {}),
                    'evidence_eligibility_release_proven': False,
                    'historical_claim_review_qualification': 'unverified_input_physical_sha_mismatch'})
                continue
            records, _ = _normalize_records(reviewed_input)
            step = records.get('evidence_steps', {}).get(evidence_id)
            if step is None:
                continue
            related_reviews.setdefault(review_binding['file_sha256'], {
                'review_binding': review_binding, 'input_binding': input_binding,
                'reviewer': review.get('reviewer', {}), 'claim_review': rows[0],
                'input_evidence': step, 'evidence_eligibility_release_proven': False,
                'historical_claim_review_qualification': 'diagnostic_only_not_replayed'})
    held = current['payload'].get('support_eligibility') == 'withheld_unreviewed'
    # Never infer missing review from the absence of an event alone: legacy
    # reviews can live in physical packages. Report both authorities explicitly.
    status = ('held_in_applied_package_no_evidence_review_event'
              if held and not events and origins and
              all(r['status'] == 'exact_applied_input_verified' for r in origins)
              else 'requires_exact_evidence_review_lineage_validation')
    return {'evidence_step_id': evidence_id, 'claim_id': claim_id,
        'status': status, 'current': current, 'applied_history': versions,
        'applied_inputs': origins, 'evidence_review_events': events,
        'related_claim_reviews': list(related_reviews.values()),
        'eligibility_release_proven': False, 'automatic_release_allowed': False}


def run_trace(packet_path, disposition_path, output, store):
    packet, disposition = read_artifact(packet_path), read_artifact(disposition_path)
    # Reuse the source investigator's live Claim semantic pin, exact fragment
    # and physical source checks; diagnostics must not attach to stale inputs.
    verified_source = build_packet(packet, disposition, store)
    claims = {c['claim_id']: c for c in packet['claims']}
    traces = []
    for row in disposition['claims']:
        if not any(c['disposition'] == 'deferred' for c in row['components']):
            continue
        claim = claims[row['claim_id']]
        for evidence in claim['evidence']:
            if evidence.get('support_eligibility') != 'withheld_unreviewed':
                continue
            eid = evidence['evidence_step_id']
            current, versions, events = read_history(store, eid)
            if (current['payload']['statement'] != evidence['evidence_statement']
                    or current['payload']['support_eligibility'] != evidence['support_eligibility']):
                raise ValueError('held packet evidence statement or eligibility drift')
            traces.append(trace_evidence(eid, row['claim_id'], current, versions, events))
    body = {'schema_version': 'wang_evidence_hold_trace_v1',
        'input_packet_sha256': sha256_json(packet), 'input_disposition_sha256': sha256_json(disposition),
        'source_verification_sha256': verified_source['artifact_sha256'],
        'physical_sources': [i['source_binding'] for i in verified_source['items']],
        'traces': traces, 'model_calls': 0, 'master_data_mutations': 0,
        'eligibility_attestation_created': False, 'group_complete': False}
    report = body | {'artifact_sha256': sha256_json(body)}
    write_immutable(output / 'evidence-hold-trace.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('packet', 'disposition', 'output-root'):
        parser.add_argument('--' + key, required=True, type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    result = run_trace(args.packet, args.disposition, args.output_root, PostgresKnowledgeStore())
    print(json.dumps({'artifact_sha256': result['artifact_sha256'],
                      'traces': [{k: t[k] for k in ('evidence_step_id', 'status')}
                                 for t in result['traces']],
                      'model_calls': 0, 'master_data_mutations': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
