import pytest
from backend.pipeline.transcript_restart_trial import apply_case,prepare
from backend.pipeline.slide_change_pilot import save_json,read_json,file_sha


def candidate(old,kept,occurrence=1):
    return dict(decision='soft_delete',deletions=[dict(old=old,occurrence=occurrence)],retained_expression=kept)


def test_abandoned_middle_can_be_removed_without_rewriting_completion():
    source='因為受達爾文影響，達爾文進化論影響。'
    r=apply_case(source,candidate('影響，達爾文','受達爾文進化論影響'))
    assert r['text']=='因為受達爾文進化論影響。'
    assert r['marked']=='因為受達爾文~~影響，達爾文~~進化論影響。'


def test_reject_invented_completion_or_nonliteral_quote():
    with pytest.raises(ValueError,match='invented'):apply_case('我……我說甲。',candidate('我……','我說乙'))
    with pytest.raises(ValueError):apply_case('甲\n乙，丙',candidate('甲乙','丙'))
    with pytest.raises(ValueError):apply_case('甲乙',candidate('錯字','乙'))


def test_uncertainty_cannot_delete_and_repeated_occurrences_are_explicit():
    source='這個，這個，這個觀點。'
    r=apply_case(source,candidate('這個，','這個，這個觀點',occurrence=2))
    assert r['text']=='這個，這個觀點。'
    with pytest.raises(ValueError):apply_case(source,{**candidate('這個，','這個觀點'),'decision':'uncertain'})
    with pytest.raises(ValueError):apply_case('甲',candidate('甲',''))


def test_human_deletion_key_is_not_in_blind_input_and_source_stays_unchanged(tmp_path):
    source=tmp_path/'source.json'
    save_json(source,[dict(index=str(i),text='原文~~人工刪除~~仍保留') for i in (118,139,145,178,243,252,254,276)])
    before=file_sha(source);out=tmp_path/'trial';prepare(source,out)
    manifest=read_json(out/'input.json')
    assert len(manifest['cases'])==8
    assert all(c['target']=='原文人工刪除仍保留' for c in manifest['cases'])
    assert '~~' not in str(manifest['cases']) and 'test_role' not in str(manifest['cases'])
    assert all(k['human_deleted_spans']==['人工刪除'] for k in read_json(out/'human-key.json'))
    assert file_sha(source)==before
