import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import exegesis_grouping_source_packet as sp
from backend.pipeline.exegesis_grouping_packet import pack, unpack, compact_json
from backend.pipeline.exegesis_intelligent_grouping_job import REVIEWER, audit_payload_for, model_payload_for

spec = importlib.util.spec_from_file_location('independent_group_review_sp', REVIEWER)
reviewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)

ROW_TEXT = '第六天，~~這句被校對者劃掉~~登山變像印證前面的應許。\n換行後：κοινωνία ☧ 「表情」😀\t製表'
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text x="1">教授畫的結構圖</text></svg>'
FROZEN_SVG_EXCERPT = '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><text>冻结的整份SVG证据</text></svg>'
MARKDOWN = '\n## 母本標題\n\n正文第一段：應許在前。\n\n![結構圖](/web/data/structure.svg)\n\n\n結尾限定：唯有在天上已經決定的。\n'
BLOCKS = ['\n', '## 母本標題\n\n', '正文第一段：應許在前。\n\n', '![結構圖](/web/data/structure.svg)\n\n\n', '結尾限定：唯有在天上已經決定的。\n']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def originals(tmp_path):
    transcript = tmp_path / 'sermon.json'
    transcript.write_text(json.dumps({'metadata': {'title': '元資料標題', 'status': 'reviewed', 'revision': 'meta-r1', 'path': 'meta/path'}, 'script': [
        {'index': 1, 'start_time': 0.0, 'end_time': 5.5, 'text': ROW_TEXT, 'type': 'content', 'user_id': 'u'},
        {'index': 'subtitle-1', 'type': 'subtitle', 'text': '## 編輯字幕標題'},
        {'index': 2, 'start_time': 5.5, 'end_time': 9.0, 'text': '第二段，反駁：有人說磐石是彼得。'}]}, ensure_ascii=False), encoding='utf-8')
    svg = tmp_path / 'structure.svg'
    svg.write_text(SVG, encoding='utf-8')
    notes = tmp_path / 'final.md'
    notes.write_text(MARKDOWN, encoding='utf-8')
    return [
        dict(source_id='S1', source_type='sermon_transcript', source_content_sha256='body1', source_revision=1,
             path=str(transcript), file_sha256=sha(transcript),
             paragraphs=[{'paragraph_key': 'S0001', 'text': '伪造的preparation段落'}]),
        dict(source_id='S2', source_type='notes_manuscript', source_content_sha256='body2', source_revision=1,
             path=str(notes), file_sha256=sha(notes), linked_files=[dict(path=str(svg), file_sha256=sha(svg))]),
    ]


def claims_for(sources):
    rows = []
    for number, source in enumerate(sources + sources):
        fragments = [dict(fragment_id=f'F{number}', revision=1, content_sha256='f' * 64, paragraph_key='S0001',
                          verbatim_excerpt='登山變像' if source['source_id'] == 'S1' else '應許在前')]
        if number == 3:  # frozen fragment holding a whole SVG, with a nested locator that uses provenance-looking keys
            fragments.append(dict(fragment_id='F3V', revision=1, content_sha256='v' * 64, paragraph_key='S0002/V01',
                                  verbatim_excerpt=FROZEN_SVG_EXCERPT, visual_locator={'path': '/web/data/structure.svg', 'revision': 'v2', 'element': 'text[1]'}))
        rows.append(dict(claim_id=f'C{number}', claim_revision=2, claim_content_sha256='c' * 64, source_id=source['source_id'],
            source_revision=1, source_content_sha256=source['source_content_sha256'], source_file_sha256=source['file_sha256'],
            statement=f'主張 {number}：捆綁可用於事', claim_type='explicit', scripture_refs=['太16:19'],
            evidence_steps=[dict(evidence_step_id=f'E{number}', revision=1, content_sha256='e' * 64, statement='證據陳述',
                scripture_refs=['太16:19'], fragments=fragments)],
            ownership=dict(status='reviewed', primary='Matt.16.19', secondary=[{'reference': 'Mark.9.1', 'role': 'parallel', 'path': 'cross-unit'}],
                reason='理由', evidence=[dict(quote='登山變像', location='S0001')], approval_basis='dual_model_consensus',
                review_artifact_sha256='r' * 64, claim_revision=2, claim_content_sha256='c' * 64)))
    return rows


