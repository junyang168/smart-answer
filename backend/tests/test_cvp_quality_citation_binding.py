import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('quality_citation', Path(__file__).parents[2] / 'scripts/cvp-quality-citation-binding.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def bind(text, quote):
    return MODULE.bind({'physical_ordinal': 1, 'quote': quote}, {'segments': [{
        'physical_ordinal': 1, 'editorial': False, 'physical_text': text,
        'live_text': text.replace('~~乙~~', '\n')}]})


def test_format_only_binding_preserves_original_quote_and_physical_segment_identity():
    r = bind('原文 **πίστιν**，*of you*（你們的）。\n這是原話。', '原文 πίστιν，of you（你們的）。這是原話。')
    assert r['binding_kind'] == 'presentation_equivalent_not_raw_verbatim'
    assert r['quote_unchanged'] and r['physical_source_unchanged']
    assert r['quote_sha256'] != r['physical_segment_sha256']
    assert bind('這是原話。', '原話')['binding_kind'] == 'raw_verbatim'


def test_deleted_words_missing_qualifier_and_changed_greek_cannot_be_normalized_away():
    for original, quote in [('甲~~乙~~丙', '甲丙'), ('甲~~乙~~丙', '甲\n丙'), ('這裡明顯，用在這些人身上，同樣的原則。', '這裡明顯，同樣的原則。'),
                            ('πίστιν', 'πιστιν'), ('the faith', 'thefaith'), ('faith\nof you', 'faithof you'),
                            ('甲，乙', '甲乙')]:
        with pytest.raises(ValueError):
            bind(original, quote)


def test_nonverbatim_citation_does_not_become_a_semantic_defect_or_drop_other_observations():
    source = {'segments': [{'physical_ordinal': 1, 'editorial': False,
                           'physical_text': '只在有条件时成立。', 'live_text': '只在有条件时成立。'}]}
    bad = MODULE.observation_evidence({'observation_id': 'O1', 'citations': [
        {'physical_ordinal': 1, 'quote': '成立。'}]}, source)
    assert bad['semantic_judgment_state'] == 'not_evaluated' and bad['is_content_defect'] is None
    uncertain = MODULE.observation_evidence({'observation_id': 'O2', 'citations': [
        {'physical_ordinal': 1, 'quote': '只在条件时成立。'}]}, source)
    assert uncertain['requires_semantic_source_check']
    assert uncertain['block_entire_source'] is False and uncertain['retained_in_denominator']
