from pathlib import Path
import copy

import pytest

from backend.pipeline.slide_change_pilot import object_sha
from backend.pipeline.transcript_audio_correction import (
    apply_corrections, audio_decision, locate, normalized, validate_proposals,
    proposal_response, validate_repaired_proposals,
)


def paragraph(text='他用了 in how 來引用這句話。'):
    return {'id': 'p0', 'text': text, 'start_ms': 1000, 'end_ms': 5000, 'source_start': 0, 'source_end': len(text)}


def proposal(p, old='in how', new='引號', occurrence=1, **fields):
    row = {'paragraph_id': p['id'], 'old': old, 'new': new, 'occurrence': occurrence,
           'kind': 'lexical', 'audio_quote': '他用了引號來引用這句話', 'audio_support': 'clear',
           'reason': 'Synthetic audio fixture', 'slide_event_ids': []}
    row.update(fields)
    return validate_proposals({'proposals': [row]}, [p], set())[0]


def test_clear_gemini_audio_slide_correction_is_accepted_without_asr_agreement():
    p = paragraph()
    edit = proposal(p)
    decision = audio_decision(p, edit)
    assert decision['status'] == 'machine_supported'
    assert apply_corrections([p], [{**edit, **decision}])[0]['text'] == '他用了 引號 來引用這句話。'


def test_only_model_uncertainty_holds_back_a_valid_correction():
    p = paragraph()
    edit = proposal(p, audio_support='uncertain')
    assert audio_decision(p, edit)['status'] == 'pending'
    assert apply_corrections([p], [{**edit, **audio_decision(p, edit)}])[0] == p


def test_completed_repair_withholds_only_unlocatable_quotes_and_never_guesses_references():
    from backend.pipeline.transcript_audio_correction import SCHEMA, validate_completed_repair
    p = paragraph()
    valid = {k: v for k, v in proposal(p).items()
             if k in SCHEMA['properties']['proposals']['items']['properties']}
    missing = {**valid, 'old': 'inhow'}  # Similarity is not exact source evidence.
    original = copy.deepcopy(p)
    edits, withheld = validate_completed_repair({'proposals': [valid, missing]}, [p], set())
    assert len(edits) == len(withheld) == 1
    assert withheld[0]['old'] == 'inhow'
    assert withheld[0]['paragraph_text_sha256'] == object_sha(p['text'])
    assert 'start' not in withheld[0] and p == original
    for bad in ({**missing, 'paragraph_id': 'invented'}, {**valid, 'slide_event_ids': ['invented']}):
        with pytest.raises(ValueError, match='invented'):
            validate_completed_repair({'proposals': [bad]}, [p], set())
    with pytest.raises(ValueError):
        validate_completed_repair({'proposals': [valid], 'extra': True}, [p], set())


def test_clear_negation_correction_is_not_vetoed_by_a_second_rule():
    p = paragraph('他說這是正確的，然後往下講。')
    edit = proposal(p, '正確', '不正確')
    assert audio_decision(p, edit)['status'] == 'machine_supported'


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
        audio_decision({**p, 'text': 'changed'}, edit)


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