def test_transcript_rows_are_physical_and_verbatim(originals):
    source = sp.canonical_source(originals[0])
    assert source['source_type'] == 'sermon_transcript' and source['source_type_basis'] == 'declared_and_physically_verified'
    assert source['document'] == {'metadata': {'title': '元資料標題', 'status': 'reviewed', 'revision': 'meta-r1', 'path': 'meta/path'}}
    rows = source['rows']
    assert rows[0]['text'] == ROW_TEXT and '~~' in rows[0]['text'] and '😀' in rows[0]['text']
    assert rows[0]['physical_row'] == 1 and rows[0]['body_row'] == 1 and rows[0]['start_time'] == 0.0 and rows[0]['user_id'] == 'u'
    assert rows[1]['editorial_row'] is True and rows[1]['body_row'] is None and rows[1]['text'] == '## 編輯字幕標題'
    assert rows[2]['physical_row'] == 3 and rows[2]['body_row'] == 2
    assert source['text_row_count'] == 3 and source['body_row_count'] == 2
    assert 'paragraphs' not in source and source['discarded_input_fields']['paragraphs']['count'] == 1
    assert '伪造' not in compact_json(source)
    assert sp.text_units(source) == [ROW_TEXT, '## 編輯字幕標題', '第二段，反駁：有人說磐石是彼得。']
    assert '元資料標題' not in sp.text_units(source)


def test_markdown_blocks_lossless_with_meaningful_locators_and_linked_svg_reference_only(originals):
    source = sp.canonical_source(originals[1])
    blocks = source['blocks']
    assert [b['text'] for b in blocks] == BLOCKS and ''.join(b['text'] for b in blocks) == MARKDOWN
    assert [b['physical_block'] for b in blocks] == [1, 2, 3, 4, 5]
    assert blocks[0]['block_ordinal'] is None and blocks[0]['body_block'] is None and blocks[0]['heading'] is False
    assert blocks[1]['heading'] is True and blocks[1]['block_ordinal'] == 1 and blocks[1]['body_block'] is None
    assert [b['block_ordinal'] for b in blocks] == [None, 1, 2, 3, 4]
    assert [b['body_block'] for b in blocks] == [None, None, 1, 2, 3]
    assert source['block_count'] == 4 and source['body_block_count'] == 3
    assert '![結構圖](/web/data/structure.svg)' in [b['text'].strip() for b in blocks]
    # The SVG is SHA-verified but never carried as text: only a reference remains in the audit shape.
    link = source['linked_files'][0]
    assert link == dict(linked_file_ordinal=1, file_name='structure.svg', format='svg_xml', path=originals[1]['linked_files'][0]['path'],
                        file_sha256=originals[1]['linked_files'][0]['file_sha256'], byte_length=len(SVG.encode()), char_length=len(SVG))
    assert '<svg' not in compact_json(source) and 'original_text' not in compact_json(source)
    assert sp.text_units(source) == BLOCKS
    assert sp.linked_texts(source) == [SVG]  # audit-only disk read


@pytest.mark.parametrize('text,expected', [
    ('a\nb\n\nc', ['a\nb\n\n', 'c']), ('a\n\n\n', ['a\n\n\n']), ('\n\n', ['\n\n']), ('\n\n a \n \nb', ['\n\n', ' a \n \n', 'b']),
    ('x\r\n\r\ny\r\n', ['x\r\n\r\n', 'y\r\n']), ('只有一行', ['只有一行'])])
def test_markdown_split_is_byte_exact_for_edge_shapes(text, expected):
    blocks = sp.markdown_blocks(text)
    assert [b['text'] for b in blocks] == expected and ''.join(b['text'] for b in blocks) == text
    assert [b['text'] for b in reviewer.markdown_blocks(text)] == expected


@pytest.mark.parametrize('mutation,pattern', [
    ('inlined', 'pre-inlined'), ('linked_inlined', 'pre-inlined'), ('declared_mismatch', 'does not match physical'),
    ('drift', 'physical source drift'), ('linked_drift', 'physical source drift'), ('linked_not_svg', 'linked file format uncertain'),
    ('not_json_not_md', 'format uncertain'), ('json_without_script', 'schema uncertain'),
    ('row_without_text', 'schema uncertain'), ('bare_rows', 'schema uncertain')])
