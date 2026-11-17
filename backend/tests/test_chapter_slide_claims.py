import copy
import json

import pytest

from backend.pipeline import chapter_exposition, chapter_slide_claims as hints, chapter_slides
from backend.tests.test_chapter_slides import CHAPTER, FakeClient, KEPT, _decided, slide


def context_for(root):
    path = chapter_exposition.record_path(root, CHAPTER.passage, 'L')
    decisions = json.loads(path.read_text())
    return {'passage': CHAPTER.passage, 'lecture': 'L',
            'decisions_sha256': chapter_exposition.file_sha256(path),
            'transcript_sha256': decisions.get('transcript_sha256'),
            'claims': [{'claim_id': 'C1', 'statement': '答案', 'evidence_steps': []}], 'excluded': []}


def test_same_frozen_hints_reach_author_reviewer_and_revision(tmp_path):
    _decided(tmp_path)
    context = context_for(tmp_path)
    author = FakeClient([{'slides': [slide('S0001')]}])
    chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path, client=author, force=True, claim_context=context)
    review = {'grade': 'B', 'blocking': True, 'findings': [
        {'slide': 'S0001', 'kind': '完整', 'problem': '漏結論', 'fix': '加結論'}],
        'claim_checks': [{'claim_id': 'C1', 'status': 'missing', 'slides': ['S0001'], 'reason': '漏結論'}]}
    reviewer = FakeClient([review])
    chapter_slides.review(CHAPTER, 'L', data_base_dir=tmp_path, client=reviewer)
    reviser = FakeClient([{'slides': [slide('S0001')]}])
    chapter_slides.revise(CHAPTER, 'L', data_base_dir=tmp_path, client=reviser)
    for client in [author, reviewer, reviser]:
        assert 'C1' in client.prompts[0]
        assert '不把未保留片段或別堂的內容補入' in client.prompts[0]
    assert chapter_slides.load_record(tmp_path, CHAPTER.passage, 'L')['claim_context'] == context


def test_other_decisions_context_fails_before_model_call(tmp_path):
    _decided(tmp_path)
    context = context_for(tmp_path)
    context['decisions_sha256'] = 'different'
    client = FakeClient([])
    with pytest.raises(ValueError, match='does not match'):
        chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path, client=client, force=True, claim_context=context)
    assert client.prompts == []


def test_review_cannot_omit_duplicate_or_invent_claims_or_hide_missing():
    context = {'claims': [{'claim_id': 'C1'}, {'claim_id': 'C2'}]}
    checks = [{'claim_id': c, 'status': 'covered', 'slides': ['S0001'], 'reason': '呈現'} for c in ['C1', 'C2']]
    answer = {'claim_checks': checks, 'blocking': False, 'findings': []}
    hints.check_review(answer, context, [{'first': 'S0001'}])
    for ids in [['C1'], ['C1', 'C1'], ['C1', 'unknown']]:
        bad = copy.deepcopy(answer)
        bad['claim_checks'] = [dict(checks[0], claim_id=c) for c in ids]
        with pytest.raises(ValueError):
            hints.check_review(bad, context, [{'first': 'S0001'}])
    bad = copy.deepcopy(answer); bad['claim_checks'][0]['status'] = 'missing'
    with pytest.raises(ValueError, match='blocking'):
        hints.check_review(bad, context, [{'first': 'S0001'}])


def test_compiler_rejects_foreign_and_unverified_fragments(tmp_path, monkeypatch):
    from backend.pipeline.original_audio_index import Sermons
    source = tmp_path / 'source.json'; source.write_text('physical source')
    pieces = KEPT
    monkeypatch.setattr(Sermons, 'transcript_path', lambda *a: source)
    monkeypatch.setattr(Sermons, 'load', lambda *a: {})
    monkeypatch.setattr(chapter_exposition, 'cut', lambda *a: pieces)
    path = chapter_exposition.record_path(tmp_path, CHAPTER.passage, 'L')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'pieces': pieces, 'decisions': [{'id': p['id'], 'keep': True} for p in pieces],
                               'transcript_sha256': chapter_exposition.file_sha256(source)}))
    records = {'source_documents': [{'source_id': 'SRC', 'transcript_id': 'L'}],
               'claims': [{'claim_id': 'C1', 'statement': '主張', 'occurrences': [{'transcript_id': 'L'}],
                           'evidence_step_ids': ['E1']}],
               'evidence_steps': [{'evidence_step_id': 'E1', 'source_fragment_ids': ['F1']}],
               'source_fragments': [{'fragment_id': 'F1', 'source_id': 'SRC', 'verbatim_excerpt': '答案在這裡。'}]}
    class Store:
        def list_records(self, name):
            return records[name]
    result = hints.compile_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, store=Store())
    assert result['claims'][0]['evidence_steps'][0]['anchors'][0]['sentences'] == ['S0003']
    records['source_fragments'][0]['source_id'] = 'OTHER'
    with pytest.raises(ValueError, match='Cross-sermon'):
        hints.compile_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, store=Store())
    records['source_fragments'][0]['source_id'] = 'SRC'
    records['source_fragments'][0]['verbatim_excerpt'] = '未講過的話'
    with pytest.raises(ValueError, match='Excerpt missing'):
        hints.compile_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, store=Store())
    source.write_text('source changed')
    with pytest.raises(ValueError, match='Actual transcript'):
        hints.compile_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, store=Store())


