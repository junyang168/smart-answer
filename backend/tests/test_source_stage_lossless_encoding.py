"""Source/Claim actions share the existing codec without resetting once budgets."""
import json
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline.source_reconciliation_runtime import model_adapter
from backend.pipeline.viewpoint_resolution_runtime import response_adapter


def payload():
    return {'records': [{'collection': 'claims', 'object_id': f'C{i}',
                         'payload': {'claim_id': f'C{i}', 'statement': '完整原文',
                                     'review_status': 'approved', 'evidence_step_ids': [f'E{i}']}}
                        for i in range(100)]}


@pytest.mark.parametrize('stage', r.STAGES)
def test_unreserved_stage_can_select_lossless_codec(tmp_path, stage):
    packet = payload()
    adapter = model_adapter('final_review' if stage == 'final_review_input_repair' else stage)
    original = response_adapter(adapter)
    smaller = original.shared_keys_copy()
    assert smaller.request_bytes(packet) < original.request_bytes(packet)
    adapter.max_request_bytes = smaller.request_bytes(packet)
    chosen = r.stage_adapter(adapter, packet, tmp_path/(stage+'.raw.json'))
    proof = chosen.request_encoding_proof(packet)
    assert proof['exact_roundtrip_verified'] is True
    assert proof['semantic_payload_sha256'] == proof['restored_payload_sha256'] == r.sha256_json(packet)
    assert chosen.generation_config_sha256 == smaller.generation_config_sha256
    assert chosen.prompt_sha256 == original.prompt_sha256


@pytest.mark.parametrize('suffix', ['.reservation.json', '.call-intent.json', '.request-encoding.json'])
def test_unknown_or_frozen_stage_cannot_reencode(tmp_path, suffix):
    adapter = model_adapter('correction')
    adapter.max_request_bytes = 1
    (tmp_path/('correction'+suffix)).touch()
    chosen = r.stage_adapter(adapter, payload(), tmp_path/'correction.raw.json')
    assert chosen.generation_config_sha256 == response_adapter(adapter).generation_config_sha256
    assert not chosen._shared_key_payload


@pytest.mark.parametrize('program', [False, True])
def test_saved_wire_is_exact_even_if_current_limit_changes(tmp_path, program):
    adapter = model_adapter('correction')
    original = response_adapter(adapter) if program else adapter
    encoded = original.shared_keys_copy()
    saved = {k: getattr(encoded, k) for k in
             ('model_id', 'backend', 'prompt_sha256', 'generation_config_sha256')}
    adapter.max_request_bytes = 1
    chosen = r.stage_adapter(adapter, payload(), tmp_path/'correction.raw.json', saved=saved)
    assert chosen.generation_config_sha256 == encoded.generation_config_sha256
    assert chosen.request_encoding_proof(payload()) == encoded.request_encoding_proof(payload())
    saved['generation_config_sha256'] = 'foreign'
    chosen = r.stage_adapter(adapter, payload(), tmp_path/'correction.raw.json', saved=saved)
    assert chosen.generation_config_sha256 != saved['generation_config_sha256']


def test_under_limit_preserves_original_wire(tmp_path):
    adapter = model_adapter('proposal')
    original = response_adapter(adapter)
    chosen = r.stage_adapter(adapter, payload(), tmp_path/'proposal.raw.json')
    assert chosen.generation_config_sha256 == original.generation_config_sha256


def test_unreserved_source_stage_uses_existing_string_piece_codec_without_loss(tmp_path):
    packet = {'rows': [{'statement': 'same long exact prefix ' * 12 + str(i) + ' same exact suffix' * 12,
                        'id': f'C{i}', 'values': [i, None, False, {'nested': [i, 'retained']}]}
                       for i in range(80)]}
    adapter = model_adapter('proposal')
    original = response_adapter(adapter)
    smaller = original.shared_shapes_v3_copy()
    assert smaller.request_bytes(packet) < original.shared_shapes_v2_copy().request_bytes(packet)
    adapter.max_request_bytes = smaller.request_bytes(packet)
    chosen = r.stage_adapter(adapter, packet, tmp_path/'proposal.raw.json')
    assert chosen.request_bytes(packet) <= adapter.max_request_bytes
    proof = chosen.request_encoding_proof(packet)
    assert proof['exact_roundtrip_verified']
    assert proof['semantic_payload_sha256'] == proof['restored_payload_sha256'] == r.sha256_json(packet)
    assert chosen.prompt_sha256 == original.prompt_sha256


@pytest.mark.skipif(os.getenv('WKP_G094_ENCODING_READONLY') != '1', reason='explicit saved read-only acceptance')
def test_saved_g094_correction_exact_roundtrip_no_call_or_write():
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83/claim-issues/670ae7487f38ca391a0b3ca9c1a7a5e42814a32d14da50ff4e951299559bfdd0/scope-amendment-once/action/CVPA-949e353397967feb493ede9b46da08843748930954cdd9338ca28088ca1480c5')
    frozen = r.body(r.read(root/'scoped-materials.json'))
    raw = r.validate_raw(root, 'proposal', r.body(r.read(root/'proposal.packet.json')))
    initial_packet = r.body(r.read(root/'initial_review.packet.json'))
    initial = r.validate_raw(root, 'initial_review', initial_packet)
    r.contract.guard_files(frozen)
    proposed = r.effective_proposal(frozen, raw['response'])
    packet = {**r.build_proposal_packet(frozen), 'initial_proposal': proposed.model_dump(),
              'Opus_review': initial['response'], 'correction_round': 1, 'another_correction_allowed': False,
              'unresolved_proposal_review': initial_packet['unresolved_proposal_review']}
    packet = json.loads(r.canonical_json(packet))
    adapter = model_adapter('correction', issue=True, evidence_eligibility=True)
    adapter.max_request_bytes = 600000
    assert response_adapter(adapter).request_bytes(packet) > 600000
    chosen = r.stage_adapter(adapter, packet, root/'correction.raw.json')
    assert chosen.request_bytes(packet) == 572729
    proof = chosen.request_encoding_proof(packet)
    assert proof['exact_roundtrip_verified']
    assert proof['semantic_payload_sha256'] == proof['restored_payload_sha256'] == r.sha256_json(packet)
    assert not list(root.glob('correction*'))