def test_source_schema_fails_closed(originals, tmp_path, mutation, pattern):
    record = copy.deepcopy(originals[0])
    if mutation == 'inlined':
        record['original_text'] = 'x'
    if mutation == 'linked_inlined':
        record = copy.deepcopy(originals[1]); record['linked_files'][0]['original_text'] = SVG
    if mutation == 'declared_mismatch':
        record['source_type'] = 'notes_manuscript'
    if mutation == 'drift':
        Path(record['path']).write_text('{"script": [{"text": "changed"}]}')
    if mutation == 'linked_drift':
        record = copy.deepcopy(originals[1]); Path(record['linked_files'][0]['path']).write_text('<svg/>')
    if mutation == 'linked_not_svg':
        record = copy.deepcopy(originals[1])
        other = tmp_path / 'notes.txt'; other.write_text('plain text, not a visual original')
        record['linked_files'][0] = dict(path=str(other), file_sha256=sha(other))
    if mutation in {'not_json_not_md', 'json_without_script', 'row_without_text', 'bare_rows'}:
        other = tmp_path / ('x.txt' if mutation == 'not_json_not_md' else 'x.json')
        other.write_text({'not_json_not_md': 'plain text', 'json_without_script': '{"metadata": {}}',
                          'row_without_text': '{"script": [{"index": 1}]}', 'bare_rows': '{"script": ["bare string"]}'}[mutation])
        record.update(path=str(other), file_sha256=sha(other)); record.pop('source_type')
    with pytest.raises(ValueError, match=pattern):
        sp.canonical_source(record)


def test_detection_without_declared_type_is_recorded(originals):
    record = copy.deepcopy(originals[1]); record.pop('source_type')
    source = sp.canonical_source(record)
    assert source['source_type'] == 'notes_manuscript' and source['source_type_basis'] == 'detected_from_physical_structure'


def test_projection_strips_only_listed_provenance_excludes_svg_and_proves_completeness(originals):
    claims = claims_for(originals)
    audit = audit_payload_for(claims, originals, scope_label='Matt', reviewed_unit={'unit_id': 'u', 'evidence': [{'location': 'S0001', 'path': 'kept'}]})
    assert audit['sources'][0]['file_sha256'] == originals[0]['file_sha256'] and audit['sources'][1]['linked_files'][0]['path']
    assert audit['claims'][0]['claim_content_sha256'] == 'c' * 64 and audit['claims'][0]['ownership']['review_artifact_sha256']
    assert audit['claims'][3]['evidence_steps'][0]['fragments'][1]['verbatim_excerpt'] == FROZEN_SVG_EXCERPT  # frozen evidence intact in audit
    model = model_payload_for(audit)
    wire = compact_json(model)
    for key in ('"content_sha256":', '"file_sha256":', '"claim_revision":', '"review_artifact_sha256":', '"discarded_input_fields":', '"source_content_sha256":'):
        assert key not in wire
    assert '<svg' not in wire and '<?xml' not in wire and 'original_text' not in wire
    first = model['claims'][0]
    assert first['statement'] == '主張 0：捆綁可用於事' and first['evidence_steps'][0]['statement'] == '證據陳述'
    assert first['evidence_steps'][0]['fragments'][0] == {'fragment_id': 'F0', 'paragraph_key': 'S0001', 'verbatim_excerpt': '登山變像'}
    assert first['ownership']['primary'] == 'Matt.16.19'
    # Only listed provenance positions are stripped: nested relation/locator objects keep their own path/revision keys.
    assert first['ownership']['secondary'] == [{'reference': 'Mark.9.1', 'role': 'parallel', 'path': 'cross-unit'}]
    visual = model['claims'][3]['evidence_steps'][0]['fragments'][1]
    assert visual['visual_locator'] == {'path': '/web/data/structure.svg', 'revision': 'v2', 'element': 'text[1]'}
    assert visual['paragraph_key'] == 'S0002/V01' and 'verbatim_excerpt' not in visual
    assert visual['verbatim_excerpt_excluded'] == sp.exclusion_marker(FROZEN_SVG_EXCERPT)
    assert model['sources'][0]['document']['metadata'] == {'title': '元資料標題', 'status': 'reviewed', 'revision': 'meta-r1', 'path': 'meta/path'}
    assert model['reviewed_unit'] == {'unit_id': 'u', 'evidence': [{'location': 'S0001', 'path': 'kept'}]} and model['scope_label'] == 'Matt'
    assert model['sources'][0]['rows'][0]['text'] == ROW_TEXT and model['sources'][0]['locator_note']
    assert model['sources'][1]['linked_files'] == [dict(linked_file_ordinal=1, file_name='structure.svg', format='svg_xml',
        byte_length=len(SVG.encode()), char_length=len(SVG), svg_excluded_by_user=True, excluded=sp.SVG_EXCLUDED)]
    assert model['visual_evidence_policy'] == sp.VISUAL_EVIDENCE_POLICY and model['omitted_provenance_fields'] == sp.OMITTED_PROVENANCE
    counts = sp.assert_projection_complete(audit, model)
    assert counts == dict(claims=4, evidence_steps=4, fragments=5, sources=2, source_texts=3 + 5, linked_files=1,
                          source_text_chars=sum(len(r['text']) for r in audit['sources'][0]['rows']) + len(MARKDOWN),
                          svg_excluded_linked_files=1, svg_excluded_fragment_excerpts=1,
                          svg_excluded_chars=len(SVG) + len(FROZEN_SVG_EXCERPT))
    assert reviewer.project_for_model(audit) == model and reviewer.assert_projection_complete(audit, model) == counts


