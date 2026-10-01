import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import exegesis_intelligent_grouping_job as job
from backend.pipeline import exegesis_grouping_capacity as capacity
from backend.pipeline import exegesis_grouping_transport as transport
from backend.pipeline import viewpoint_passage_grouping_sample_runner as sample
from backend.pipeline.exegesis_grouping_packet import compact_json, INSTRUCTION
from backend.pipeline.exegesis_grouping_source_packet import SVG_EXCLUDED, project_for_model, text_occurrences
from backend.pipeline.viewpoint_passage_grouping_preflight import plan_reviewed_passage_unit
from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse

spec = importlib.util.spec_from_file_location('independent_group_review', job.REVIEWER)
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)

ROW_TEXT = '第六天，~~被劃掉的話~~登山變像印證前面的應許。\n換行：κοινωνία ☧ 😀'
SVG_ONLY_TEXT = '視覺原件裡才有的字'
SVG = f'<svg xmlns="http://www.w3.org/2000/svg"><text>{SVG_ONLY_TEXT}</text></svg>'
MARKDOWN = '## 母本標題\n\n正文：應許在前，實現在後。\n\n![結構圖](/web/data/structure.svg)\n\n結尾限定。\n'


def seal(path, value):
    return transport.write_new(path, value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    transcript = tmp_path / 'source.json'
    transcript.write_text(json.dumps({'metadata': {'title': '元資料標題'}, 'script': [
        {'index': 1, 'start_time': 0, 'end_time': 5, 'text': ROW_TEXT, 'type': 'content', 'user_id': 'u'},
        {'index': 'subtitle-1', 'type': 'subtitle', 'text': '## 字幕'},
        {'index': 2, 'start_time': 5, 'end_time': 9, 'text': '第二段。'}]}, ensure_ascii=False), encoding='utf-8')
    svg = tmp_path / 'structure.svg'
    svg.write_text(SVG, encoding='utf-8')
    notes = tmp_path / 'final.md'
    notes.write_text(MARKDOWN, encoding='utf-8')
    sources = [dict(source_id='S1', source_type='sermon_transcript', source_content_sha256='body', path=str(transcript), file_sha256=sha(transcript),
                    paragraphs=[{'paragraph_key': 'S0001', 'text': '伪造的preparation段落'}]),
               dict(source_id='S2', source_type='notes_manuscript', source_content_sha256='body2', path=str(notes), file_sha256=sha(notes),
                    linked_files=[dict(path=str(svg), file_sha256=sha(svg))])]
    index = {s['source_id']: s for s in sources}
    claims, decisions, roles = [], [], []
    for number in range(3843):
        sid = 'S2' if 20 <= number < 24 else 'S1'
        quote = '登山變像' if sid == 'S1' else '應許在前'
        if number == 23:
            quote = SVG_ONLY_TEXT  # reviewed ownership evidence taken from the visual original
        c = dict(claim_id=f'C{number}', claim_revision=1, claim_content_sha256=f'sha{number}',
                 source_id=sid, source_revision=1, source_content_sha256=index[sid]['source_content_sha256'],
                 source_file_sha256=index[sid]['file_sha256'], statement=f'Claim {number}', claim_type='explicit',
                 scripture_refs=['太16:28'], evidence_steps=[dict(evidence_step_id=f'E{number}', revision=1, content_sha256=f'e{number}',
                    statement='證據', scripture_refs=[], fragments=[dict(fragment_id=f'F{number}', revision=1, content_sha256=f'f{number}',
                    paragraph_key='S0001', verbatim_excerpt=SVG if number == 23 else quote)])])
        claims.append(c)
        roles.append(dict(claim_id=c['claim_id'], role='passage_exegesis'))
        row = {k: c[k] for k in ('claim_id', 'claim_revision', 'claim_content_sha256', 'source_id', 'source_revision', 'source_content_sha256')}
        if number < 24:
            row.update(status='reviewed', primary='Matt.16.28-Matt.17.8', secondary=[{'reference': 'Mark.9.1', 'role': 'parallel'}],
                reason='应许与实现', evidence=[{'quote': quote, 'location': 'S0001'}], approval_basis='dual_model_consensus')
        else:
            row.update(status='unresolved', primary='', secondary=[], missing='原件尚不能证明唯一主归属')
        decisions.append(row)
    approval = seal(tmp_path / 'approval.json', {'decisions': [r for r in decisions if r['status'] == 'reviewed']})
    for row in decisions[:24]:
        row['review_artifact_sha256'] = approval['artifact_sha256']
    packet = seal(tmp_path / 'packet.json', dict(claims=claims, schema_version='wang_claim_passage_role_packet_v4'))
    ledger = seal(tmp_path / 'ledger.json', dict(schema_version='wang_claim_passage_role_ledger_v8',
        status='all_eligible_reviewed_with_user_approved_context_overlay', decisions=roles, packet_sha256=packet['artifact_sha256']))
    monkeypatch.setattr(job, 'FORMAL_LEDGER', ledger['artifact_sha256'])
    monkeypatch.setattr(job, 'FORMAL_PACKET', packet['artifact_sha256'])
    ownership = seal(tmp_path / 'ownership.json', dict(schema_version='wang_exegesis_reviewed_ownership_v1',
        role_ledger_sha256=ledger['artifact_sha256'], role_packet_sha256=packet['artifact_sha256'], decisions=decisions,
        sources=sources, review_artifacts=[{'path': str(tmp_path / 'approval.json'), 'artifact_sha256': approval['artifact_sha256']}]))
    return ledger, packet, ownership


def assert_model_payload_shape(payload):
    wire = compact_json(payload)
    assert payload['projection_format'] == 'wang_exegesis_model_projection_v2'
    for key in ('"file_sha256":', '"path":', '"content_sha256":', '"claim_revision":', '"review_artifact_sha256":', '"paragraphs":', '"original_text":'):
        assert key not in wire
    assert '伪造' not in wire and '<svg' not in wire and '<?xml' not in wire
    for source in payload['sources']:
        for text in [r['text'] for r in source.get('rows', [])] + [b['text'] for b in source.get('blocks', [])]:
            assert text_occurrences(wire, text) == 1, text
        for link in source.get('linked_files', []):
            assert link['svg_excluded_by_user'] is True and link['excluded'] == SVG_EXCLUDED and 'original_text' not in link
    assert payload['visual_evidence_policy']


def test_full_schedule_cross_chapter_direct_and_oversized_resume(tmp_path, monkeypatch, inputs):
    calls = []
    monkeypatch.setattr(job, 'verify_current', lambda claims: calls.append(('current', len(claims))))
    def propose(**kwargs):
        calls.append(('plan', len(kwargs['payload']['claims'])))
        assert_model_payload_shape(kwargs['payload'])
        assert kwargs['payload']['sources'][0]['rows'][0]['text'] == ROW_TEXT
        assert kwargs['payload']['claims'][0]['ownership']['secondary'] == [{'reference': 'Mark.9.1', 'role': 'parallel'}]
        visual = kwargs['payload']['claims'][23]['evidence_steps'][0]['fragments'][0]
        assert 'verbatim_excerpt' not in visual and visual['verbatim_excerpt_excluded']['excluded'] == SVG_EXCLUDED
        kwargs['directory'].mkdir(parents=True)
        return {'units': [dict(unit_id=f'u{i}', passage_key='Matt.16.28-Matt.17.8', claim_ids=ids,
                    rationale='完整跨章论证', evidence=[dict(source_id='S1', location='S0001', quote='登山變像')])
                    for i, ids in enumerate([[f'C{n}' for n in range(21)], ['C21', 'C22', 'C23']])]}
    monkeypatch.setattr(job, 'call', propose)
    def split(**kwargs):
        calls.append(('split', len(kwargs['payload']['claims'])))
        assert_model_payload_shape(kwargs['payload'])
        assert {s['source_id'] for s in kwargs['payload']['sources']} == {'S1', 'S2'}  # cross-source unit, SVG reference only
        assert kwargs['payload']['reviewed_unit']['unit_id'] == 'u0'
        kwargs['directory'].mkdir(parents=True)
        ids = [c['claim_id'] for c in kwargs['payload']['claims']]
        return plan_reviewed_passage_unit(unit_id=kwargs['unit_id'], claim_ids=ids, batch_size=20,
            model_split=ClaimGroupingResponse(scope_label=kwargs['unit_id'], groups=[
                dict(group_key='premise', claim_ids=ids[:10], rationale='前提'),
                dict(group_key='conclusion', claim_ids=ids[10:], rationale='推理与结论')]))
    monkeypatch.setattr(job, 'split_reviewed_unit', split)
    def independent(payload, proposal, directory, args, binding, model_payload_sha256):
        # Audit layer keeps every binding; checked against the actual first member of this unit/book.
        first = payload['claims'][0]
        assert payload['sources'][0]['file_sha256'] and first['claim_content_sha256'] == 'sha' + first['claim_id'][1:]
        assert all(f['path'] and f['file_sha256'] and 'original_text' not in f for s in payload['sources'] for f in s['linked_files'])
        assert sha256_json(project_for_model(payload)) == model_payload_sha256
        if (directory / 'validated.json').exists():
            return job.checked(directory / 'validated.json')['response']
        directory.mkdir(parents=True)
        report = dict(status='pass', binding=binding, artifact_sha256='independent-test-sha')
        seal(directory / 'validated.json', dict(response=report))
        return report
    monkeypatch.setattr(job, 'independent_review', independent)
    args = argparse.Namespace(role_ledger=tmp_path / 'ledger.json', role_packet=tmp_path / 'packet.json',
        ownership=tmp_path / 'ownership.json', output_root=tmp_path / 'out', provider='gpt', model='test-gpt',
        reviewer_provider='claude', reviewer_model='test-claude', effort='high', max_request_bytes=500000)
    job.execute(args)
    manifest = job.checked(args.output_root / 'manifest.json')
    assert manifest['grouped_count'] == 24 and manifest['unresolved_count'] == 3819
    assert manifest['status'] == 'partial_with_explicit_unresolved' and manifest['svg_source_in_model_input'] is False
    assert manifest['missing'] == manifest['duplicate'] == manifest['foreign'] == 0
    assert calls.count(('split', 21)) == 1  # only >20 calls the grouping model
    assert len(manifest['groups']) == 3
    assert manifest['claim_packets'][0]['ownership']['secondary'] == inputs[2]['decisions'][0]['secondary']
    assert manifest['claim_packets'][23]['evidence_steps'][0]['fragments'][0]['verbatim_excerpt'] == SVG  # frozen evidence intact in audit output
    assert set(manifest['canonical_source_shas']) == {'S1', 'S2'}
    config = job.checked(args.output_root / 'config.json')
    assert config['visual_originals'] == SVG_EXCLUDED and config['model_projection'] == 'wang_exegesis_model_projection_v2'
    audit = job.checked(args.output_root / 'audit-payloads' / 'units' / 'Matt.json')
    assert audit['audit_payload']['sources'][0]['rows'][0]['text'] == ROW_TEXT
    assert audit['audit_payload']['sources'][0]['file_sha256'] == inputs[2]['sources'][0]['file_sha256']
    assert audit['audit_payload']['sources'][0]['discarded_input_fields']['paragraphs']['count'] == 1
    assert audit['audit_payload']['sources'][1]['linked_files'][0]['file_sha256'] == inputs[2]['sources'][1]['linked_files'][0]['file_sha256']
    completeness = audit['projection_completeness']
    assert completeness['claims'] == 24 and completeness['linked_files'] == 1
    assert completeness['svg_excluded_linked_files'] == 1 and completeness['svg_excluded_fragment_excerpts'] == 1
    assert audit['model_payload_sha256'] == sha256_json(project_for_model(audit['audit_payload']))
    assert '<svg' not in json.dumps(audit['audit_payload']['sources'])  # audit keeps references, not SVG text
    assert (args.output_root / 'audit-payloads' / 'groups' / '00000_u0.json').exists()
    job.execute(args)
    assert calls.count(('plan', 24)) == 1 and calls.count(('split', 21)) == 1
    assert calls.count(('current', 3843)) == 2  # fresh drift check even when all answers cached
    args.model = 'changed-model'
    with pytest.raises(ValueError, match='drift'):
        job.execute(args)


@pytest.mark.parametrize('mutation,pattern', [
    ('candidate', 'candidate-only'), ('primary', 'actual approved'), ('revision', 'revision/SHA'),
    ('missing', 'specific missing'), ('secondary', 'secondary'), ('duplicate', 'ownership denominator'),
    ('foreign', 'ownership denominator'), ('review', 'verified artifact'), ('preview', 'reviewed ownership'),
    ('inlined', 'pre-inlined'), ('linked_inlined', 'pre-inlined')])
def test_ownership_fails_closed(inputs, mutation, pattern):
    ledger, packet, original = inputs
    ownership = copy.deepcopy(original)
    row = ownership['decisions'][0]
    if mutation == 'candidate': row['status'] = 'candidate'
    if mutation == 'primary': row['primary'] = 'Matt.16.1'
    if mutation == 'revision': row['claim_revision'] = 2
    if mutation == 'missing': ownership['decisions'][-1]['missing'] = ''
    if mutation == 'secondary': row.pop('secondary')
    if mutation == 'duplicate': ownership['decisions'].append(copy.deepcopy(row))
    if mutation == 'foreign': row['claim_id'] = 'OTHER'
    if mutation == 'review': row['review_artifact_sha256'] = 'unbound'
    if mutation == 'preview': ownership['schema_version'] = 'preview'
    if mutation == 'inlined': ownership['sources'][0]['original_text'] = '伪造'
    if mutation == 'linked_inlined': ownership['sources'][1]['linked_files'][0]['original_text'] = SVG
    with pytest.raises(ValueError, match=pattern):
        job.load_inputs(ledger, packet, ownership)


@pytest.mark.parametrize('target', ['source', 'linked_svg'])
def test_physical_source_drift(inputs, target):
    source = inputs[2]['sources'][0] if target == 'source' else inputs[2]['sources'][1]['linked_files'][0]
    Path(source['path']).write_text('changed')
    with pytest.raises(ValueError, match='physical source drift'):
        job.load_inputs(*inputs)


def test_preparation_paragraphs_never_become_model_text(inputs):
    ledger, packet, ownership = inputs
    claims, _, sources = job.load_inputs(ledger, packet, ownership)
    audit = job.audit_payload_for(claims[:3], sources, scope_label='Matt')
    model = job.model_payload_for(audit)
    assert_model_payload_shape(model)
    assert '伪造' not in compact_json(audit)
    assert audit['sources'][0]['discarded_input_fields']['paragraphs']['sha256'] == sha256_json(ownership['sources'][0]['paragraphs'])


@pytest.mark.parametrize('ids', [['A'], ['A', 'A'], ['A', 'B', 'OTHER']])
def test_unit_coverage_no_repair(ids):
    unit = dict(unit_id='cross_chapter', passage_key='Matt.16.28-Matt.17.8', claim_ids=ids,
        rationale='应许与实现', evidence=[dict(source_id='S', location='P', quote='原文')])
    with pytest.raises(ValueError, match='membership'):
        job.validate_units({'units': [unit]}, [{'claim_id': 'A'}, {'claim_id': 'B'}])


def test_failed_checkpoint_never_retries(tmp_path):
    directory = tmp_path / 'call'
    calls = []
    def fail():
        calls.append(1)
        directory.mkdir()
        raise ValueError('semantic failure')
    with pytest.raises(ValueError, match='semantic failure'): job.obtain(directory, 'same', fail)
    with pytest.raises(ValueError, match='no automatic retry'): job.obtain(directory, 'same', fail)
    assert len(calls) == 1


@pytest.fixture
def fake_transport(monkeypatch):
    client = SimpleNamespace(executable='claude-test', environment={}, _verify_subscription_login=lambda: None)
    monkeypatch.setattr(transport, 'ClaudeSubscriptionClient', lambda **kwargs: client)
    return client


def test_invalid_raw_saved_before_parse(tmp_path, monkeypatch, fake_transport):
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='not JSON', stderr='diagnostic', returncode=0))
    with pytest.raises(json.JSONDecodeError):
        transport.call(provider='claude', model='test', effort='high', prompt='规则', payload={'claims': []},
            schema={}, directory=tmp_path / 'call', max_bytes=10000)
    assert job.checked(tmp_path / 'call/transport.raw.json')['stdout'] == 'not JSON'
    assert (tmp_path / 'call/failure.json').exists()


