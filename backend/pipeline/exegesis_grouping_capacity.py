"""Read-only #411 request capacity measurement for every model stage; never authorizes grouping.

Measures the exact wire of four request kinds with the runtime serializers:

* L1 whole-book passage-unit proposal (proposer CLI, planning prompt, unit schema);
* L1 independent review of that proposal (reviewer script's own serializer);
* L2 split of a candidate unit with more than 20 members (grouping prompt,
  optional Matt.16.19 regression context, strict grouping schema);
* L2 independent review of that split.

Formal L1 units and formal reviewed ownership do not exist yet, so every unit
and every proposal measured here is a labelled diagnostic candidate: book
association from the preparation's candidate keys, oversized units from all
candidate members of one passage key (reviewed-primary and unreviewed
citation members merged and de-duplicated, each member keeping its review
status), and proposals that are explicit placeholders sized like a real
response (a lower bound, never a semantic pass). No model is called, no
candidate is promoted, no SVG source enters a payload, and byte fit is never
reported as token fit.
"""
import argparse
from collections import defaultdict
import importlib.util
import json
from pathlib import Path
from backend.pipeline.exegesis_intelligent_grouping_job import (
    checked, exact, audit_payload_for, canonical_index, model_payload_for, unit_schema, PROMPTS, REVIEWER)
from backend.pipeline.exegesis_grouping_packet import unpack, compact_json
from backend.pipeline.exegesis_grouping_source_packet import assert_projection_complete
from backend.pipeline.exegesis_grouping_transport import serialize_request, write_new
from backend.pipeline.viewpoint_passage_grouping_preflight import passage_sort_key
from backend.api.canonical_repository.viewpoint_foundation import sha256_json

DIAGNOSTIC = 'diagnostic_candidate_not_reviewed_not_model_output'
PLACEHOLDER = 'DIAGNOSTIC_PLACEHOLDER_NOT_MODEL_OUTPUT'
MERGED = 'candidate_members_merged_by_passage_key'
REVIEWED_PRIMARY, UNREVIEWED_KEY = 'reviewed_primary', 'unreviewed_citation_key'
SCHEMA_VERSION = 'wang_exegesis_request_capacity_v3'