@pytest.mark.parametrize('mutation,pattern', [
    ('summarized_excerpt', 'lost C1 fragment verbatim_excerpt'), ('dropped_block', 'lost S2 blocks'),
    ('dropped_tail_claim', 'lost claim count'), ('dropped_secondary', 'lost C0 ownership secondary'),
    ('dropped_visual_locator', 'lost C3 fragment visual_locator'), ('svg_restored', 'carries SVG verbatim_excerpt'),
    ('added_field', 'added fields at C0'),
    ('linked_text_added', 'lost S2 linked file reference'), ('context_changed', 'lost context reviewed_unit')])
def test_completeness_proof_detects_loss_addition_and_svg_leaks(originals, mutation, pattern):
    claims = claims_for(originals)
    audit = audit_payload_for(claims, originals, scope_label='Matt', reviewed_unit={'unit_id': 'u'})
    broken = copy.deepcopy(model_payload_for(audit))
    if mutation == 'summarized_excerpt': broken['claims'][1]['evidence_steps'][0]['fragments'][0]['verbatim_excerpt'] = '摘要'
    if mutation == 'dropped_block': broken['sources'][1]['blocks'].pop()
    if mutation == 'dropped_tail_claim': broken['claims'].pop()
    if mutation == 'dropped_secondary': broken['claims'][0]['ownership'].pop('secondary')
    if mutation == 'dropped_visual_locator': broken['claims'][3]['evidence_steps'][0]['fragments'][1].pop('visual_locator')
    if mutation == 'svg_restored': broken['claims'][3]['evidence_steps'][0]['fragments'][1]['verbatim_excerpt'] = FROZEN_SVG_EXCERPT
    if mutation == 'added_field': broken['claims'][0]['model_hint'] = 'invented'
    if mutation == 'linked_text_added': broken['sources'][1]['linked_files'][0]['original_text'] = SVG
    if mutation == 'context_changed': broken['reviewed_unit'] = {'unit_id': 'other'}
    with pytest.raises(ValueError, match=pattern):
        sp.assert_projection_complete(audit, broken)
    with pytest.raises(ValueError):
        reviewer.assert_projection_complete(audit, broken)


@pytest.mark.parametrize('location', ['scope_label', 'reviewed_unit', 'statement', 'ownership_reason'])
def test_svg_cannot_be_smuggled_through_any_other_field(originals, location):
    """Fields the projection copies verbatim are scanned: SVG anywhere fails closed instead of being sent."""
    claims = claims_for(originals)
    audit = audit_payload_for(claims, originals, scope_label='Matt', reviewed_unit={'unit_id': 'u'})
    if location == 'scope_label': audit['scope_label'] = SVG
    if location == 'reviewed_unit': audit['reviewed_unit'] = {'unit_id': 'u', 'rationale': '見圖 ' + SVG}
    if location == 'statement': audit['claims'][0]['statement'] = '主張 ' + SVG
    if location == 'ownership_reason': audit['claims'][0]['ownership']['reason'] = SVG
    with pytest.raises(ValueError, match='SVG/XML source text in model input'):
        model_payload_for(audit)
    with pytest.raises(ValueError, match='SVG/XML source text'):
        reviewer.project_for_model(audit)


def test_every_source_text_appears_once_in_the_actual_wire_and_svg_never(originals):
    claims = claims_for(originals)
    model = model_payload_for(audit_payload_for(claims, originals, scope_label='Matt'))
    wire = compact_json(pack(model))
    assert unpack(pack(model)) == model
    for text in [ROW_TEXT, '第二段，反駁：有人說磐石是彼得。', *BLOCKS]:
        assert sp.text_occurrences(wire, text) == 1, text
    assert sp.text_occurrences(wire, '伪造的preparation段落') == 0
    assert sp.text_occurrences(wire, SVG) == 0 and sp.text_occurrences(wire, FROZEN_SVG_EXCERPT) == 0
    assert '<svg' not in wire and '<?xml' not in wire