def test_request_limit_includes_prompt_and_schema_and_records_three_payload_sizes(tmp_path, monkeypatch, fake_transport):
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: pytest.fail('must not call model'))
    payload = {'claims': [{'claim_id': str(i), 'statement': '完整Claim及逐字證據。' * 20} for i in range(5)]}
    with pytest.raises(ValueError, match='byte limit'):
        transport.call(provider='claude', model='test', effort='high', prompt='很长规则' * 100,
            payload=payload, schema={'description': '完整schema' * 100}, directory=tmp_path / 'call', max_bytes=1000)
    request = job.checked(tmp_path / 'call/request.json')
    assert request['request_bytes'] > 1000 and request['payload'] == payload
    assert request['pretty_payload_bytes'] > request['compact_uninterned_payload_bytes'] >= request['wire_payload_bytes']
    assert request['interned'] is True and request['byte_fit_is_not_token_fit'] is True
    assert request['request_bytes'] >= request['wire_payload_bytes'] + request['prompt_bytes'] + request['schema_bytes']


def test_independent_reviewer_rereads_originals_and_checks_projection_before_any_model_call(tmp_path, monkeypatch, inputs):
    ledger, packet, ownership = inputs
    claims, _, sources = job.load_inputs(ledger, packet, ownership)
    members = claims[18:24]  # spans S1 and S2, includes the Claim whose evidence quotes the visual original
    audit = job.audit_payload_for(members, sources, scope_label='Matt')
    model = job.model_payload_for(audit)
    proposal = {'units': [dict(unit_id='u', passage_key='Matt.16.28-Matt.17.8', claim_ids=[c['claim_id'] for c in members],
                               rationale='r', evidence=[dict(source_id='S2', location='B2', quote='應許在前')])]}
    monkeypatch.setattr(reviewer.subprocess, 'run', lambda *a, **k: pytest.fail('model must not be called'))
    def run(name, request):
        path = tmp_path / f'{name}.json'
        reviewer.seal(path, request)
        monkeypatch.setattr(sys, 'argv', ['review', '--input', str(path), '--output-dir', str(tmp_path / name)])
        reviewer.main()
    base = dict(proposal=proposal, binding='b', proposer_provider='gpt', reviewer_provider='claude', reviewer_model='m',
                max_request_bytes=500000, effort='high')
    with pytest.raises(ValueError, match='own projection'):
        run('wrong_sha', dict(base, payload=audit, model_payload_sha256='0' * 64))
    assert reviewer.checked(tmp_path / 'wrong_sha' / 'failure.json')['error_type'] == 'ValueError'
    forged = copy.deepcopy(audit)
    forged['sources'][0]['rows'][0]['text'] = '伪造正文'
    with pytest.raises(ValueError, match='differs from physical original'):
        run('forged_text', dict(base, payload=forged, model_payload_sha256=sha256_json(project_for_model(forged))))
    inlined = copy.deepcopy(audit)
    inlined['sources'][1]['linked_files'][0]['original_text'] = SVG  # not a legitimate audit object: SVG text never travels
    with pytest.raises(ValueError, match='inlined linked file text is not accepted'):
        run('inlined_svg', dict(base, payload=inlined, model_payload_sha256='0' * 64))  # rejected on re-read, before any SHA check
    bad_quote = copy.deepcopy(audit)
    bad_quote['claims'][0]['ownership']['evidence'][0]['quote'] = '元資料標題'  # metadata string is not professor text
    with pytest.raises(ValueError, match='not verbatim in physical source'):
        run('metadata_quote', dict(base, payload=bad_quote, model_payload_sha256=sha256_json(project_for_model(bad_quote))))
    svg_proposal = copy.deepcopy(proposal)
    svg_proposal['units'][0]['evidence'][0]['quote'] = SVG_ONLY_TEXT  # the proposer never saw the visual original
    with pytest.raises(ValueError, match='unit proposal evidence is not verbatim'):
        run('svg_quote_proposal', dict(base, proposal=svg_proposal, payload=audit, model_payload_sha256=sha256_json(model)))
    # Fully consistent input (ownership quote from the SVG verified on disk) reaches the subscription login check.
    monkeypatch.setattr(reviewer.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr=''))
    with pytest.raises(ValueError, match='subscription login'):
        run('consistent', dict(base, payload=audit, model_payload_sha256=sha256_json(model)))
    request = reviewer.checked(tmp_path / 'consistent' / 'request.json')
    assert request['roundtrip_verified'] and request['model_payload_sha256'] == sha256_json(model)
    assert request['payload']['proposal'] == proposal and request['physical_sources_reread'] == ['S1', 'S2']
    assert request['linked_files_sha_verified'] == [sources[1]['linked_files'][0]['file_sha256']]
    assert request['svg_source_in_model_input'] is False and request['projection_completeness']['svg_excluded_linked_files'] == 1
    sent = json.dumps(request['payload'], ensure_ascii=False)
    assert '<svg' not in sent and '<?xml' not in sent  # the reviewed ownership quote (plain text) stays; SVG markup never travels
    assert request['compact_uninterned_payload_bytes'] >= request['wire_payload_bytes']
    # Physical SVG drift is intercepted by the reviewer's own disk read although the SVG text is never sent.
    Path(sources[1]['linked_files'][0]['path']).write_text('<svg><text>changed</text></svg>')
    with pytest.raises(ValueError, match='physical source drift'):
        run('svg_drift', dict(base, payload=audit, model_payload_sha256=sha256_json(model)))


