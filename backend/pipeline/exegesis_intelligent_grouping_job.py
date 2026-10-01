"""#411 read-only full scheduling: reviewed ownership -> passage units -> grouping.

No preview authorization, master-data writes, CVP calls or automatic retries.
Run `--help`; input format is documented in the companion README.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import fcntl
import hashlib
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
import subprocess
import sys

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.exegesis_grouping_transport import call, write_new
from backend.pipeline.viewpoint_passage_grouping_preflight import passage_sort_key, plan_reviewed_passage_unit
from backend.pipeline.viewpoint_passage_grouping_sample_runner import split_reviewed_unit

PROMPTS = Path(__file__).parent / 'prompts'
REVIEWER = Path(__file__).resolve().parents[2] / 'scripts' / 'review-exegesis-grouping.py'
FORMAL_LEDGER = '1cf9679bb47ad34a78476bff12ba09c5803db28c356f2990f81ac8a8da948747'
FORMAL_PACKET = '7002b3956469527924d9f94c53a04f5608c2d952696313e1606cd59c6b8d4474'


def checked(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('artifact_sha256') != sha256_json({k: v for k, v in value.items() if k != 'artifact_sha256'}):
        raise ValueError(f'artifact SHA mismatch: {path}')
    return value


def exact(ids, expected, label):
    counts = Counter(ids)
    missing, duplicate, foreign = set(expected) - set(ids), {i for i, n in counts.items() if n > 1}, set(ids) - set(expected)
    if missing or duplicate or foreign:
        raise ValueError(f'{label}: missing={sorted(missing)}, duplicate={sorted(duplicate)}, foreign={sorted(foreign)}')


def verify_files(sources):
    if len({s['source_id'] for s in sources}) != len(sources):
        raise ValueError('duplicate physical source mapping')
    for source in sources:
        for file in [source, *source.get('linked_files', [])]:
            if hashlib.sha256(Path(file['path']).read_bytes()).hexdigest() != file['file_sha256']:
                raise ValueError(f"physical source drift: {file['path']}")


def verify_current(claims):
    from dotenv import load_dotenv
    from backend.pipeline.claim_passage_role_runner import build_rows, PostgresKnowledgeStore
    load_dotenv('.env')
    pins = [dict(claim_id=c['claim_id'], source_id=c['source_id'], statement=c['statement'],
                 scripture_refs=c['scripture_refs'], pinned_claim_revision=c['claim_revision'],
                 claim_revision_sha256=c['claim_content_sha256']) for c in claims]
    rows = build_rows(PostgresKnowledgeStore(), pins)
    frozen = {c['claim_id']: c for c in claims}
    exact([r['claim_id'] for r in rows], frozen, 'current graph')
    if any(r != frozen[r['claim_id']] for r in rows):
        raise ValueError('current Claim/source/evidence/fragment graph drift')


def load_inputs(ledger, packet, ownership):
    if ledger.get('schema_version') != 'wang_claim_passage_role_ledger_v8' or ledger.get('status') != 'all_eligible_reviewed_with_user_approved_context_overlay':
        raise ValueError('formal v8 role ledger required')
    if ledger['artifact_sha256'] != FORMAL_LEDGER or packet['artifact_sha256'] != FORMAL_PACKET:
        raise ValueError('unexpected #409 formal input SHA')
    if ledger['packet_sha256'] != packet['artifact_sha256']:
        raise ValueError('role ledger/packet binding mismatch')
    if ownership.get('schema_version') != 'wang_exegesis_reviewed_ownership_v1':
        raise ValueError('reviewed ownership artifact required; candidates/preview not authorized')
    if ownership['role_ledger_sha256'] != ledger['artifact_sha256'] or ownership['role_packet_sha256'] != packet['artifact_sha256']:
        raise ValueError('ownership belongs to a different freeze')
    frozen = {c['claim_id']: c for c in packet['claims']}
    exact([c['claim_id'] for c in packet['claims']], frozen, 'packet')
    exact([d['claim_id'] for d in ledger['decisions']], frozen, 'role ledger')
    eligible = {d['claim_id'] for d in ledger['decisions'] if d['role'] == 'passage_exegesis'}
    if len(eligible) != 3843:
        raise ValueError('formal exegesis denominator must be 3843')
    exact([r['claim_id'] for r in ownership['decisions']], eligible, 'ownership denominator')
    reviews = {}
    for reference in ownership['review_artifacts']:
        artifact = checked(reference['path'])
        if artifact['artifact_sha256'] != reference['artifact_sha256']:
            raise ValueError('ownership review artifact binding drift')
        reviews[artifact['artifact_sha256']] = artifact
    sources = ownership['sources']
    verify_files(sources)
    source_index = {s['source_id']: s for s in sources}
    ready, unresolved = [], []
    for row in ownership['decisions']:
        claim = frozen[row['claim_id']]
        for field in ('claim_revision', 'claim_content_sha256', 'source_id', 'source_revision', 'source_content_sha256'):
            if row.get(field) != claim[field]:
                raise ValueError(f"ownership revision/SHA drift: {row['claim_id']} {field}")
        if 'secondary' not in row or not isinstance(row['secondary'], list):
            raise ValueError('secondary/cross-passage relations must be preserved explicitly')
        if row['status'] == 'unresolved':
            if not row.get('missing') or row.get('primary'):
                raise ValueError('unresolved ownership needs specific missing evidence and no primary')
            unresolved.append(row)
            continue
        if row['status'] != 'reviewed' or row.get('approval_basis') not in {'dual_model_consensus', 'human_exception_review'}:
            raise ValueError('candidate-only ownership cannot authorize grouping')
        if not row.get('review_artifact_sha256') or not row.get('reason') or not row.get('evidence'):
            raise ValueError('reviewed ownership lacks bound review/evidence/reason')
        if row['review_artifact_sha256'] not in reviews:
            raise ValueError('ownership approval reference is not a verified artifact')
        approved = reviews[row['review_artifact_sha256']].get('decisions', [])
        matches = [d for d in approved if d.get('claim_id') == row['claim_id']]
        if len(matches) != 1 or any(matches[0].get(k) != v for k, v in row.items() if k != 'review_artifact_sha256'):
            raise ValueError('ownership not bound to the actual approved decision')
        passage_sort_key(row['primary'])  # chapter-only and cross-chapter accepted
        if claim['source_id'] not in source_index:
            raise ValueError('reviewed Claim lacks physical source')
        source = source_index[claim['source_id']]
        if source.get('source_content_sha256') != claim['source_content_sha256']:
            raise ValueError('source/body binding mismatch')
        ready.append({**claim, 'ownership': row})
    return ready, unresolved, sources


class BoundaryEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class PassageUnit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    unit_id: str = Field(pattern='^[a-z0-9_]+$')
    passage_key: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence: list[BoundaryEvidence] = Field(min_length=1)


class PassageUnits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    units: list[PassageUnit] = Field(min_length=1)


def unit_schema():
    from backend.api.canonical_repository.viewpoint_resolution import _strict_json_schema
    return _strict_json_schema(PassageUnits.model_json_schema())


def validate_units(response, claims):
    PassageUnits.model_validate(response)
    ids = [c['claim_id'] for c in claims]
    exact([cid for u in response['units'] for cid in u['claim_ids']], ids, 'complete passage membership')
    exact([u['unit_id'] for u in response['units']], {u['unit_id'] for u in response['units']}, 'unit IDs')
    for unit in response['units']:
        key = passage_sort_key(unit['passage_key'])
        if key[:3] > key[3:6]:
            raise ValueError('reversed passage range')


def payload_for(claims, sources, **extra):
    needed = {c['source_id'] for c in claims}
    selected = [s for s in sources if s['source_id'] in needed]
    # Entire original files, not just excerpts or transport windows.
    full = [{**s, 'original_text': Path(s['path']).read_text(encoding='utf-8'),
        'linked_files': [{**f, 'original_text': Path(f['path']).read_text(encoding='utf-8')} for f in s.get('linked_files', [])]}
        for s in selected]
    return {'claims': claims, 'sources': full, **extra}


def obtain(directory, fingerprint, generate):
    target = directory / 'validated.json'
    if target.exists():
        cached = checked(target)
        if cached['fingerprint'] != fingerprint:
            raise ValueError(f'cached input/model/prompt/schema/code changed: {directory}')
        return cached['response']
    if directory.exists():
        raise ValueError(f'incomplete/failed attempt retained; no automatic retry: {directory}')
    try:
        response = generate()
        write_new(target, dict(fingerprint=fingerprint, response=response))
        return response
    except Exception as exc:
        directory.mkdir(parents=True, exist_ok=True)
        if not (directory / 'validation-failure.json').exists():
            write_new(directory / 'validation-failure.json', dict(fingerprint=fingerprint, error=str(exc), error_type=type(exc).__name__))
        raise


def independent_review(payload, proposal, directory, args, binding):
    request = dict(payload=payload, proposal=proposal, binding=binding,
        proposer_provider=args.provider, reviewer_provider=args.reviewer_provider, reviewer_model=args.reviewer_model,
        max_request_bytes=args.max_request_bytes, effort=args.effort)
    fingerprint = sha256_json(request | {'reviewer_code_sha256': hashlib.sha256(REVIEWER.read_bytes()).hexdigest()})
    def execute():
        directory.mkdir(parents=True, exist_ok=False)
        write_new(directory / 'input.json', request)
        completed = subprocess.run([sys.executable, str(REVIEWER), '--input', str(directory / 'input.json'),
                                   '--output-dir', str(directory / 'call')], capture_output=True, text=True, check=False)
        write_new(directory / 'controller.raw.json', {'stdout': completed.stdout, 'stderr': completed.stderr, 'returncode': completed.returncode})
        if completed.returncode:
            raise ValueError(f'independent semantic review failed; see {directory}')
        report = checked(directory / 'call' / 'report.json')
        if report['binding'] != binding or report['status'] != 'pass':
            raise ValueError('independent semantic review did not pass')
        return report
    return obtain(directory, fingerprint, execute)


def execute(args):
    if args.provider == args.reviewer_provider:
        raise ValueError('independent semantic review must use another vendor')
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / '.grouping.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not (root / 'config.json').exists() and any(p.name != '.grouping.lock' for p in root.iterdir()):
            raise ValueError('refusing to adopt an existing unrelated output root')
        ledger, packet, ownership = checked(args.role_ledger), checked(args.role_packet), checked(args.ownership)
        claims, unresolved, sources = load_inputs(ledger, packet, ownership)
        # Read-only current graph + physical files checked on every start/resume.
        owned_ids = {r['claim_id'] for r in ownership['decisions']}
        verify_current([c for c in packet['claims'] if c['claim_id'] in owned_ids])
        code_paths = [Path(__file__), REVIEWER, Path(__file__).with_name('exegesis_grouping_transport.py'),
                      Path(__file__).with_name('viewpoint_passage_grouping_sample_runner.py'),
                      Path(__file__).with_name('viewpoint_passage_grouping_preflight.py')]
        config = dict(role_ledger_sha256=ledger['artifact_sha256'], role_packet_sha256=packet['artifact_sha256'],
            ownership_sha256=ownership['artifact_sha256'], provider=args.provider, model=args.model,
            reviewer_provider=args.reviewer_provider, reviewer_model=args.reviewer_model, effort=args.effort,
            max_request_bytes=args.max_request_bytes, max_group_size=20,
            code_shas={str(p.relative_to(REVIEWER.parent.parent)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
            prompt_shas={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [PROMPTS / 'exegesis_passage_unit_planning.md',
                PROMPTS / 'exegesis_argument_grouping.md', PROMPTS / 'exegesis_matthew_16_19_regression.md']})
        if (root / 'config.json').exists():
            old = checked(root / 'config.json')
            if {k: v for k, v in old.items() if k != 'artifact_sha256'} != config:
                raise ValueError('resume configuration/input/code drift; use a new output root')
        else:
            write_new(root / 'config.json', config)
        if not (root / 'preflight.json').exists():
            write_new(root / 'preflight.json', dict(config_sha256=checked(root / 'config.json')['artifact_sha256'],
                current_graph_verified_claim_count=3843, physical_sources_verified=len(sources),
                reviewed_count=len(claims), unresolved_count=len(unresolved),
                other_excluded=8743, deferred_excluded=170, master_data_mutations=0))
            write_new(root / 'unresolved.json', dict(schema_version='wang_exegesis_unresolved_v1',
                ownership_sha256=ownership['artifact_sha256'], decisions=unresolved))
        book_claims = defaultdict(list)
        for c in claims:
            book_claims[c['ownership']['primary'].split('.')[0]].append(c)
        units, groups, reviews = [], [], []
        for book in sorted(book_claims, key=lambda b: passage_sort_key(b + '.1')):
            scope = book_claims[book]
            payload = payload_for(scope, sources, scope_label=book)
            prompt = (PROMPTS / 'exegesis_passage_unit_planning.md').read_text()
            fingerprint = sha256_json(dict(config=config, payload=payload, schema=unit_schema(), phase='units'))
            directory = root / 'units' / book
            def plan():
                answer = call(provider=args.provider, model=args.model, effort=args.effort, prompt=prompt,
                    payload=payload, schema=unit_schema(), directory=directory, max_bytes=args.max_request_bytes)
                validate_units(answer, scope)
                return answer
            proposal = obtain(directory, fingerprint, plan)
            validate_units(proposal, scope)
            binding = sha256_json(dict(payload=payload, proposal=proposal, config=config))
            review = independent_review(payload, proposal, root / 'unit-reviews' / book, args, binding)
            reviews.append(review)
            units.extend(proposal['units'])
        index = {c['claim_id']: c for c in claims}
        units.sort(key=lambda u: (*passage_sort_key(u['passage_key']), u['unit_id']))
        exact([cid for u in units for cid in u['claim_ids']], index, 'global unit membership')
        # Stable unique storage keys even if separate books use identical model slugs.
        for number, unit in enumerate(units):
            unit['storage_key'] = f'{number:05d}_{unit["unit_id"]}'
            members = [index[cid] for cid in unit['claim_ids']]
            payload = payload_for(members, sources, scope_label=unit['storage_key'], reviewed_unit=unit)
            directory = root / 'groups' / unit['storage_key']
            fingerprint = sha256_json(dict(config=config, payload=payload, phase='groups'))
            def group():
                if len(members) <= 20:
                    directory.mkdir(parents=True, exist_ok=False)
                    result = plan_reviewed_passage_unit(unit_id=unit['storage_key'], claim_ids=unit['claim_ids'], batch_size=20)
                else:
                    result = split_reviewed_unit(unit_id=unit['storage_key'], payload=payload,
                        provider=args.provider, model=args.model, effort=args.effort, directory=directory,
                        max_request_bytes=args.max_request_bytes,
                        regression_context=unit['passage_key'] == 'Matt.16.19')
                return result.model_dump(mode='json')
            answer = obtain(directory, fingerprint, group)
            from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
            expected_grouping = plan_reviewed_passage_unit(unit_id=unit['storage_key'], claim_ids=unit['claim_ids'], batch_size=20,
                model_split=ClaimGroupingResponse.model_validate(answer) if len(members) > 20 else None)
            if len(members) <= 20 and answer != expected_grouping.model_dump(mode='json'):
                raise ValueError('small complete unit must remain exactly one deterministic group')
            exact([cid for g in answer['groups'] for cid in g['claim_ids']], unit['claim_ids'], 'retained grouping')
            if any(len(g['claim_ids']) > 20 for g in answer['groups']):
                raise ValueError('retained group exceeds 20')
            binding = sha256_json(dict(payload=payload, proposal=answer, config=config))
            review = independent_review(payload, answer, root / 'group-reviews' / unit['storage_key'], args, binding)
            reviews.append(review)
            groups.extend([{**g, 'unit_storage_key': unit['storage_key']} for g in answer['groups']])
        exact([cid for g in groups for cid in g['claim_ids']], index, 'eligible group membership')
        result = dict(schema_version='wang_exegesis_grouping_manifest_v1', status='complete' if not unresolved else 'partial_with_explicit_unresolved',
            config_sha256=checked(root / 'config.json')['artifact_sha256'], units=units, groups=groups,
            claim_packets=claims, sources=sources, unresolved=unresolved, deferred_excluded=170, other_excluded=8743,
            total_exegesis=3843, eligible_count=len(claims), grouped_count=len(index), unresolved_count=len(unresolved),
            missing=0, duplicate=0, foreign=0, max_group_size=20, master_data_mutations=0, cvp_generated=0,
            independent_review_shas=[r['artifact_sha256'] for r in reviews])
        if not (root / 'manifest.json').exists():
            write_new(root / 'manifest.json', result)
        elif {k: v for k, v in checked(root / 'manifest.json').items() if k != 'artifact_sha256'} != result:
            raise ValueError('existing manifest differs; refusing overwrite')
        if not (root / 'validation-report.json').exists():
            write_new(root / 'validation-report.json', dict(manifest_sha256=checked(root / 'manifest.json')['artifact_sha256'],
                eligible_count=len(claims), unresolved_count=len(unresolved), total_exegesis=3843,
                missing=0, duplicate=0, foreign=0, group_ceiling_pass=True, provenance_preserved=True,
                semantic_review_status='pass', independent_review_shas=result['independent_review_shas']))
        print(json.dumps({k: v for k, v in result.items() if k not in {'units', 'groups', 'claim_packets', 'sources', 'unresolved', 'independent_review_shas'}}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('role-ledger', 'role-packet', 'ownership', 'output-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--provider', choices=['gpt', 'claude'], required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--reviewer-provider', choices=['gpt', 'claude'], required=True)
    parser.add_argument('--reviewer-model', required=True)
    parser.add_argument('--effort', default='high')
    parser.add_argument('--max-request-bytes', type=int, default=500000)
    args = parser.parse_args()
    if args.max_request_bytes < 1:
        parser.error('max-request-bytes must be positive')
    execute(args)


if __name__ == '__main__':
    main()
