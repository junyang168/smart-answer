import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import exegesis_intelligent_grouping_job as job
from backend.pipeline import exegesis_grouping_transport as transport
from backend.pipeline import viewpoint_passage_grouping_sample_runner as sample
from backend.pipeline.viewpoint_passage_grouping_preflight import plan_reviewed_passage_unit
from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse

spec = importlib.util.spec_from_file_location('independent_group_review', job.REVIEWER)
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


def seal(path, value):
    return transport.write_new(path, value)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'paragraphs': ['第六天，登山變像印證前面的應許。']}, ensure_ascii=False))
    sources = [dict(source_id='S1', source_content_sha256='body', path=str(source),
                    file_sha256=hashlib.sha256(source.read_bytes()).hexdigest())]
    claims, decisions, roles = [], [], []
    for number in range(3843):
        c = dict(claim_id=f'C{number}', claim_revision=1, claim_content_sha256=f'sha{number}',
                 source_id='S1', source_revision=1, source_content_sha256='body',
                 source_file_sha256=sources[0]['file_sha256'], statement=f'Claim {number}',
                 scripture_refs=['太16:28'], evidence_steps=[])
        claims.append(c)
        roles.append(dict(claim_id=c['claim_id'], role='passage_exegesis'))
        row = {k: c[k] for k in ('claim_id', 'claim_revision', 'claim_content_sha256', 'source_id', 'source_revision', 'source_content_sha256')}
        if number < 24:
            row.update(status='reviewed', primary='Matt.16.28-Matt.17.8', secondary=[{'reference': 'Mark.9.1', 'role': 'parallel'}],
                reason='应许与实现', evidence=[{'quote': '登山變像', 'location': 'P1'}], approval_basis='dual_model_consensus')
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


def test_full_schedule_cross_chapter_direct_and_oversized_resume(tmp_path, monkeypatch, inputs):
    calls = []
    monkeypatch.setattr(job, 'verify_current', lambda claims: calls.append(('current', len(claims))))
    def propose(**kwargs):
        calls.append(('plan', len(kwargs['payload']['claims'])))
        kwargs['directory'].mkdir(parents=True)
        return {'units': [dict(unit_id=f'u{i}', passage_key='Matt.16.28-Matt.17.8', claim_ids=ids,
                    rationale='完整跨章论证', evidence=[dict(source_id='S1', location='P1', quote='登山變像')])
                    for i, ids in enumerate([[f'C{n}' for n in range(21)], ['C21', 'C22', 'C23']])]}
    monkeypatch.setattr(job, 'call', propose)
    def split(**kwargs):
        calls.append(('split', len(kwargs['payload']['claims'])))
        kwargs['directory'].mkdir(parents=True)
        ids = [c['claim_id'] for c in kwargs['payload']['claims']]
        return plan_reviewed_passage_unit(unit_id=kwargs['unit_id'], claim_ids=ids, batch_size=20,
            model_split=ClaimGroupingResponse(scope_label=kwargs['unit_id'], groups=[
                dict(group_key='premise', claim_ids=ids[:10], rationale='前提'),
                dict(group_key='conclusion', claim_ids=ids[10:], rationale='推理与结论')]))
    monkeypatch.setattr(job, 'split_reviewed_unit', split)
    def independent(payload, proposal, directory, args, binding):
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
    assert manifest['status'] == 'partial_with_explicit_unresolved'
    assert manifest['missing'] == manifest['duplicate'] == manifest['foreign'] == 0
    assert calls.count(('split', 21)) == 1  # only >20 calls the grouping model
    assert len(manifest['groups']) == 3
    assert manifest['claim_packets'][0]['ownership']['secondary'] == inputs[2]['decisions'][0]['secondary']
    job.execute(args)
    assert calls.count(('plan', 24)) == 1 and calls.count(('split', 21)) == 1
    assert calls.count(('current', 3843)) == 2  # fresh drift check even when all answers cached
    args.model = 'changed-model'
    with pytest.raises(ValueError, match='drift'):
        job.execute(args)


@pytest.mark.parametrize('mutation,pattern', [
    ('candidate', 'candidate-only'), ('primary', 'actual approved'), ('revision', 'revision/SHA'),
    ('missing', 'specific missing'), ('secondary', 'secondary'), ('duplicate', 'ownership denominator'),
    ('foreign', 'ownership denominator'), ('review', 'verified artifact'), ('preview', 'reviewed ownership')])
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
    with pytest.raises(ValueError, match=pattern):
        job.load_inputs(ledger, packet, ownership)


def test_physical_source_drift(inputs):
    source = inputs[2]['sources'][0]
    Path(source['path']).write_text('changed')
    with pytest.raises(ValueError, match='physical source drift'):
        job.load_inputs(*inputs)


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


def test_request_limit_includes_prompt_and_schema(tmp_path, monkeypatch, fake_transport):
    monkeypatch.setattr(transport.subprocess, 'run', lambda *a, **k: pytest.fail('must not call model'))
    with pytest.raises(ValueError, match='byte limit'):
        transport.call(provider='claude', model='test', effort='high', prompt='很长规则' * 100,
            payload={}, schema={'description': '完整schema' * 100}, directory=tmp_path / 'call', max_bytes=1000)
    request = job.checked(tmp_path / 'call/request.json')
    assert request['request_bytes'] > 1000 and request['payload'] == {}


def test_independent_reader_original_and_verbatim(tmp_path):
    file = tmp_path / 'original.json'
    file.write_text(json.dumps({'text': '第六天登山變像。'}, ensure_ascii=False))
    sources = [dict(source_id='S', path=str(file), file_sha256=hashlib.sha256(file.read_bytes()).hexdigest(), original_text='伪造文本')]
    originals, strings = reviewer.original_sources(sources)
    assert '伪造' not in originals[0]['original_text']
    proposal = {'groups': [{'group_key': 'g', 'claim_ids': ['A']}]}
    response = {'status': 'pass', 'findings': [dict(key='g', status='pass', reason='真实论证边界',
        evidence=[dict(source_id='S', location='P1', quote='登山變像')])]}
    assert reviewer.validate_report(response, proposal, strings, 'binding')['status'] == 'pass'
    response['findings'][0]['evidence'][0]['quote'] = '登山变像'
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


def test_complete_membership_counts_mapping_keys_not_assignment_values():
    job.exact({'c0001': 'u001', 'c0002': 'u001'}, ['c0001', 'c0002'], 'assignments')
    with pytest.raises(ValueError, match='assignments'):
        job.exact({'c0001': 'u001'}, ['c0001', 'c0002'], 'assignments')
