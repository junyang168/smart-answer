import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from backend.pipeline import exegesis_intelligent_grouping_job as job

spec = importlib.util.spec_from_file_location('review411', Path(__file__).parents[2] / 'scripts/review-exegesis-grouping.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

@pytest.mark.parametrize('module', [job, review])
def test_svg_keeps_separate_prose_and_audit_hash(module):
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>graphic</text></svg>'
    result = module.exclude_svg('before' + svg + 'after')
    assert result['model_text_parts'] == ['before', {'svg_excluded': True,
        'sha256': hashlib.sha256(svg.encode()).hexdigest(), 'start': 6, 'end': 6 + len(svg)}, 'after']
    assert '<svg' not in json.dumps(result)
    with pytest.raises(ValueError):
        module.exclude_svg('before<svg>unclosed')


def test_payload_removes_duplicate_and_svg_without_mutating_original(tmp_path):
    source_file = tmp_path / 'source.json'
    source_file.write_text(json.dumps({'script': [{'text': 'before<svg/>after'}]}))
    source = dict(source_id='s', path=str(source_file), file_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest(), paragraphs=['duplicate'])
    claims = [dict(claim_id='c', source_id='s', excerpt='<svg/>')]
    payload = job.payload_for(claims, [source])
    assert 'paragraphs' not in payload['sources'][0]
    assert '<svg' not in json.dumps(payload)
    assert claims[0]['excerpt'] == '<svg/>'
    originals, strings = review.original_sources(payload['sources'])
    assert originals == payload['sources']
    assert 'before' in strings['s'] and 'after' in strings['s']
    assert 'beforeafter' not in strings['s']
    assert not any('graphic' in text for text in strings['s'])


def test_scoped_context_is_verbatim_and_not_a_passage_boundary(tmp_path):
    texts = [f'physical paragraph {i}' for i in range(15)]
    file = tmp_path / 'source.json'
    file.write_text(json.dumps({'script': [{'text': t} for t in texts]}))
    source = dict(source_id='s', path=str(file), file_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    claim = dict(claim_id='c', source_id='s', claim_revision=7, claim_content_sha256='claim-sha', evidence_steps=[
        dict(evidence_step_id='e', revision=3, statement='argument', fragments=[
            dict(fragment_id='f', revision=2, paragraph_key='S0008', verbatim_excerpt=texts[7])])])
    payload = job.payload_for([claim], [source])
    scoped = payload['sources'][0]
    assert scoped['selected_locations'] == [f'row:{n}' for n in range(6, 11)]
    assert [c['text'] for c in scoped['source_context']] == texts[5:10]
    assert scoped['context_is_complete_source'] is False
    assert 'original_text' not in scoped
    assert 'claim_revision' not in payload['claims'][0]
    assert claim['claim_revision'] == 7
    assert payload['frozen_claim_graph_sha256'] == job.sha256_json([claim])
    originals, _ = review.original_sources(payload['sources'])
    assert originals == payload['sources']
    scoped['source_context'][0]['text'] = 'forged'
    with pytest.raises(ValueError, match='context drift'):
        review.original_sources(payload['sources'])


def test_context_request_stops_grouping_and_missing_anchor_is_explicit(tmp_path):
    with pytest.raises(ValueError, match='context insufficient'):
        job.validate_units(dict(units=[], context_requests=[dict(source_id='s', location='row:8', reason='continuation not provided')]), [])
    file = tmp_path / 'source.json'
    file.write_text(json.dumps([{'text': 'original'}]))
    source = dict(source_id='s', path=str(file), file_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
    result = job.payload_for([dict(claim_id='c', source_id='s', evidence_steps=[dict(fragments=[
        dict(fragment_id='f', paragraph_key='S0001', verbatim_excerpt='not verbatim')])])], [source])
    assert result['sources'][0]['unresolved_evidence_locations'][0]['fragment_id'] == 'f'
    assert result['sources'][0]['source_context'][0]['text'] == 'original'