def load_reviewer():
    """Dynamically load the stdlib reviewer for its own pure serializer."""
    spec = importlib.util.spec_from_file_location('independent_group_review', REVIEWER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grouping_schema():
    from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
    from backend.api.canonical_repository.viewpoint_resolution import _strict_json_schema
    return _strict_json_schema(ClaimGroupingResponse.model_json_schema())


def canonical_order(key):
    """Canonical Bible order for report ordering; an unparseable key sorts last, never fails a measurement."""
    try:
        return (0, passage_sort_key(key))
    except ValueError:
        return (1, key)


def candidate_rows(preparation):
    """Preparation rows exact-once per Claim; explicitly unresolved rows excluded, never guessed."""
    exact([row['claim']['claim_id'] for row in preparation['rows']],
          {row['claim']['claim_id'] for row in preparation['rows']}, 'preparation rows')
    return [row for row in preparation['rows'] if row['preparation_status'] != 'unresolved_primary']


def candidate_keys(row):
    """Reviewed primary if present, else the unreviewed interpreted keys, each at most once."""
    keys = [row['primary']] if row['primary'] else row['role_decision'].get('interpreted_passage_keys', [])
    return list(dict.fromkeys(keys))


def candidate_claim(row, keys, basis=None):
    # Preparation history is not runtime Claim content; bindings stay in the audit payload.
    ownership = dict(primary=row['primary'], secondary=row['secondary_relations'] or [],
        preparation_status=row['preparation_status'], candidate_passage_keys=keys, diagnostic=DIAGNOSTIC)
    if basis is not None:
        ownership['candidate_membership_basis'] = basis
    return dict(row['claim'], ownership=ownership)


def candidate_books(preparation):
    books, unassigned = defaultdict(list), []
    for row in candidate_rows(preparation):
        keys = candidate_keys(row)
        if not keys:
            unassigned.append(row['claim']['claim_id'])
        for book in sorted({key.split('.')[0] for key in keys}):
            books[book].append(candidate_claim(row, keys))
    return books, unassigned


def candidate_oversized_units(preparation, ceiling=20):
    """Candidate L2 inputs: every candidate member of one passage key, merged across review status.

    A reviewed primary and an unreviewed citation of the same key land in the
    same candidate set (de-duplicated by Claim ID, reviewed basis preferred),
    so the measured capacity covers the whole candidate population of that
    passage. Historical review classification is a label on each member, not
    a grouping boundary; no member is promoted to a formal unit.
    """
    by_key = defaultdict(dict)
    for row in candidate_rows(preparation):
        keys = candidate_keys(row)
        for key in keys:
            basis = REVIEWED_PRIMARY if key == row['primary'] else UNREVIEWED_KEY
            claim_id = row['claim']['claim_id']
            if claim_id not in by_key[key] or basis == REVIEWED_PRIMARY:
                by_key[key][claim_id] = candidate_claim(row, keys, basis)
    units = []
    for key, members in sorted(by_key.items(), key=lambda item: canonical_order(item[0])):
        if len(members) <= ceiling:
            continue
        members = list(members.values())
        bases = {b: sum(1 for m in members if m['ownership']['candidate_membership_basis'] == b) for b in (REVIEWED_PRIMARY, UNREVIEWED_KEY)}
        units.append(dict(unit_id=f'candidate:{key}', passage_key=key, claim_ids=[c['claim_id'] for c in members],
            members=members, unit_origin=MERGED, membership_basis_counts=bases, diagnostic=DIAGNOSTIC,
            rationale=PLACEHOLDER, evidence=[dict(source_id=members[0]['source_id'], location=PLACEHOLDER, quote=PLACEHOLDER)]))
    return units


def placeholder_units_proposal(book, claims):
    """Sized like a response the planner could return; explicitly not model output."""
    return dict(units=[dict(unit_id=f'diagnostic_{book.lower()}', passage_key=f'{book}.1', claim_ids=[c['claim_id'] for c in claims],
        rationale=PLACEHOLDER, evidence=[dict(source_id=claims[0]['source_id'], location=PLACEHOLDER, quote=PLACEHOLDER)])],
        proposal_origin=PLACEHOLDER)


def placeholder_groups_proposal(unit):
    """One placeholder group listing every member; not a split, only a size lower bound."""
    return dict(scope_label=unit['unit_id'], groups=[dict(group_key='diagnostic_placeholder', claim_ids=list(unit['claim_ids']), rationale=PLACEHOLDER)],
        proposal_origin=PLACEHOLDER)


def measurement(kind, serialized, audit, model, limit, **labels):
    completeness = assert_projection_complete(audit, model)
    return dict(kind=kind, pretty_payload_bytes=serialized['pretty_payload_bytes'],
        compact_uninterned_payload_bytes=serialized['compact_uninterned_payload_bytes'],
        interned_wire_payload_bytes=serialized['wire_payload_bytes'], interned=serialized['interned'],
        prompt_bytes=serialized['prompt_bytes'], schema_bytes=serialized['schema_bytes'], argv_bytes=serialized['argv_bytes'],
        prompt_carried_in=serialized['prompt_carried_in'], schema_carried_in=serialized['schema_carried_in'],
        wire_bytes=len(serialized['wire'].encode()),
        complete_request_bytes=serialized['size'], limit=limit, exceeds_limit=serialized['size'] > limit,
        audit_payload_compact_bytes=len(compact_json(audit).encode()), audit_payload_sha256=sha256_json(audit),
        model_payload_sha256=sha256_json(model), lossless_roundtrip=True, fit='bytes_only_not_token_capacity',
        projection_completeness=completeness, svg_source_in_model_input=False, **labels)


def measure_proposal(kind, *, audit, model, prompt, schema, executable, proposer_model, directory, limit, **labels):
    request = serialize_request(provider='gpt', executable=executable, model=proposer_model, effort='high',
        prompt=prompt, payload=model, schema=schema, directory=directory)
    if unpack(request['wire_payload']) != model:
        raise ValueError('capacity packet lost material')
    # The GPT schema travels as a file argument, so it must be counted in addition to the wire.
    if request['size'] < request['wire_payload_bytes'] + request['prompt_bytes'] + request['schema_bytes']:
        raise ValueError('schema/prompt not part of measured request')
    return measurement(kind, request, audit, model, limit, prompt_sha256=sha256_json({'prompt': request['prompt']}),
        schema_sha256=sha256_json(schema), **labels)


def measure_review(kind, reviewer, *, audit, model, proposal, reviewer_provider, reviewer_model, reviewer_cli, directory, limit, **labels):
    reviewer.assert_projection_complete(audit, model)
    payload = dict(model, proposal=proposal)
    serialized = reviewer.serialize_review_request(provider=reviewer_provider, model=reviewer_model, effort='high',
        payload=payload, cli=reviewer_cli, root=directory)
    if reviewer.expand(serialized['wire_payload']) != payload:
        raise ValueError('reviewer capacity packet lost material')
    if serialized['size'] < serialized['wire_payload_bytes'] + serialized['prompt_bytes'] + serialized['schema_bytes']:
        raise ValueError('reviewer schema/prompt not part of measured request')
    return measurement(kind, serialized, audit, model, limit, prompt_sha256=sha256_json({'prompt': serialized['wire_prompt']}),
        schema_sha256=sha256_json(reviewer.review_schema()), proposal_origin=proposal['proposal_origin'],
        proposal_bytes_lower_bound=True, proposal_bytes=len(compact_json(proposal).encode()), **labels)


def measure(preparation, sources, root, executable, max_bytes, *, proposer_model='gpt-6-sol',
            reviewer_provider='claude', reviewer_model='claude-opus-5-5', reviewer_cli='claude'):
    reviewer = load_reviewer()
    canonical = canonical_index(sources)
    books, unassigned = candidate_books(preparation)
    oversized = candidate_oversized_units(preparation)
    root.mkdir(parents=True, exist_ok=False)
    planning_prompt = (PROMPTS / 'exegesis_passage_unit_planning.md').read_text()
    grouping_prompt = (PROMPTS / 'exegesis_argument_grouping.md').read_text(encoding='utf-8')
    regression_prompt = (PROMPTS / 'exegesis_matthew_16_19_regression.md').read_text(encoding='utf-8')
    book_results = []
    for book in sorted(books, key=lambda b: canonical_order(b + '.1')):
        claims = books[book]
        audit = audit_payload_for(claims, sources, canonical=canonical, scope_label=book, diagnostic=DIAGNOSTIC)
        model = model_payload_for(audit)
        proposal = placeholder_units_proposal(book, claims)
        book_results.append(dict(book=book, candidate_claim_count=len(claims),
            source_count=len(audit['sources']), diagnostic=DIAGNOSTIC,
            l1_proposal=measure_proposal('L1_unit_proposal', audit=audit, model=model, prompt=planning_prompt, schema=unit_schema(),
                executable=executable, proposer_model=proposer_model, directory=root / 'units' / book, limit=max_bytes),
            l1_review=measure_review('L1_independent_review', reviewer, audit=audit, model=model, proposal=proposal,
                reviewer_provider=reviewer_provider, reviewer_model=reviewer_model, reviewer_cli=reviewer_cli,
                directory=root / 'unit-reviews' / book, limit=max_bytes)))
    unit_results = []
    for unit in oversized:
        members = unit.pop('members')
        regression = unit['passage_key'] == 'Matt.16.19'
        audit = audit_payload_for(members, sources, canonical=canonical, scope_label=unit['unit_id'], reviewed_unit=unit, diagnostic=DIAGNOSTIC)
        model = model_payload_for(audit)
        proposal = placeholder_groups_proposal(unit)
        prompt = grouping_prompt + ('\n' + regression_prompt if regression else '')
        unit_results.append(dict(unit_id=unit['unit_id'], passage_key=unit['passage_key'], unit_origin=unit['unit_origin'],
            membership_basis_counts=unit['membership_basis_counts'],
            candidate_claim_count=len(members), source_count=len(audit['sources']), regression_context=regression, diagnostic=DIAGNOSTIC,
            l2_split=measure_proposal('L2_split_request', audit=audit, model=model, prompt=prompt, schema=grouping_schema(),
                executable=executable, proposer_model=proposer_model, directory=root / 'groups' / unit['unit_id'].replace(':', '_'), limit=max_bytes),
            l2_review=measure_review('L2_independent_review', reviewer, audit=audit, model=model, proposal=proposal,
                reviewer_provider=reviewer_provider, reviewer_model=reviewer_model, reviewer_cli=reviewer_cli,
                directory=root / 'group-reviews' / unit['unit_id'].replace(':', '_'), limit=max_bytes)))
    over = [r for r in book_results if r['l1_proposal']['exceeds_limit'] or r['l1_review']['exceeds_limit']]
    over_units = [r for r in unit_results if r['l2_split']['exceeds_limit'] or r['l2_review']['exceeds_limit']]
    l1_counts = [r['l1_proposal']['projection_completeness'] for r in book_results]
    svg = dict(linked_files=sum(c['svg_excluded_linked_files'] for c in l1_counts),
               fragment_excerpts=sum(c['svg_excluded_fragment_excerpts'] for c in l1_counts),
               excluded_chars=sum(c['svg_excluded_chars'] for c in l1_counts))
    return write_new(root / 'report.json', dict(schema_version=SCHEMA_VERSION,
        preparation_sha256=preparation['artifact_sha256'], proposer_model=proposer_model, executable=executable,
        reviewer_provider=reviewer_provider, reviewer_model=reviewer_model, reviewer_cli=reviewer_cli,
        grouping_authorized=False, formal_reviewed_ownership_available=False, formal_l1_units_available=False,
        formal_proposals_available=False, candidate_associations_not_reviewed_primary=True,
        oversized_candidate_membership=MERGED, svg_source_in_model_input=False, svg_exclusions_in_l1_payloads=svg,
        books=book_results, oversized_candidate_units=unit_results, unassigned_claim_ids=unassigned,
        unresolved_primary_excluded=sum(r['preparation_status'] == 'unresolved_primary' for r in preparation['rows']),
        books_over_limit=[r['book'] for r in over], oversized_units_over_limit=[r['unit_id'] for r in over_units],
        source_shas={sid: dict(file_sha256=src['file_sha256'], canonical_sha256=sha256_json(src),
                               linked_file_shas=[f['file_sha256'] for f in src['linked_files']]) for sid, src in sorted(canonical.items())},
        model_calls=0, source_text_omissions=0, total_exegesis=3843,
        explanation='Exact proposer/reviewer request bytes with the runtime serializers (argv + prompt + schema + interned payload). '
                    'Every unit and proposal is a diagnostic candidate; no formal L1 unit, ownership or model output exists. '
                    'Oversized candidates merge every member of one passage key across review status. '
                    'SVG/XML visual originals are SHA-verified references only and never enter a payload. '
                    'Byte fit alone does not establish model token capacity.'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'sources', 'output-root'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--executable', required=True, help='proposer CLI path (bytes of argv are counted)')
    p.add_argument('--proposer-model', default='gpt-6-sol')
    p.add_argument('--reviewer-provider', choices=['gpt', 'claude'], default='claude')
    p.add_argument('--reviewer-model', default='claude-opus-5-5')
    p.add_argument('--reviewer-cli', default='claude')
    p.add_argument('--max-request-bytes', type=int, default=500000)
    args = p.parse_args()
    report = measure(checked(args.preparation), checked(args.sources)['sources'], args.output_root,
                     args.executable, args.max_request_bytes, proposer_model=args.proposer_model,
                     reviewer_provider=args.reviewer_provider, reviewer_model=args.reviewer_model, reviewer_cli=args.reviewer_cli)
    print(json.dumps({'books': len(report['books']), 'books_over_limit': len(report['books_over_limit']),
                      'oversized_candidate_units': len(report['oversized_candidate_units']),
                      'oversized_units_over_limit': len(report['oversized_units_over_limit']),
                      'matthew': next((r for r in report['books'] if r['book'] == 'Matt'), None)}, ensure_ascii=False))


if __name__ == '__main__': main()