def test_independent_report_requires_verbatim_model_visible_evidence(tmp_path):
    strings = {'S': ['第六天登山變像。']}
    proposal = {'groups': [{'group_key': 'g', 'claim_ids': ['A']}]}
    response = {'status': 'pass', 'findings': [dict(key='g', status='pass', reason='真实论证边界',
        evidence=[dict(source_id='S', location='P1', quote='登山變像')])]}
    assert reviewer.validate_report(response, proposal, strings, 'binding')['status'] == 'pass'
    response['findings'][0]['evidence'][0]['quote'] = '登山变像'
    with pytest.raises(ValueError, match='non-verbatim'):
        reviewer.validate_report(response, proposal, strings, 'binding')
    response['findings'][0]['evidence'][0]['quote'] = SVG_ONLY_TEXT  # the reviewer model never received the SVG
    with pytest.raises(ValueError, match='non-verbatim'):
        reviewer.validate_report(response, proposal, strings, 'binding')


def test_generic_prompt_and_matthew_regression_are_scoped(tmp_path, monkeypatch):
    prompts = []
    def fake(**kwargs):
        prompts.append(kwargs['prompt'])
        ids = [c['claim_id'] for c in kwargs['payload']['claims']]
        return dict(scope_label='u', groups=[dict(group_key='a', claim_ids=ids[:10], rationale='前提'),
                                            dict(group_key='b', claim_ids=ids[10:], rationale='结论')])
    monkeypatch.setattr(transport, 'call', fake)
    for regression in (False, True):
        sample.split_reviewed_unit(unit_id='u', payload={'claims': [{'claim_id': str(n)} for n in range(21)]},
            provider='gpt', model='configurable', effort='high', directory=tmp_path / str(regression),
            max_request_bytes=500000, regression_context=regression)
    assert '拉比用语' not in prompts[0] and '拉比用语' in prompts[1]


