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
