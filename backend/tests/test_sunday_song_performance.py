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