def test_rejected_claim_trial_revision_is_preserved_and_not_called_again(tmp_path):
    _decided(tmp_path)
    context = context_for(tmp_path)
    chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path,
                         client=FakeClient([{'slides': [slide('S0001')]}]), force=True, claim_context=context)
    verdict = {'grade': 'B', 'blocking': False,
               'findings': [{'slide': 'S0001', 'kind': '跟得上', 'problem': '故事', 'fix': '加要點'}],
               'claim_checks': [{'claim_id': 'C1', 'status': 'covered', 'slides': ['S0001'], 'reason': '完整'}]}
    chapter_slides.review(CHAPTER, 'L', data_base_dir=tmp_path, client=FakeClient([verdict]))
    client = FakeClient([{'slides': [slide('S0001', points=('一', '二', '三', '四', '五', '六'))]}])
    assert chapter_slides.revise(CHAPTER, 'L', data_base_dir=tmp_path, client=client)['status'] == 'rejected'
    before = chapter_slides.record_path(tmp_path, CHAPTER.passage, 'L').read_bytes()
    unused = FakeClient([])
    assert chapter_slides.revise(CHAPTER, 'L', data_base_dir=tmp_path, client=unused)['status'] == 'revision_attempt_used'
    assert not unused.prompts
    assert chapter_slides.record_path(tmp_path, CHAPTER.passage, 'L').read_bytes() == before


def test_source_ranges_preserve_roles_and_gaps_in_retained_transcript():
    omitted = {"id": "U-omitted", "start": 210, "end": 220, "text": "省略。"}
    decisions = {
        "pieces": [KEPT[0], omitted, KEPT[1]],
        "decisions": [{"id": p["id"], "keep": True} for p in KEPT],
    }
    context = {
        "claims": [{
            "claim_id": "C1",
            "evidence_steps": [
                {"step_type": "question", "anchors": [{"sentences": ["S0001"]}]},
                {"step_type": "reasoning", "anchors": [{"sentences": ["S0002", "S0003", "S0004"]}]},
            ],
        }],
    }
    expected = [{
        "claim_id": "C1",
        "ranges": [
            {"first": "S0001", "last": "S0001", "evidence_roles": ["question"]},
            {"first": "S0002", "last": "S0002", "evidence_roles": ["reasoning"]},
            {"first": "S0003", "last": "S0004", "evidence_roles": ["reasoning"]},
        ],
    }]
    assert hints.claim_source_ranges(context, decisions) == expected
    prompt = chapter_slides.user_prompt("L", decisions, context)
    supplied = json.loads(prompt[prompt.index('{"claims"'):])
    assert supplied["claim_source_ranges"] == expected
    assert "claim_source_ranges" not in context

    context["claim_source_ranges"] = [{"claim_id": "C1", "ranges": []}]
    with pytest.raises(ValueError, match="differ from retained evidence"):
        chapter_slides.user_prompt("L", decisions, context)


STRUCTURE = """# 測試
## G01 結構
- A:CL1 蓋子  〔頂層；L1-S0001,L1-S0002；講出 L1-S0002「答案在這裡」〕
  - A:CL2 論點  〔supports A:CL1；L1-S0001；講出 L1-S0001「這是問題嗎」〕
  - A:CL3 別講才講的  〔supports A:CL1；L2-S0005；講出 L2-S0005「別處」〕
## G02 別講
- B:CL1 不相干  〔頂層；L2-S0001；講出 L2-S0001「無關」〕
"""


