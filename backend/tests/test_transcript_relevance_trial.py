import pytest
from backend.pipeline.transcript_relevance_trial import apply_case,prepare
from backend.pipeline.slide_change_pilot import read_json,save_json,file_sha

def test_whole_block_soft_delete_preserves_original_marked_text():
 s='今天先調整設備。\n\n請把投影打開。'
 r=apply_case(s,dict(decision='soft_delete',deletions=[dict(old=s,occurrence=1)],retained_expression=''))
 assert r['text']=='' and r['marked']=='~~'+s+'~~'

def test_reject_overlap_rewriting_and_uncertain_deletions():
 s='甲乙丙丁'
 for result in [dict(decision='soft_delete',deletions=[dict(old='甲乙',occurrence=1),dict(old='乙丙',occurrence=1)],retained_expression='丁'),dict(decision='soft_delete',deletions=[dict(old='甲',occurrence=1)],retained_expression='戊'),dict(decision='uncertain',deletions=[dict(old='甲',occurrence=1)],retained_expression='乙')]:
  with pytest.raises(ValueError):apply_case(s,result)

def test_blind_input_removes_marks_not_words_and_does_not_write_source(tmp_path):
 source=tmp_path/'source.json';save_json(source,[dict(index=str(i),text='原文~~刪除文字~~末尾') for i in range(1,314)])
 sha=file_sha(source);prepare(source,tmp_path/'out');m=read_json(tmp_path/'out/input.json')
 assert len(m['cases'])==8 and file_sha(source)==sha
 for c in m['cases']:
  assert '~~' not in str(c) and '刪除文字' in c['target']
  assert 'human_marked' not in c and 'paragraph_start' not in c
