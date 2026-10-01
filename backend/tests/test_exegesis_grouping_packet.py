import copy
import importlib.util
from pathlib import Path
import pytest
from backend.pipeline.exegesis_grouping_packet import pack, unpack, compact_json


def test_lossless_multilingual_complete_evidence_and_originals():
    text = '原文κοινωνία，章末的應許與次章的實現。\n' * 30
    payload = {'claims': [{'claim_id': str(i), 'statement': text, 'primary': 'Matt.16.28-Matt.17.8',
                'secondary': [{'reference': 'Mark.9.1', 'role': 'parallel'}],
                'evidence_steps': [{'statement': text, 'fragments': [{'verbatim_excerpt': text}]}]} for i in range(25)],
               'sources': [{'source_id': 'S', 'original_text': text}], 'revision': 3, 'sha': 'a' * 64}
    before = copy.deepcopy(payload)
    encoded = pack(payload)
    assert unpack(encoded) == payload == before
    assert len(compact_json(encoded).encode()) < len(compact_json(payload).encode())
    assert len(unpack(encoded)['claims']) == 25
    spec = importlib.util.spec_from_file_location('reviewer', Path(__file__).resolve().parents[2] / 'scripts/review-exegesis-grouping.py')
    reviewer = importlib.util.module_from_spec(spec); spec.loader.exec_module(reviewer)
    assert unpack(reviewer.compact_packet(payload)) == payload


def test_no_benefit_uses_original_and_reserved_key_rejected():
    assert pack({'statement': 'short', 'ids': ['A', 'B']}) == {'statement': 'short', 'ids': ['A', 'B']}
    with pytest.raises(ValueError, match='reserved'):
        pack({'claim': {'$text': 0}})


def test_invalid_reference_rejected():
    with pytest.raises(ValueError, match='reference'):
        unpack({'packet_format': 'wang_exegesis_interned_packet_v1', 'texts': ['text'], 'data': {'$text': -1}})


def test_actual_wire_size_compact_payload_and_prompt_schema_arguments(tmp_path):
    from backend.pipeline.exegesis_grouping_transport import serialize_request
    payload = {'claims': [{'claim_id': str(i), 'statement': '完整Claim及逐字證據。' * 100} for i in range(22)]}
    for provider in ('gpt', 'claude'):
        request = serialize_request(provider=provider, executable='subscription-cli', model='test-model',
            effort='high', prompt='读全部材料', payload=payload, schema={'type': 'object'}, directory=tmp_path)
        expected = len(request['wire'].encode()) + sum(len(arg.encode()) for arg in request['command'])
        if provider == 'gpt': expected += len(request['schema_text'].encode())
        assert request['size'] == expected
        assert unpack(request['wire_payload']) == payload
        assert len(request['body'].encode()) < len(compact_json(payload).encode())
