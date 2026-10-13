import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('quality_packets', Path(__file__).parents[2] / 'scripts/cvp-quality-packets.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source(path, kind):
    return dict(path=str(path), source_type=kind, source_id='SRC-a', physical_sha256=MODULE.PF.digest(path))


def test_full_transcript_preserves_tail_editorial_roles_and_deleted_boundaries(tmp_path):
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(dict(script=[dict(index='subtitle-1', text='## Editorial'),
         dict(index=8, text='甲~~乙~~丙'), dict(index=9, text='last original segment')]), ensure_ascii=False))
    packet = MODULE.read_full_source(source(path, 'sermon_transcript'))
    assert len(packet['segments']) == 3
    assert packet['segments'][0]['editorial']
    assert packet['segments'][1]['live_text'] == '甲\n丙'
    assert packet['segments'][1]['physical_text'] == '甲~~乙~~丙'
    assert packet['segments'][-1]['physical_text'] == 'last original segment'


def test_notes_are_complete_and_sha_drift_rejects(tmp_path):
    path = tmp_path / 'source.md'
    path.write_text('# Heading\n\nfirst\n\nlast')
    pin = source(path, 'notes_manuscript')
    packet = MODULE.read_full_source(pin)
    assert [r['physical_text'] for r in packet['segments']] == ['# Heading', 'first', 'last']
    path.write_text('changed')
    with pytest.raises(ValueError, match='drift'):
        MODULE.read_full_source(pin)


def test_dependency_query_is_read_only_and_quotes_exact_ids(monkeypatch):
    observed = {}
    class Result:
        returncode = 0
        stdout = ''
    def run(command, **kwargs):
        observed['sql'] = kwargs['input']
        return Result()
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    assert MODULE.query_dependencies('unused', {"CL-a'quote"}, []) == []
    assert "'CL-a''quote'" in observed['sql']
    assert 'REPEATABLE READ READ ONLY' in observed['sql']
    for mutation in ('INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'ALTER '):
        assert mutation not in observed['sql']


def test_compact_material_is_exactly_reversible_with_deleted_and_editorial_text():
    payload = {'complete_source': {'segments': [
        {'physical_ordinal': 1, 'physical_text': '# Heading', 'live_text': '# Heading', 'editorial': True},
        {'physical_ordinal': 2, 'physical_text': '甲~~乙~~丙', 'live_text': '甲\n丙', 'editorial': False}]}}
    encoded = MODULE.compact_preview(payload)
    assert MODULE.expand_preview(encoded) == payload
    assert payload['complete_source']['segments'][1]['live_text'] == '甲\n丙'
    payload['complete_source']['segments'][1]['live_text'] = 'wrong derivation'
    with pytest.raises(ValueError, match='cannot omit'):
        MODULE.compact_preview(payload)
