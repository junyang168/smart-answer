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