def test_claims_without_physical_source_fail(originals):
    claims = claims_for(originals)
    claims[0]['source_id'] = 'S9'
    with pytest.raises(ValueError, match='without physical originals'):
        audit_payload_for(claims, originals, scope_label='Matt')


def test_independent_reviewer_parity_roundtrip_and_drift(originals, tmp_path):
    claims = claims_for(originals)
    audit = audit_payload_for(claims, originals, scope_label='Matt')
    model = model_payload_for(audit)
    assert reviewer.project_for_model(audit) == model and reviewer.digest(model) == sha256_json(model)
    reviewer.assert_projection_complete(audit, model)
    fresh, strings, physical = reviewer.original_sources(audit['sources'])
    assert [f['rows'] for f in fresh if 'rows' in f] == [audit['sources'][0]['rows']]
    assert fresh[1]['linked_files'] == audit['sources'][1]['linked_files'] and 'original_text' not in json.dumps(fresh)
    assert strings['S1'] == sp.text_units(audit['sources'][0]) and strings['S2'] == BLOCKS
    assert physical['S2'] == BLOCKS + [SVG]  # disk-read visual text verifies quotes only; never a payload
    forged = copy.deepcopy(audit['sources'])
    forged[0]['rows'][0]['text'] = '伪造正文'
    with pytest.raises(ValueError, match='differs from physical original'):
        reviewer.original_sources(forged)
    forged = copy.deepcopy(audit['sources'])
    forged[1]['linked_files'][0]['original_text'] = SVG  # a legitimate audit object never inlines the SVG
    with pytest.raises(ValueError, match='inlined linked file text is not accepted'):
        reviewer.original_sources(forged)
    forged = copy.deepcopy(audit['sources'])
    forged[1]['linked_files'][0]['char_length'] = 1
    with pytest.raises(ValueError, match='linked file references differ'):
        reviewer.original_sources(forged)
    with pytest.raises(ValueError, match='not accepted'):
        reviewer.original_sources([dict(audit['sources'][0], original_text='x')])
    payload = dict(model, proposal={'units': [{'unit_id': 'u', 'claim_ids': [c['claim_id'] for c in claims]}]})
    packet = reviewer.compact_packet(payload)
    assert packet is not payload and reviewer.expand(packet) == payload == unpack(packet)
    corrupted = copy.deepcopy(packet)
    corrupted['texts'].reverse()
    assert reviewer.expand(corrupted) != payload
    for provider in ('gpt', 'claude'):
        serialized = reviewer.serialize_review_request(provider=provider, model='m', effort='high', payload=payload, cli='cli', root=tmp_path)
        expected = len(serialized['wire'].encode()) + sum(len(a.encode()) for a in serialized['command'])
        if provider == 'gpt':
            expected += len(serialized['schema_text'].encode())
            assert serialized['prompt_carried_in'] == 'wire' and serialized['schema_carried_in'] == 'file_argument'
        else:
            assert serialized['schema_text'] in serialized['command'] and serialized['wire_prompt'] in serialized['command']
            assert serialized['prompt_carried_in'] == 'argv' and serialized['schema_carried_in'] == 'argv'
        assert serialized['size'] == expected and serialized['roundtrip_verified'] and serialized['interned']
        assert serialized['size'] >= serialized['wire_payload_bytes'] + serialized['prompt_bytes'] + serialized['schema_bytes']
        assert serialized['compact_uninterned_payload_bytes'] >= serialized['wire_payload_bytes']
        assert serialized['pretty_payload_bytes'] > serialized['compact_uninterned_payload_bytes']
        assert '<svg' not in serialized['wire'] and serialized['svg_source_in_model_input'] is False
    with pytest.raises(ValueError, match='SVG/XML source text'):
        reviewer.serialize_review_request(provider='claude', model='m', effort='high', payload=dict(payload, proposal={'note': SVG}), cli='cli', root=tmp_path)
    # Physical SVG drift is intercepted by the reviewer's own disk read, even though the text is never sent.
    Path(originals[1]['linked_files'][0]['path']).write_text('<svg><text>changed</text></svg>')
    with pytest.raises(ValueError, match='physical source drift'):
        reviewer.original_sources(audit['sources'])
