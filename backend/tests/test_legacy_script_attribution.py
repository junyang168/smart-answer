import hashlib
import json
from types import SimpleNamespace

import pytest
from backend.api.sc_api.script_delta import ScriptDelta


@pytest.mark.parametrize('row_type', [None, 'content', 'subtitle'])
def test_legacy_save_preserves_row_attribution_without_blaming_current_login(tmp_path, row_type):
    item = '既有稿件'
    (tmp_path / 'script').mkdir()
    (tmp_path / 'script_review').mkdir()
    (tmp_path / 'script' / f'{item}.json').write_text('{"entries": []}')
    path = tmp_path / 'script_review' / f'{item}.json'
    path.write_text('[]')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = [SimpleNamespace(text='已知作者的舊內容', type=row_type,
                            user_id='prior-editor@example.org', index='1', end_index='1'),
            SimpleNamespace(text='沒有作者紀錄的內容', type=row_type,
                            user_id=None, index='2', end_index='2')]
    delta = ScriptDelta(str(tmp_path), item)
    delta.save_script('current-login@example.org', 'scripts', item, data,
                      expected_current_sha256=before)
    saved = json.loads(path.read_text())
    assert saved[0]['user_id'] == 'prior-editor@example.org'
    assert 'user_id' not in saved[1]
    assert 'current-login@example.org' not in path.read_text()
    assert [row['text'] for row in saved] == [p.text for p in data]