def test_outline_claims_are_placed_on_retained_sentences_without_a_store(tmp_path):
    _decided(tmp_path)
    structure = tmp_path / 'structure.md'
    structure.write_text(STRUCTURE)
    context = hints.compile_outline_context(CHAPTER.passage, 'L', 1, data_base_dir=tmp_path, structure_path=structure)
    assert context['schema_version'] == hints.OUTLINE_SCHEMA
    assert [g['group_id'] for g in context['groups']] == ['G01']
    stated = {c['claim_id']: c['stated'] for c in context['claims']}
    assert stated == {'A:CL1': 'S0003', 'A:CL2': 'S0002', 'A:CL3': '其他講（L2-S0005）'}
    assert {c['claim_id']: c['parent'] for c in context['claims']}['A:CL2'] == 'A:CL1'
    client = FakeClient([{'slides': [slide('S0001', points=({'sentence': 'S0002', 'text': '一', 'source': 'A:CL2'},
                                                            {'sentence': 'S0003', 'text': '二', 'source': 'A:CL1'}))]}])
    chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path, client=client, force=True, claim_context=context)
    assert '不另做本堂地圖' in client.prompts[0] and 'A:CL1' in client.prompts[0]


def test_outline_claim_whose_sentence_was_not_kept_is_excluded(tmp_path):
    _decided(tmp_path)
    structure = tmp_path / 'structure.md'
    structure.write_text(STRUCTURE.replace('答案在這裡', '沒有保留的話'))
    context = hints.compile_outline_context(CHAPTER.passage, 'L', 1, data_base_dir=tmp_path, structure_path=structure)
    assert context['excluded'] == [{'claim_id': 'A:CL1', 'reason': 'stated sentence not in retained transcript'}]


def test_outline_has_no_lecture_map_slide(tmp_path):
    """負責人 2026-11-16：本堂地圖是編輯整理、他沒有講；聆聽頁按經文段落組織，與講課時間不重合。"""
    _decided(tmp_path)
    record = {'claim_context': {'schema_version': hints.OUTLINE_SCHEMA},
              'slides': [dict(slide('S0001', points=({'sentence': 'S0001', 'text': '一'}, {'sentence': 'S0002', 'text': '二'})), last='S0002')]}
    decisions = json.loads(chapter_exposition.record_path(tmp_path, CHAPTER.passage, 'L').read_text())
    _, timed = chapter_slides.placed(decisions, record)
    assert 'overview' not in timed[0] and timed[0]['points'][0]['at'] != timed[0]['points'][1]['at']

def creation_run(root, complete=True, quote_e2='我們看以賽亞書六十一章一節。'):
    """A minimal Claim-creation output folder: one group, cover CL0001 and point CL0002."""
    root.mkdir(parents=True)
    frag = lambda fid, quote: {'fragment_id': fid, 'verbatim_excerpt': quote}
    package = {'complete': complete,
        'claims': [{'claim_id': 'DK-x-CL0001', 'title': '蓋子', 'evidence_step_ids': ['E1'], 'opposed_position_ids': ['P1']},
                   {'claim_id': 'DK-x-CL0002', 'title': '論點', 'evidence_step_ids': ['E2'], 'opposed_position_ids': []}],
        'evidence_steps': [{'evidence_step_id': 'E1', 'statement': '他的回答', 'step_type': 'answer', 'source_fragment_ids': ['F1']},
                           {'evidence_step_id': 'E2', 'statement': '引經', 'step_type': 'scripture_evidence', 'source_fragment_ids': ['F2']}],
        'observations': [{'observation_id': 'O1', 'statement': '經文事實', 'observation_type': 'scripture_text', 'source_fragment_ids': ['F3']}],
        'position_nodes': [{'position_id': 'P1', 'title': '反方', 'source_fragment_ids': ['F4']}],
        'knowledge_relations': [{'from_id': 'O1', 'to_id': 'E2', 'relation_type': 'supports'}],
        'source_fragments': [frag('F1', '答案在這裡。'), frag('F2', quote_e2), frag('F3', '耶穌上了山。'), frag('F4', '這是問題嗎？')]}
    (root / 'candidate.json').write_text(json.dumps(package, ensure_ascii=False))
    anchor = lambda q: {'conclusion_anchor': {'verbatim_excerpt': q}}
    (root / 'claim-candidates.json').write_text(json.dumps({'CL0001': anchor('答案在這裡。'), 'CL0002': anchor('沒有保留的話')}, ensure_ascii=False))
    (root / 'groups.json').write_text(json.dumps([{'title': '第一部分'}], ensure_ascii=False))
    return root


