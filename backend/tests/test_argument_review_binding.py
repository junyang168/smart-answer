"""Review/input counterexamples, with no model calls or production writes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import argument_corrections as ac
from backend.pipeline import argument_fidelity as af
from backend.pipeline import argument_instance_load as loader
from backend.pipeline import argument_shape as sh


def package(number=1):
    source, fragment, step = f'SRC-{number}', f'F-{number}', f'DK-{number}-P01-ST01'
    claims = [f'DK-{number}-CL0001', f'DK-{number}-CL0002']
    return {
        'package_id': f'argument-test-{number}',
        'source_documents': [{'source_id': source, 'source_type': 'sermon_transcript', 'title': '測試講次'}],
        'source_fragments': [{'fragment_id': fragment, 'source_id': source, 'paragraph_key': 'S0001',
                              'verbatim_excerpt': '這是測試原話。這是第二個判斷。'}],
        'claims': [{'claim_id': cid, 'claim_type': 'explicit_claim', 'title': f'測試判斷 {i}',
                    'evidence_step_ids': [step], 'occurrences': [{'anchors': [{'source_fragment_id': fragment}]}]}
                   for i, cid in enumerate(claims)],
        'evidence_steps': [{'evidence_step_id': step, 'statement': '測試理由', 'source_fragment_ids': [fragment],
                            'produced_claim_ids': claims}],
        'observations': [], 'questions': [], 'position_nodes': [], 'knowledge_relations': [], 'claim_relations': [],
    }


def review_report(candidate_path, module):
    candidate = json.loads(candidate_path.read_bytes())
    rows = module.records(candidate) if module is af else module.rows(candidate)
    report = module.prefilter_report(rows, {r['job_id']: 0 for r in rows})
    report['input_binding'] = {'candidate_sha256': loader.sha(candidate_path),
                               'record_ids': sorted(r['job_id'] for r in rows)}
    return module.judged_report(report, [], {})


def write_run(root, candidate=None, corrections=()):
    candidate = package() if candidate is None else candidate
    creation = root / 'creation'; creation.mkdir(parents=True)
    original, corrected = creation / 'candidate.json', creation / 'candidate-owner-corrected.json'
    original.write_text(json.dumps(candidate, ensure_ascii=False))
    spec = creation / 'corrections.json'
    spec.write_text(json.dumps({'corrections': list(corrections)}, ensure_ascii=False))
    ac.apply(original, spec, corrected)
    for name, module in [('fidelity', af), ('shape', sh)]:
        folder = root / name; folder.mkdir()
        (folder / f'{name}-final.json').write_text(json.dumps(review_report(original, module), ensure_ascii=False))
    return corrected


def load_run(conn, root, **kwargs):
    candidate = root / 'creation/candidate-owner-corrected.json'
    return loader.load(conn, candidate, receipt_path=candidate.with_suffix('.receipt.json'),
                       fidelity_path=root / 'fidelity/fidelity-final.json',
                       shape_path=root / 'shape/shape-final.json', run_dir=root, **kwargs)


@pytest.mark.parametrize('name', ['fidelity', 'shape'])
def test_report_from_another_candidate_is_refused_before_sql(tmp_path, name):
    first, second = tmp_path / 'first', tmp_path / 'second'
    write_run(first)
    changed = package(); changed['claims'][0]['title'] = '尚未審過的判斷'
    write_run(second, changed)
    (second / name / f'{name}-final.json').write_bytes((first / name / f'{name}-final.json').read_bytes())
    with pytest.raises(ValueError, match='binding'):
        load_run(None, second)


@pytest.mark.parametrize('name', ['fidelity', 'shape'])
def test_report_must_cover_every_record_even_when_nothing_was_flagged(tmp_path, name):
    root = tmp_path / 'run'; write_run(root)
    path = root / name / f'{name}-final.json'; report = json.loads(path.read_text())
    report['input_binding']['record_ids'].pop()
    report['records' if name == 'fidelity' else 'claims'] -= 1
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='binding'):
        load_run(None, root)


def test_a_matching_corrected_sha_cannot_hide_changes_outside_the_corrections(tmp_path):
    root = tmp_path / 'run'; candidate = write_run(root)
    payload = json.loads(candidate.read_text()); payload['claims'][0]['title'] = '不在更正單的改動'
    candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    receipt_path = candidate.with_suffix('.receipt.json'); receipt = json.loads(receipt_path.read_text())
    receipt['corrected_sha256'] = loader.sha(candidate); receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='corrections'):
        load_run(None, root)


@pytest.mark.parametrize('part', ['candidate', 'corrections'])
def test_the_original_candidate_and_correction_spec_are_also_bound(tmp_path, part):
    root = tmp_path / 'run'; corrected = write_run(root)
    receipt = json.loads(corrected.with_suffix('.receipt.json').read_text())
    path = Path(receipt[part]); path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='receipt'):
        load_run(None, root)


@pytest.mark.parametrize('module', [af, sh])
def test_stale_prefilter_is_rejected_before_any_judge_call(tmp_path, monkeypatch, module):
    candidate = tmp_path / 'candidate.json'; candidate.write_text(json.dumps(package()))
    (tmp_path / 'prefilter.json').write_text(json.dumps(review_report(candidate, module)))
    changed = package(); changed['claims'][0]['title'] = '已換成另一句'; candidate.write_text(json.dumps(changed))
    def no_call(*args):
        pytest.fail('a stale prefilter must fail before calling Claude')
    monkeypatch.setattr(module, 'judge', no_call)
    monkeypatch.setattr(sys, 'argv', ['check', '--candidate', str(candidate), '--out', str(tmp_path), '--judge'])
    with pytest.raises(ValueError, match='binding'):
        module.main()
    assert not (tmp_path / ('fidelity-final.json' if module is af else 'shape-final.json')).exists()


@pytest.mark.parametrize('module', [af, sh])
def test_prefilter_cli_binds_all_records_without_changing_the_model_request(tmp_path, monkeypatch, module):
    from backend.pipeline import codex_subscription_client, evidence_refresh
    monkeypatch.setattr(codex_subscription_client, 'CodexSubscriptionClient', lambda **kw: SimpleNamespace())
    monkeypatch.setattr(evidence_refresh, 'Models', lambda *args: object())
    monkeypatch.setattr(module, 'prefilter', lambda rows, *args: {r['job_id']: 0 for r in rows})
    candidate = tmp_path / 'candidate.json'; candidate.write_text(json.dumps(package()))
    monkeypatch.setattr(sys, 'argv', ['check', '--candidate', str(candidate), '--out', str(tmp_path), '--prefilter'])
    module.main()
    report = json.loads((tmp_path / 'prefilter.json').read_text())
    assert report['input_binding'] == review_report(candidate, module)['input_binding']


@pytest.mark.parametrize('module', [af, sh])
def test_a_candidate_changed_during_prefilter_does_not_get_a_report(tmp_path, monkeypatch, module):
    from backend.pipeline import codex_subscription_client, evidence_refresh
    monkeypatch.setattr(codex_subscription_client, 'CodexSubscriptionClient', lambda **kw: SimpleNamespace())
    monkeypatch.setattr(evidence_refresh, 'Models', lambda *args: object())
    candidate = tmp_path / 'candidate.json'; candidate.write_text(json.dumps(package()))
    def changing(rows, *args):
        candidate.write_bytes(candidate.read_bytes() + b'\n')
        return {r['job_id']: 0 for r in rows}
    monkeypatch.setattr(module, 'prefilter', changing)
    monkeypatch.setattr(sys, 'argv', ['check', '--candidate', str(candidate), '--out', str(tmp_path), '--prefilter'])
    with pytest.raises(ValueError, match='candidate changed'):
        module.main()
    assert not (tmp_path / 'prefilter.json').exists()


@pytest.mark.parametrize('module', [af, sh])
def test_zero_looks_preserve_the_binding_without_a_model_call(tmp_path, monkeypatch, module):
    from backend.pipeline import claude_subscription_client
    monkeypatch.setattr(claude_subscription_client, 'ClaudeSubscriptionClient', lambda **kw: SimpleNamespace())
    candidate = tmp_path / 'candidate.json'; candidate.write_text(json.dumps(package()))
    pre = review_report(candidate, module)
    (tmp_path / 'prefilter.json').write_text(json.dumps(pre))
    monkeypatch.setattr(sys, 'argv', ['check', '--candidate', str(candidate), '--out', str(tmp_path), '--judge'])
    module.main()
    final = tmp_path / ('fidelity-final.json' if module is af else 'shape-final.json')
    assert json.loads(final.read_text())['input_binding'] == pre['input_binding']
    before = final.read_bytes()
    with pytest.raises(SystemExit, match='exists'):
        module.main()
    assert final.read_bytes() == before


@pytest.mark.parametrize('name', ['fidelity', 'shape'])
def test_an_unbound_legacy_report_is_not_silently_assigned_to_current_input(tmp_path, name):
    root = tmp_path / 'run'; write_run(root)
    path = root / name / f'{name}-final.json'; report = json.loads(path.read_text())
    report.pop('input_binding'); path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='binding'):
        load_run(None, root)
