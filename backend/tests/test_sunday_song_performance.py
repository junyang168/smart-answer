import pytest
from backend.sunday_song_performance import apply_song_notes


def test_selects_explicit_verses_and_repeats_only_for_this_service():
    original=[['第一節歌詞'],['第二節歌詞'],['第三節歌詞'],['第四節歌詞']]
    result=apply_song_notes(original,'只唱一、二、四節，最後一節重複一次')
    assert result==[original[0],original[1],original[3],original[3]]
    assert len(original)==4


@pytest.mark.parametrize('notes',['只唱第五節','最後一節有返覆，字幕須配合','配合伴奏改一下'])
def test_unclear_or_nonexistent_verse_directions_need_clarification(notes):
    with pytest.raises(ValueError):apply_song_notes([['第一節']],notes)


def test_explicit_first_three_lines_repeat_is_local_and_preserves_other_stanzas():
    sections = [['甲', '乙', '丙', '丁'], ['戊', '己', '庚', '辛']]
    assert apply_song_notes(sections, '前三行重複一次') == [['甲','乙','丙','甲','乙','丙','丁'], sections[1]]
    assert apply_song_notes(sections, '每段前三行反覆一次') == [['甲','乙','丙','甲','乙','丙','丁'], ['戊','己','庚','戊','己','庚','辛']]
    assert sections == [['甲','乙','丙','丁'], ['戊','己','庚','辛']]


@pytest.mark.parametrize('notes', ['返覆前三行', '前三行重複', '前三行重複零次', '前三行重複99次', '前九行重複一次'])
def test_line_repetition_never_invents_count_or_lyrics(notes):
    with pytest.raises(ValueError):
        apply_song_notes([['甲', '乙', '丙', '丁']], notes)