def preparation_rows(packet):
    claims = {c['claim_id']: c for c in packet['claims']}
    def row(number, status, primary, keys):
        return dict(claim=claims[f'C{number}'], role_decision=dict(role='passage_exegesis', interpreted_passage_keys=keys),
                    preparation_status=status, primary=primary, secondary_relations=[], passage_location_review=None)
    rows = []
    for number in range(22):  # 22 reviewed-primary candidates on one key, including S2 members and the visual-quote Claim
        rows.append(row(number, 'primary_confirmed_passage_membership_not_reviewed', 'Matt.16.19', []))
    rows += [row(n, 'existing_keys_require_primary_and_membership_review', '', ['Mark.9.1']) for n in range(30, 33)]
    # Unreviewed citations of the same key join the same candidate set (merged, each keeping its status).
    rows += [row(n, 'existing_keys_require_primary_and_membership_review', '', ['Matt.16.19', 'Mark.9.1', 'Matt.16.19']) for n in range(60, 62)]
    rows += [row(n, 'unresolved_primary', '', []) for n in range(40, 45)]
    rows.append(row(50, 'existing_keys_require_primary_and_membership_review', '', []))
    return rows


def test_capacity_measures_all_four_request_kinds_as_labelled_diagnostics(tmp_path, inputs):
    ledger, packet, ownership = inputs
    preparation = dict(artifact_sha256='prep', rows=preparation_rows(packet))
    report = capacity.measure(preparation, ownership['sources'], tmp_path / 'cap', 'codex-test', 500000,
                              proposer_model='gpt-test', reviewer_provider='claude', reviewer_model='claude-test', reviewer_cli='claude-test')
    assert report['model_calls'] == 0 and report['formal_proposals_available'] is False and report['grouping_authorized'] is False
    assert report['svg_source_in_model_input'] is False and report['oversized_candidate_membership'] == capacity.MERGED
    assert [b['book'] for b in report['books']] == ['Matt', 'Mark'] and report['unassigned_claim_ids'] == ['C50']  # canonical order
    assert report['unresolved_primary_excluded'] == 5
    matt, mark = report['books']
    assert matt['candidate_claim_count'] == 24 and mark['candidate_claim_count'] == 5 and matt['diagnostic'] == capacity.DIAGNOSTIC
    for item in (matt['l1_proposal'], matt['l1_review']):
        assert item['pretty_payload_bytes'] > item['compact_uninterned_payload_bytes'] >= item['interned_wire_payload_bytes']
        # Exact accounting: wire + argv, plus the schema file when it travels as a file argument (GPT).
        assert item['complete_request_bytes'] == item['wire_bytes'] + item['argv_bytes'] + (item['schema_bytes'] if item['schema_carried_in'] == 'file_argument' else 0)
        assert item['complete_request_bytes'] >= item['interned_wire_payload_bytes'] + item['prompt_bytes'] + item['schema_bytes']
        assert item['fit'] == 'bytes_only_not_token_capacity' and item['lossless_roundtrip'] and item['exceeds_limit'] is False
        assert item['svg_source_in_model_input'] is False and item['projection_completeness']['svg_excluded_linked_files'] == 1
        assert item['projection_completeness']['svg_excluded_fragment_excerpts'] == 0  # C23 is not a Matt candidate here
    assert matt['l1_proposal']['prompt_carried_in'] == 'wire' and matt['l1_proposal']['schema_carried_in'] == 'file_argument'
    assert matt['l1_review']['prompt_carried_in'] == 'argv' and matt['l1_review']['schema_carried_in'] == 'argv'
    assert matt['l1_proposal']['schema_sha256'] == sha256_json(job.unit_schema())
    assert matt['l1_review']['proposal_origin'] == capacity.PLACEHOLDER and matt['l1_review']['proposal_bytes_lower_bound']
    assert matt['l1_review']['schema_sha256'] == sha256_json(reviewer.review_schema())
    assert matt['l1_review']['model_payload_sha256'] == matt['l1_proposal']['model_payload_sha256']
    assert len(report['oversized_candidate_units']) == 1
    unit = report['oversized_candidate_units'][0]
    assert unit['unit_id'] == 'candidate:Matt.16.19' and unit['regression_context'] is True and unit['unit_origin'] == capacity.MERGED
    assert unit['candidate_claim_count'] == 24 and unit['source_count'] == 2
    assert unit['membership_basis_counts'] == {capacity.REVIEWED_PRIMARY: 22, capacity.UNREVIEWED_KEY: 2}
    assert unit['l2_split']['schema_sha256'] == sha256_json(capacity.grouping_schema())
    generic = (job.PROMPTS / 'exegesis_argument_grouping.md').read_text(encoding='utf-8')
    regression = (job.PROMPTS / 'exegesis_matthew_16_19_regression.md').read_text(encoding='utf-8')
    assert unit['l2_split']['prompt_sha256'] == sha256_json({'prompt': INSTRUCTION + generic + '\n' + regression})
    assert unit['l2_review']['kind'] == 'L2_independent_review' and unit['l2_review']['proposal_origin'] == capacity.PLACEHOLDER
    assert report['source_shas']['S1']['file_sha256'] == ownership['sources'][0]['file_sha256']
    assert report['source_shas']['S2']['linked_file_shas'] == [ownership['sources'][1]['linked_files'][0]['file_sha256']]
    assert report['svg_exclusions_in_l1_payloads'] == dict(linked_files=1, fragment_excerpts=0, excluded_chars=len(SVG))  # only Matt touches S2
    assert (tmp_path / 'cap' / 'report.json').exists()