def test_creation_runs_become_an_outline_with_their_evidence_on_retained_sentences(tmp_path):
    _decided(tmp_path)
    context = hints.compile_package_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, runs=[creation_run(tmp_path / 'run')])
    assert context['schema_version'] == hints.OUTLINE_SCHEMA and context['groups'] == [{'group_id': 'G01', 'title': '第一部分'}]
    cover, point = context['claims']
    assert (cover['parent'], point['parent']) == (None, 'G01-CL0001')
    assert cover['stated'] == 'S0003' and point['stated'] == '本堂未保留'
    # The cover keeps its Evidence and the view it refutes; the point gets the Observation behind its Evidence.
    assert [(e['evidence_step_id'], e['step_type'], e['anchors'][0]['sentences']) for e in cover['evidence_steps']] == [
        ('E1', 'answer', ['S0003']), ('P1', 'opposed_position', ['S0002'])]
    assert [e['evidence_step_id'] for e in point['evidence_steps']] == ['E2', 'O1']
    client = FakeClient([{'slides': [slide('S0001', points=({'sentence': 'S0002', 'text': '反方', 'source': 'P1'},
                                                            {'sentence': 'S0004', 'text': '引經', 'source': 'E2'}))]}])
    chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path, client=client, force=True, claim_context=context)
    assert '不另做本堂地圖' in client.prompts[0] and '經文事實' in client.prompts[0]


def test_unplaceable_fragment_is_recorded_and_incomplete_run_is_refused(tmp_path):
    _decided(tmp_path)
    context = hints.compile_package_context(CHAPTER.passage, 'L', data_base_dir=tmp_path,
                                            runs=[creation_run(tmp_path / 'a', quote_e2='不在逐字稿裡的話')])
    assert context['excluded'] == [{'claim_id': 'G01-CL0002', 'object_id': 'F2', 'reason': 'excerpt missing or ambiguous'}]
    with pytest.raises(ValueError, match='not complete'):
        hints.compile_package_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, runs=[creation_run(tmp_path / 'b', complete=False)])


def test_only_verses_he_reads_aloud_must_be_on_a_slide(tmp_path):
    cuv = tmp_path / 'cuv' / 'MAT'
    cuv.mkdir(parents=True)
    (cuv / '5.json').write_text(json.dumps({'3': '虛心的人有福了！因為天國是他們的。', '4': '哀慟的人有福了！因為他們必得安慰。'}, ensure_ascii=False))
    lines = ['五章三節說：虛心的人有福了，因為天國是他們的。', '第五章三節到第十二節講的是彌賽亞的身份。']
    # 5:3 is read aloud, 5:4 only falls inside a range he names; a cross-chapter range is him giving scope.
    assert hints.evidence_scriptures(['太5:3-4', '太5:13-7:29', '路15'], lines, tmp_path / 'cuv') == (['mat-5-3'], ['路15'])
    context = {'claims': [{'claim_id': 'G01-CL0001', 'evidence_steps': [{'scriptures': ['mat-5-3']}]}]}
    assert hints.evidence_scripture_gaps(context, [{'scriptures': [{'slug': 'mat-5-3-10'}]}]) == []
    assert hints.evidence_scripture_gaps(context, [{'scriptures': [{'slug': 'mat-5-4'}]}]) == [
        'G01-CL0001 的 Evidence 引用 mat-5-3，投影片沒有顯示']


def test_review_that_finds_slides_contradicting_evidence_must_block():
    context = {'claims': [{'claim_id': 'C1'}]}
    slides = [{'first': 'S0001'}]
    check = {'claim_id': 'C1', 'status': 'contradicts', 'slides': ['S0001'], 'reason': '把反方寫成他的主張'}
    with pytest.raises(ValueError, match='blocking fidelity'):
        hints.check_review({'claim_checks': [check], 'blocking': False, 'findings': []}, context, slides)
    hints.check_review({'claim_checks': [check], 'blocking': True,
                        'findings': [{'kind': '忠實', 'slide': 'S0001', 'problem': 'p', 'fix': 'f'}]}, context, slides)