@pytest.mark.parametrize('verdict, heard, expected', [
    ('replacement', '引號', 'machine_supported'),
    ('original', 'in how', 'pending'),
    ('uncertain', '', 'pending'),
    ('replacement', '別的詞', 'pending'),
    (None, '', None),
])
def test_run_verifies_context_edits_and_preserves_out_of_scope_paragraphs(
    tmp_path, monkeypatch, verdict, heard, expected,
):
    import json
    from backend.pipeline import transcript_audio_correction as module
    from backend.pipeline.slide_change_pilot import file_sha, save_json

    source, audio, out = [tmp_path / name for name in ('source', 'audio', 'out')]
    source.mkdir()
    audio.mkdir()
    (audio / 'audio.wav').write_bytes(b'synthetic audio')
    (audio / 'illustrated_transcript.json').write_text('{}')
    editable = paragraph()
    protected = {**editable, 'id': 'protected'}
    row = proposal(editable)
    fields = ('paragraph_id', 'old', 'new', 'occurrence', 'kind', 'reason')
    candidate = {key: row[key] for key in fields}
    save_json(audio / 'audio_manifest.json', {
        'source_sha256': 'source', 'audio_sha256': file_sha(audio / 'audio.wav'), 'duration_ms': 900000,
    })
    save_json(source / 'illustrated_with_visual_text.json', {
        'source_sha256': 'source', 'speech_artifact_sha256': file_sha(audio / 'illustrated_transcript.json'),
        'paragraphs': [protected, editable], 'assets': [], 'occurrences': [],
    })
    stages = []

    class Adapter:
        input_rate = output_rate = 1

        def __init__(self, provider, prompt, schema):
            self.model = provider
            self.prompt = prompt
            self.config = {'max_output_tokens': 1024}

        def count(self, parts):
            return 100

        def generate(self, parts):
            stages.append(self.model)
            if self.model == 'openai':
                material = json.loads(parts)
                assert material['eligible_ids'] == ['p0']
                assert [p['id'] for p in material['context']] == ['protected', 'p0']
                suggestions = [{**candidate, 'paragraph_id': 'protected'}]
                if verdict is not None:
                    suggestions.append(candidate)
                return {'proposals': suggestions}
            assert parts[1].inline_data.data == b'synthetic mp3'
            return {'verdict': verdict, 'heard': heard}

        def text(self, raw):
            return json.dumps(raw)

        def usage(self, raw):
            return {'estimated_usd_upper': .01}

    def fake_clip(samples, start, end, folder):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'audio.mp3'
        path.write_bytes(b'synthetic mp3')
        return path

    monkeypatch.setattr(module, 'VisionReviewer', Adapter)
    monkeypatch.setattr(module, 'read_audio', lambda _: None)
    monkeypatch.setattr(module, 'clip_audio', fake_clip)
    result = module.run(source, audio, out, eligible_ids={'p0'})
    assert stages == (['openai'] if verdict is None else ['openai', 'gemini'])
    assert result['corrected_paragraphs'][0] == protected
    assert result['withheld_proposals'][0]['status'] == 'withheld_outside_edit_scope'
    assert result['status_counts'] == ({expected: 1} if expected else {})
    expected_text = editable['text'].replace('in how', '引號') if expected == 'machine_supported' else editable['text']
    assert result['corrected_paragraphs'][1]['text'] == expected_text
    assert result['independent_asr_calls'] == 0
    assert module.run(source, audio, out, eligible_ids={'p0'}) == result
    assert len(stages) == (1 if verdict is None else 2)


def test_redundant_nested_foreign_word_edit_is_recorded_under_covering_edit():
    p = paragraph('zēlōtai este，後面再說 este。')
    parent = {'paragraph_id': p['id'], 'old': 'zēlōtai este', 'new': 'ζηλωταί ἐστε',
              'occurrence': 1, 'kind': 'foreign_word', 'audio_quote': 'ζηλωταί ἐστε',
              'audio_support': 'clear', 'reason': 'Synthetic', 'slide_event_ids': []}
    child = {**parent, 'old': 'este', 'new': 'ἐστε'}
    answer = {'proposals': [parent, child]}
    with pytest.raises(ValueError, match='Overlapping'):
        validate_proposals(answer, [p], set())
    edits = validate_repaired_proposals(answer, [p], set())
    assert len(edits) == 1 and edits[0]['subsumed_proposals'][0]['old'] == 'este'
    corrected = apply_corrections([p], [{**edits[0], **audio_decision(p, edits[0])}])[0]
    # Do not guess that the nested edit meant the second occurrence.
    assert corrected['text'] == 'ζηλωταί ἐστε，後面再說 este。'
    with pytest.raises(ValueError, match='Overlapping'):
        validate_repaired_proposals({'proposals': [parent, {**child, 'new': 'different'}]}, [p], set())


def test_existing_internal_linebreak_does_not_block_a_clear_word_correction():
    p = paragraph('這個\n不是有恩賜你就屬靈。')
    edit = proposal(p, '這個\n不是', '這個不是屬靈，不是')
    corrected = apply_corrections([p], [{**edit, **audio_decision(p, edit)}])
    assert len(corrected) == 1 and corrected[0]['id'] == p['id']
    assert corrected[0]['text'] == '這個不是屬靈，不是有恩賜你就屬靈。'


