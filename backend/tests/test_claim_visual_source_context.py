"""Ordinary Claim review can read attested SVGs without weakening source edits."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import source_reconciliation as source
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_projection import project_script


@pytest.fixture
def visual_source(tmp_path):
    rows = [{'index': 1, 'text': '教授的原話。<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="2">基督的律法</text></svg>'},
            {'index': 2, 'text': '圖以外的另一段原話。'}]
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(rows, ensure_ascii=False))
    projection = project_script(rows)
    doc = {'source_type': 'sermon_transcript', 'source_path': str(path),
           'source_body_sha256': projection.body_sha256,
           'source_visual_sha256': projection.visual_content_sha256,
           'visual_sources': [b.descriptor() for b in projection.visual_blocks],
           'visual_source_attestations': [{'locator': b.locator, 'raw_sha256': b.raw_sha256,
                'attestation': 'professor_displayed_or_drawn_visual_source'} for b in projection.visual_blocks]}
    return path, doc, projection


def test_ordinary_context_keeps_complete_svg_and_exact_spoken_coordinates(visual_source):
    path, doc, projection = visual_source
    result = source.source_snapshot(binding(path), source=doc, visual_context=True)
    assert result['visual_sources'] == doc['visual_sources']
    assert result['visual_sources'][0]['raw_svg'] == projection.visual_blocks[0].raw_svg
    assert result['paragraphs']['S0001']['text'] == projection.spoken_rows[0]['text']
    assert result['paragraphs']['S0002']['text'] == '圖以外的另一段原話。'
    assert result['body_sha256'] == projection.body_sha256
    assert result['visual_content_sha256'] == projection.visual_content_sha256
    assert '基督的律法' not in result['paragraphs']['S0001']['text']


@pytest.mark.parametrize('mutation', ['missing_attestation', 'wrong_attestation', 'visual_hash', 'body_hash', 'descriptor'])
def test_svg_context_refuses_missing_or_drifted_provenance(visual_source, mutation):
    path, original, _ = visual_source
    doc = deepcopy(original)
    if mutation == 'missing_attestation': doc.pop('visual_source_attestations')
    elif mutation == 'wrong_attestation': doc['visual_source_attestations'][0]['raw_sha256'] = '0' * 64
    elif mutation == 'visual_hash': doc['source_visual_sha256'] = '0' * 64
    elif mutation == 'body_hash': doc['source_body_sha256'] = '0' * 64
    else: doc['visual_sources'][0]['raw_svg'] = '<svg/>'
    with pytest.raises(ValueError):
        source.source_snapshot(binding(path), source=doc, visual_context=True)


def test_source_change_still_requires_its_separate_visual_adapter(visual_source):
    path, doc, _ = visual_source
    with pytest.raises(ValueError, match='separately scoped visual adapter'):
        source.source_snapshot(binding(path), source=doc)


def test_text_only_snapshot_contract_is_unchanged(tmp_path):
    path = tmp_path / 'text.json'
    path.write_text(json.dumps([{'index': 1, 'text': '原話。'}]))
    assert source.source_snapshot(binding(path)) == source.source_snapshot(binding(path), visual_context=True)


def test_svg_edit_cannot_reuse_the_old_physical_binding(visual_source):
    path, doc, _ = visual_source
    original = binding(path)
    path.write_text(path.read_text().replace('基督的律法', '其他文字'))
    with pytest.raises(ValueError):
        source.source_snapshot(original, source=doc, visual_context=True)


def test_legacy_inline_descriptor_uses_the_existing_resolver_default(visual_source):
    path, doc, _ = visual_source
    doc['visual_sources'][0].pop('binding_kind')
    result = source.source_snapshot(binding(path), source=doc, visual_context=True)
    assert result['visual_sources'][0]['binding_kind'] == 'inline_svg'


def test_update_authority_reaches_the_attested_visual_adapter(visual_source, tmp_path):
    from backend.pipeline import claim_management as cm
    path, doc, _ = visual_source
    auth = tmp_path / 'authority.json'
    request = cm.Request(action_id='visual-context', operation='Update', allowed_operations=['Update'],
        target_keys=['claims/C'], source_bindings={'S': binding(path)}, policy=binding(cm.DEFAULT_POLICY),
        authorization={'path': str(auth), 'file_sha256': '0' * 64})
    auth.write_text(json.dumps({'schema_version': cm.VERSION, 'authorized_by': 'owner',
        'authorization_quote': 'Refresh this Claim including its source pictures.', 'human_content_approval': False,
        'request': request.model_dump(mode='json', exclude={'authorization', 'policy'}),
        'policy': request.policy.model_dump(mode='json')}))
    request.authorization = source.FileBinding.model_validate(binding(auth))
    _, snapshots = cm.load_authority(request, source_documents={'S': doc})
    assert snapshots['S']['visual_sources'] == doc['visual_sources']
