import json

import pytest

from backend.pipeline import argument_replay as r


def saved(root, name, request, answer):
    folder = root / name / 'gpt' / 'x'
    folder.mkdir(parents=True)
    (folder / 'request.json').write_text(json.dumps(request))
    (folder / 'response.json').write_text(json.dumps(answer))


def test_a_saved_answer_is_reused_only_for_the_same_input(tmp_path):
    saved(tmp_path, 'steps', {'instruction': 'old', 'input': {'task': 'steps', 'text': 'A'}}, {'steps': [1]})
    ports = r.ReplayPorts(tmp_path)
    assert ports.repair({'instruction': 'new', 'input': {'task': 'steps', 'text': 'A'}}) == {'steps': [1]}
    assert ports.used[0]['instruction_changed'] is True
    with pytest.raises(r.InputChanged, match="text"):
        ports.repair({'instruction': 'new', 'input': {'task': 'steps', 'text': 'B'}})