@pytest.mark.parametrize('second_reason', ['STOP', 'MALFORMED_FUNCTION_CALL'])
def test_known_malformed_function_response_has_one_recorded_tools_disabled_recovery(tmp_path, second_reason):
    class Adapter:
        def __init__(self):
            self.config = {'max_output_tokens': 16384}
            self.generations = 0
            self.configs = []
        def count(self, parts): return 100
        def generate(self, parts):
            self.generations += 1
            self.configs.append(copy.deepcopy(self.config))
            return {'candidates': [{'finish_reason': 'MALFORMED_FUNCTION_CALL' if self.generations == 1 else second_reason}]}
        def usage(self, raw): return {'estimated_usd_upper': .01}
    adapter = Adapter()
    request = {'stage': 'test-malformed', 'config': adapter.config}
    result = proposal_response(adapter, [], request, tmp_path)
    assert result[0]['candidates'][0]['finish_reason'] == second_reason
    assert result[1] == .02
    assert adapter.generations == 2
    assert adapter.configs[1]['automatic_function_calling']['disable'] is True
    assert adapter.configs[1]['tool_config']['function_calling_config']['mode'] == 'NONE'
    from backend.pipeline.transcript_audio_correction import SCHEMA
    assert adapter.configs[1]['response_json_schema'] == SCHEMA
    assert adapter.config == request['config']
    assert proposal_response(adapter, [], request, tmp_path) == result
    assert adapter.generations == 2


def test_local_correction_can_preserve_existing_asr_hard_wrap():
    p = paragraph('神\n要為著拯救我們。')
    edit = proposal(p, old='神\n要為著拯救我們', new='神預定耶穌基督\n要為著拯救我們')
    result = apply_corrections([p], [{**edit, **audio_decision(p, edit)}])
    assert result[0]['text'] == '神預定耶穌基督\n要為著拯救我們。'
    assert p['text'] == '神\n要為著拯救我們。'


@pytest.mark.parametrize('source,old,new', [
    ('神要拯救我們。', '神要拯救我們', '神\n要拯救信徒'),
    ('神要拯救我們。', '神\n要拯救我們', '神\n要拯救信徒'),
    ('甲\n乙丙。', '甲\n乙丙', '甲乙\n丁'),
])
def test_correction_cannot_add_or_move_linebreaks_even_when_model_old_contains_them(source, old, new):
    with pytest.raises(ValueError, match='paragraph breaks|exact original'):
        proposal(paragraph(source), old=old, new=new)


def test_completed_repair_keeps_valid_audio_edits_and_withholds_case_only_changes():
    from backend.pipeline.transcript_audio_correction import SCHEMA, validate_completed_repair
    p = paragraph('他用了 in how 和 Irresistible calling。')
    fields = SCHEMA['properties']['proposals']['items']['properties']
    valid = {k:v for k,v in proposal(p).items() if k in fields}
    case = {**valid, 'old':'Irresistible calling', 'new':'irresistible calling'}
    edits, withheld = validate_completed_repair({'proposals':[valid,case]},[p],set())
    assert len(edits) == len(withheld) == 1
    assert withheld[0]['status'] == 'withheld_punctuation_only'
    assert 'start' not in withheld[0]
    assert p['text'] == '他用了 in how 和 Irresistible calling。'
    with pytest.raises(ValueError, match='invented'):
        validate_completed_repair({'proposals':[{**case,'paragraph_id':'invented'}]},[p],set())


def test_empty_completed_reply_gets_only_one_bound_format_recovery(tmp_path):
    from backend.pipeline.transcript_audio_correction import SCHEMA
    from backend.pipeline.slide_change_pilot import read_json
    class Adapter:
        config={'max_output_tokens':2048}
        generations=0
        def count(self,parts):return 10
        def generate(self,parts):
            self.generations+=1
            return {'candidates':[{'finish_reason':'STOP','content':{} if self.generations==1 else {'parts':[{'text':'{"proposals":[]}'}]}}]}
        def usage(self,raw):return {'estimated_usd_upper':0}
    adapter=Adapter();request={'stage':'original','model':'fixture'}
    first=proposal_response(adapter,[],request,tmp_path)
    assert adapter.generations==2
    assert proposal_response(adapter,[],request,tmp_path)==first and adapter.generations==2
    requests=[read_json(p) for p in (tmp_path/'calls').glob('*/request.json')]
    recovery=next(r for r in requests if r['stage']=='known_empty_response_recovery_once')
    assert recovery['parent_response_sha256'] and recovery['config']['response_json_schema']==SCHEMA