def test_capacity_candidates_merge_per_key_keep_status_and_fail_on_duplicate_rows(inputs):
    packet = inputs[1]
    rows = preparation_rows(packet)
    units = capacity.candidate_oversized_units(dict(rows=rows))
    members = {m['claim_id']: m for m in units[0]['members']}
    assert len(members) == 24 and units[0]['claim_ids'] == list(members)  # exact-once, no duplicate from the repeated key
    assert members['C0']['ownership']['candidate_membership_basis'] == capacity.REVIEWED_PRIMARY
    assert members['C60']['ownership']['candidate_membership_basis'] == capacity.UNREVIEWED_KEY
    assert members['C60']['ownership']['candidate_passage_keys'] == ['Matt.16.19', 'Mark.9.1'] and members['C60']['ownership']['primary'] == ''
    assert members['C0']['ownership']['preparation_status'] == 'primary_confirmed_passage_membership_not_reviewed'
    assert all(m['ownership']['diagnostic'] == capacity.DIAGNOSTIC for m in members.values())
    assert capacity.candidate_oversized_units(dict(rows=rows), ceiling=23)[0]['claim_ids'] == units[0]['claim_ids']
    assert capacity.candidate_oversized_units(dict(rows=rows), ceiling=24) == []
    with pytest.raises(ValueError, match='preparation rows'):
        capacity.candidate_oversized_units(dict(rows=rows + [rows[0]]))
    with pytest.raises(ValueError, match='preparation rows'):
        capacity.candidate_books(dict(rows=rows + [rows[0]]))
