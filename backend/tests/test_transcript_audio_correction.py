import copy

import pytest

from backend.pipeline.slide_change_pilot import object_sha
from backend.pipeline.transcript_audio_correction import (
    apply_corrections, audio_decision, locate, normalized, validate_proposals,
    proposal_response,
    refinement_parent,
)


def paragraph(text='他用了 in how 來引用這句話。'):
    return {'id': 'p0', 'text': text, 'start_ms': 1000, 'end_ms': 5000, 'source_start': 0, 'source_end': len(text)}


def proposal(p, old='in how', new='引號', occurrence=1, **fields):
    row = {'paragraph_id': p['id'], 'old': old, 'new': new, 'occurrence': occurrence,
           'kind': 'lexical', 'audio_quote': '他用了引號來引用這句話', 'audio_support': 'clear',
           'reason': 'Synthetic audio fixture', 'slide_event_ids': []}
    row.update(fields)
    return validate_proposals({'proposals': [row]}, [p], set())[0]


def test_independent_audio_support_needs_the_same_surrounding_words():
    p = paragraph()
    edit = proposal(p)
    assert audio_decision(p, edit, '他用了引號來引用這句話。')['status'] == 'machine_supported'
    # A hint or another mention of the new word somewhere in the clip is not evidence.
    assert audio_decision(p, edit, '他用了 in how 來引用這句話，後面再談引號。')['status'] == 'pending'
    assert audio_decision(p, edit, '這張投影片有引號。')['status'] == 'pending'


def test_slide_or_bible_reference_cannot_override_audible_chapter():
    p = paragraph('我們來看十三章，這裡提到愛。')
    edit = proposal(p, '十三章', '十四章', kind='scripture_reference')
    heard = '我們來看十三章，這裡提到愛，後面還會講十四章。'
    assert audio_decision(p, edit, heard)['status'] == 'pending'
    assert apply_corrections([p], [{**edit, 'status': 'pending'}])[0] == p


def test_negation_and_uncertain_audio_are_preserved_even_if_another_model_agrees():
    p = paragraph('他說這是正確的，然後往下講。')
    edit = proposal(p, '正確', '不正確')
    assert audio_decision(p, edit, '他說這是不正確的，然後往下講。')['status'] == 'pending'
    p = paragraph()
    edit = proposal(p, audio_support='uncertain')
    assert audio_decision(p, edit, '他用了引號來引用這句話。')['status'] == 'pending'


def test_exact_occurrences_preserve_repetition_and_only_audited_ranges_change():
    p = paragraph('他說 in how，然後再說 in how。')
    edit = proposal(p, occurrence=2)
    before = copy.deepcopy(p)
    corrected = apply_corrections([p], [{**edit, 'status': 'machine_supported'}])[0]
    assert corrected['text'] == '他說 in how，然後再說 引號。'
    assert p == before
    assert corrected['start_ms'] == p['start_ms'] and corrected['end_ms'] == p['end_ms']
    assert corrected['word_timing_valid'] is False
    assert corrected['source_ranges_address_original_text_only']


def test_stale_or_overlapping_edits_cannot_be_applied():
    p = paragraph()
    edit = proposal(p)
    with pytest.raises(ValueError):
        apply_corrections([p], [{**edit, 'status': 'machine_supported'}] * 2)
    with pytest.raises(ValueError):
        apply_corrections([{**p, 'text': 'changed'}], [{**edit, 'status': 'machine_supported'}])
    with pytest.raises(ValueError):
        audio_decision({**p, 'text': 'changed'}, edit, '引號')


@pytest.mark.parametrize('field,value', [
    ('paragraph_id', 'invented'), ('slide_event_ids', ['invented']), ('old', 'absent'),
    ('occurrence', 2), ('occurrence', 1.5), ('new', ''), ('new', 'a' * 33), ('new', '引\n號'),
])
def test_model_cannot_invent_sources_or_rewrite_beyond_bounded_replacements(field, value):
    p = paragraph()
    with pytest.raises(ValueError):
        proposal(p, **{field: value})


def test_punctuation_only_and_combining_sequence_splits_are_rejected():
    p = paragraph('甲，乙。')
    with pytest.raises(ValueError):
        proposal(p, '，', '。')
    p = paragraph('甲 e\u0301 乙。')
    with pytest.raises(ValueError, match='combining'):
        proposal(p, 'e', 'a')


def test_normalization_does_not_translate_simplify_or_remove_diacritics():
    assert normalized('引號， quote!') == '引號quote'
    assert normalized('引號') != normalized('引号')
    assert normalized('e\u0301') != normalized('e')


def test_known_truncation_has_only_one_bound_recovery_and_replays_both_responses(tmp_path):
    class Adapter:
        def __init__(self):
            self.config = {'max_output_tokens': 8192}
            self.generations, self.counts, self.configs = 0, 0, []
        def count(self, parts):
            self.counts += 1
            return 100
        def generate(self, parts):
            self.generations += 1
            self.configs.append(copy.deepcopy(self.config))
            return {'candidates': [{'finish_reason': 'MAX_TOKENS' if self.generations == 1 else 'STOP'}]}
        def usage(self, raw): return {'estimated_usd_upper': .01}
    adapter = Adapter()
    request = {'stage': 'test', 'config': adapter.config}
    first, cost, digest = proposal_response(adapter, [], request, tmp_path)
    assert first['candidates'][0]['finish_reason'] == 'STOP' and cost == .02
    assert adapter.generations == 2 and adapter.counts == 2
    assert adapter.configs[1]['thinking_config']['thinking_level'] == 'LOW'
    assert adapter.config['max_output_tokens'] == 8192
    replay, again, repeated_digest = proposal_response(adapter, [], request, tmp_path)
    assert (replay, again, repeated_digest) == (first, cost, digest)
    assert adapter.generations == 2 and adapter.counts == 2


def test_unknown_outcome_has_no_recovery_generation(tmp_path):
    class Adapter:
        config = {}
        def count(self, parts): return 100
        def generate(self, parts): raise TimeoutError('unknown outcome')
    with pytest.raises(TimeoutError):
        proposal_response(Adapter(), [], {'test': 'unknown'}, tmp_path)
    assert len(list((tmp_path / 'calls').glob('*/state.json'))) == 1


def test_composite_reference_can_be_narrowed_without_overriding_the_audible_chapter():
    p = paragraph('我們來看哥羅前書第十三章第一節。')
    parent = proposal(p, '哥羅前書第十三章第一節', '哥林多前書第十四章第一節', kind='scripture_reference')
    child = proposal(p, '哥羅前書', '哥林多前書', kind='proper_name')
    assert refinement_parent(child, [parent]) == parent
    heard = '我們來看哥林多前書第十三章第一節。'
    assert audio_decision(p, parent, heard)['status'] == 'pending'
    decision = audio_decision(p, child, heard)
    assert decision['status'] == 'machine_supported'
    corrected = apply_corrections([p], [{**child, **decision}])[0]
    assert corrected['text'] == heard
    outside = proposal(p, '我們', '大家')
    with pytest.raises(ValueError, match='original pending'):
        refinement_parent(outside, [parent])
