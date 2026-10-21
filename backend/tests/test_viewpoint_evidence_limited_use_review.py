from copy import deepcopy
import pytest

from backend.pipeline.viewpoint_evidence_limited_use_review import validate_decision, run_review
from backend.pipeline.viewpoint_resolution_runtime import read_artifact


def packet():
    return {'artifact_sha256': 'packet', 'item_id': 'c:2', 'evidence_step_id': 'e',
        'exact_use': '随后讨论祷告', 'registry_before_sha256': 'registry',
        'evidence': {'paragraph_key': 'S0018', 'verbatim_excerpt': '講到禱告的問題。'}}


def decision(**changes):
    return {'item_id': 'c:2', 'evidence_step_id': 'e', 'exact_use': '随后讨论祷告',
        'decision': 'approved_limited_use', 'paragraph_key': 'S0018',
        'exact_quote': '講到禱告的問題。', 'reason': '仅支持主题，不批准经节范围或全局资格。',
        'problems': []} | changes


@pytest.mark.parametrize('changes', [
    {'evidence_step_id': 'foreign'}, {'item_id': 'other:2'}, {'exact_use': '确定经节范围'},
    {'paragraph_key': 'S0019'}, {'exact_quote': '完整正确的经节范围'},
    {'problems': ['仍需修改']}, {'decision': 'pass'},
])
def test_false_or_widened_approval_rejected(changes):
    with pytest.raises(ValueError): validate_decision(decision(**changes), packet())


class Adapter:
    model_id = 'test'
    backend = 'subscription_test'
    prompt_sha256 = 'prompt'
    generation_config_sha256 = 'config'

    def __init__(self, answer):
        self.answer, self.calls, self.payloads = answer, 0, []

    def generate(self, payload):
        self.calls += 1; self.payloads.append(deepcopy(payload))
        return self.answer


def test_blind_dual_review_not_release_or_completion(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_evidence_limited_use_review as module
    monkeypatch.setattr(module, 'registry_fingerprint', lambda *args: 'registry')
    adapters = [Adapter(decision()), Adapter(decision())]
    report = run_review(packet(), tmp_path, object(), adapters)
    assert report['limited_use_approved']
    assert report['global_eligibility_released'] is report['group_complete'] is False
    assert report['registry_writes'] == 0
    assert adapters[0].payloads == adapters[1].payloads
    assert run_review(packet(), tmp_path, object(), adapters) == report
    assert [a.calls for a in adapters] == [1, 1]


def test_actual_rejection_stops_once(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_evidence_limited_use_review as module
    monkeypatch.setattr(module, 'registry_fingerprint', lambda *args: 'registry')
    adapters = [Adapter(decision(decision='rejected', problems=['超出支持范围'])), Adapter(decision())]
    report = run_review(packet(), tmp_path, object(), adapters)
    assert not report['limited_use_approved']
    assert report['next_action'] == 'stop_no_retry'
    assert [a.calls for a in adapters] == [1, 0]


def test_invalid_answer_saved_and_not_retried(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_evidence_limited_use_review as module
    monkeypatch.setattr(module, 'registry_fingerprint', lambda *args: 'registry')
    adapters = [Adapter(decision(exact_quote='伪造')), Adapter(decision())]
    with pytest.raises(ValueError): run_review(packet(), tmp_path, object(), adapters)
    assert (tmp_path / 'raw-gpt.json').exists()
    assert read_artifact(tmp_path / 'failure.json')['registry_writes'] == 0
    with pytest.raises(ValueError, match='retried'): run_review(packet(), tmp_path, object(), adapters)
    assert [a.calls for a in adapters] == [1, 0]


def test_uncertain_started_call_not_repeated(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_evidence_limited_use_review as module
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    monkeypatch.setattr(module, 'registry_fingerprint', lambda *args: 'registry')
    write_immutable(tmp_path / 'gpt-review-start.json', {})
    adapters = [Adapter(decision()), Adapter(decision())]
    with pytest.raises(ValueError, match='no automatic retry'):
        run_review(packet(), tmp_path, object(), adapters)
    assert [a.calls for a in adapters] == [0, 0]


def test_registry_drift_blocks_before_models(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_evidence_limited_use_review as module
    monkeypatch.setattr(module, 'registry_fingerprint', lambda *args: 'drift')
    adapters = [Adapter(decision()), Adapter(decision())]
    with pytest.raises(ValueError, match='Registry drift'):
        run_review(packet(), tmp_path, object(), adapters)
    assert [a.calls for a in adapters] == [0, 0]
