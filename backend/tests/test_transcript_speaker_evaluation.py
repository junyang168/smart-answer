from backend.pipeline.transcript_speaker_evaluation import compare, labelled_reference


def test_parenthetical_scope_does_not_label_surrounding_text():
    text, labels = labelled_reference('好。(聽眾插話: 為什麼？)對。')
    assert text == '好為什麼對'
    assert labels == [None, 'audience', 'audience', 'audience', None]


def test_multiturn_and_soft_deleted_text():
    text, labels = labelled_reference('聽眾問: 甲？\n教授: ~~不是~~乙。\n聽眾: 丙。\n教授: 丁。')
    assert text == '甲乙丙丁'
    assert labels == ['audience', 'professor', 'audience', 'professor']


def test_unlabelled_is_not_a_negative_control():
    result = compare('沒有標記', [{'text':'沒有標記', 'role':'professor'}])
    assert result['label_agreement_on_matched_characters'] is None
    assert result['explicit_reference_characters'] == 0


def test_wrong_role_and_unaligned_edited_text_are_disclosed():
    result = compare('聽眾: 甲乙丙\n教授: 丁', [
        {'text': '甲乙', 'role': 'professor'}, {'text':'丁','role':'professor'}])
    assert result['reference_alignment_coverage'] == 0.75
    assert result['label_agreement_on_matched_characters'] == 1/3
    assert len(result['mismatches']) == 2