def test_points_are_timed_by_the_argument_step_they_show_and_unrelated_slides_are_sent_back(tmp_path):
    """負責人 2026-11-16：他講要點 A、投影片亮著 B，或他論證 X、投影片顯示不相關的，都不行。"""
    _decided(tmp_path)
    context = hints.compile_package_context(CHAPTER.passage, 'L', data_base_dir=tmp_path, runs=[creation_run(tmp_path / 'run')])
    # A point bound outside the step it shows is sent back; inside, it keeps the sentence the model chose.
    outside = [dict(slide('S0001', points=({'sentence': 'S0001', 'text': '引經', 'source': 'E2'},
                                           {'sentence': 'S0002', 'text': '反方', 'source': 'P1'})), last='S0004')]
    assert hints.point_spans(context, outside) == ['S0001: 要點「引經」綁在 S0001，但它的 source E2 在 S0004–S0004']
    assert [p['sentence'] for p in hints.retime([dict(outside[0], points=outside[0]['points'][::-1])], context)[0]['points']] == ['S0001', 'S0002']
    # Two slides: the second covers his answer (E1, Claim CL0001) but carries only the other Claim's point.
    first = dict(slide('S0001', points=({'sentence': 'S0001', 'text': '經文事實', 'source': 'O1'},
                                        {'sentence': 'S0002', 'text': '反方', 'source': 'P1'})), last='S0002')
    second = dict(slide('S0003', points=({'sentence': 'S0004', 'text': '引經', 'source': 'E2'},
                                         {'sentence': 'S0004', 'text': '插話', 'source': ''})), last='S0004')
    problems = hints.argument_alignment(context, [first, second])
    assert len(problems) == 1 and 'S0003' in problems[0] and 'G01-CL0001' in problems[0]
    # decide sends the problems back once with the previous answer, and keeps the corrected one.
    fixed = dict(second, points=[{'sentence': 'S0003', 'text': '回答', 'source': 'E1'}, {'sentence': 'S0004', 'text': '引經', 'source': 'E2'}])
    client = FakeClient([{'slides': [first, second]}, {'slides': [first, fixed]}])
    result = chapter_slides.decide(CHAPTER, 'L', data_base_dir=tmp_path, client=client, force=True, claim_context=context)
    assert result['status'] == 'saved' and len(client.prompts) == 2
    assert '程序核對不通過' in client.prompts[1] and 'G01-CL0001' in client.prompts[1]


def test_a_point_left_over_from_another_step_is_reported(tmp_path):
    """他已講到另一步，畫面最新的要點還屬於別的 Claim：三句以上就報；兩條綁同一句也報。"""
    context = {"claims": [
        {"claim_id": "C1", "evidence_steps": [{"evidence_step_id": "E1", "anchors": [{"sentences": ["S0001", "S0002"]}]}]},
        {"claim_id": "C2", "evidence_steps": [{"evidence_step_id": "E2", "anchors": [{"sentences": ["S0003", "S0004", "S0005", "S0006"]}]}]},
    ]}
    lines = [{"id": f"S000{i}"} for i in range(1, 7)]
    late = [{"first": "S0001", "title": "甲", "points": [{"sentence": "S0001", "text": "一", "source": "E1"},
                                                         {"sentence": "S0006", "text": "二", "source": "E2"}]}]
    assert hints.stale_points(context, late, lines) == [
        "甲（S0003–S0005）: 他已講到另一步（C2、E2），畫面還停在要點「一」；那一步的第一條要點要在 S0003 或更早出現"]
    on_time = [dict(late[0], points=[late[0]["points"][0], dict(late[0]["points"][1], sentence="S0003")])]
    assert hints.stale_points(context, on_time, lines) == []
    together = [dict(late[0], points=[dict(p, sentence="S0001") for p in late[0]["points"]])]
    assert any("兩條要點都綁在 S0001" in p for p in hints.stale_points(context, together, lines))


def test_a_verse_of_the_chapter_he_reads_is_not_counted_as_another_books_verse(tmp_path):
    (tmp_path / "MAT").mkdir(); (tmp_path / "ISA").mkdir()
    (tmp_path / "MAT" / "5.json").write_text(json.dumps({"9": "使人和睦的人有福了！因為他們必稱為神的兒子。"}))
    (tmp_path / "ISA" / "61.json").write_text(json.dumps({
        "6": "你們倒要稱為耶和華的祭司；人必稱你們為我們神的僕役。你們必喫用列國的財物，因得他們的榮耀自誇。",
        "7": "你們必得加倍的好處，代替所受的羞辱；分中所得的喜樂必代替所受的凌辱。"}))
    lines = ["因為他們必稱為神的兒子。」", "你們必得加倍的好處，代替所受的羞辱"]
    # Without the chapter in view, Matthew 5:9 reads as Isaiah 61:6 (half its pairs are shared).
    assert hints.read_aloud("isa-61-6-7", lines, tmp_path) == ["isa-61-6", "isa-61-7"]
    assert hints.read_aloud("isa-61-6-7", lines, tmp_path, passage="mat-5-1-48") == ["isa-61-7"]
    # A cited verse in the expounded chapter itself is still read.
    assert hints.read_aloud("mat-5-9", lines, tmp_path, passage="mat-5-1-48") == ["mat-5-9"]
