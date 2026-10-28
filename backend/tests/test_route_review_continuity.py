"""Review history informs new calls; it never approves or repeats old calls."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import viewpoint_route_cvp_resolution as flow
from backend.pipeline.viewpoint_route_review_continuity import (
    CONTEXT, RULES, continuity_packet,
)
from test_viewpoint_observation_support import Answer, approved, fixture_packet, observation_proposal


def test_review_sees_compiled_observation_identity_after_content_change(tmp_path):
    from backend.api.canonical_repository.viewpoint_observation_support import (
        ArgumentRouteProposalV3Response, compile_observation_package)
    from backend.pipeline.viewpoint_route_review_continuity import OBSERVATION_IDENTITIES
    evidence=fixture_packet();proposal=observation_proposal(evidence)
    old=compile_observation_package(ArgumentRouteProposalV3Response.model_validate(proposal),
        evidence,review_sha256='old')[0][0]
    evidence['existing_observation_supports']=[old]
    proposal['observation_supports'][0].update(local_observation_key=old['observation_support_id'],
        supports='仅支持本节经文用词的观察。')
    proposal['retired_observation_support_ids']=[old['observation_support_id']]
    packet={'route_proposal':proposal,'route_evidence_context':evidence}
    result=continuity_packet(packet,[],packet_path=tmp_path/'packet.json',response_path=tmp_path/'raw.json')
    item=result[OBSERVATION_IDENTITIES]['records'][0]
    assert item['local_observation_key']==old['observation_support_id']
    assert item['compiled_observation_support_id']!=old['observation_support_id']
    created,retired,_=compile_observation_package(ArgumentRouteProposalV3Response.model_validate(proposal),
        evidence,review_sha256='new')
    assert created[0]['observation_support_id']==item['compiled_observation_support_id']
    assert retired==[('viewpoint_observation_supports',old['observation_support_id'])]
    assert continuity_packet(packet,[],packet_path=tmp_path/'packet.json',response_path=tmp_path/'raw.json')==result


def test_all_rounds_keep_objections_dispositions_and_shared_rubric(tmp_path):
    packet = fixture_packet()
    original = observation_proposal(packet)

    def review(p):
        history = p[CONTEXT]['rounds']
        assert len(history) == reviewer.calls - 1
        assert p[CONTEXT]['shared_rules'] == RULES
        if history:
            first = history[0]
            assert first['change_reviews'][-1]['correction'] == '删除夸大的支持作用，保留观察'
            assert first['finding_dispositions'][0]['reason'] == '保留观察并收窄作用'
            assert 'source_sections' not in json.dumps(history)
        answer = approved(p)
        if reviewer.calls < 3:
            row = next(r for r in answer['change_reviews'] if r['target_kind'] == 'observation')
            row.update(decision='correct', finding_codes=['scope'],
                reason='观察成立，但作用须收窄', correction='删除夸大的支持作用，保留观察')
        return answer

    def correct(p):
        assert p[CONTEXT]['shared_rules'] == RULES
        assert len(p[CONTEXT]['rounds']) == corrector.calls
        assert p[CONTEXT]['rounds'][-1]['route_review_sha256'] == p['route_review_sha256']
        revised = deepcopy(p['route_proposal'])
        revised['observation_supports'][0]['supports'] = f'第{corrector.calls}次收窄后的实际作用'
        return {'schema_version': 'wang_argument_route_reconsideration_v3',
            'route_proposal_sha256': p['route_proposal_sha256'],
            'route_review_sha256': p['route_review_sha256'],
            'finding_dispositions': [{'target_kind': 'observation', 'target_key': 'OBS-1',
                'disposition': 'accepted', 'reason': '保留观察并收窄作用'}],
            'revised_proposal': revised}

    proposer = Answer(original, model_id='gpt')
    reviewer = Answer(review, model_id='opus')
    corrector = Answer(correct, model_id='gpt')
    args = dict(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=proposer, reviewer=reviewer, reconsiderer=corrector, max_correction_rounds=2)
    result = flow.run_cvp_route_scope(**args)
    assert result['rounds'][-1]['outcome'] == 'pass'
    assert proposer.payloads[0][CONTEXT]['shared_rules'] == RULES
    # Prior payloads are snapshots, not references subsequently filled in.
    assert corrector.payloads[0][CONTEXT]['rounds'][0]['finding_dispositions'] == []
    assert len(reviewer.payloads[1][CONTEXT]['rounds']) == 1
    before = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    flow.run_cvp_route_scope(**args)
    assert (proposer.calls, reviewer.calls, corrector.calls) == (1, 3, 2)
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}


def test_legacy_saved_rounds_replay_exactly_then_new_round_gets_all_history(tmp_path, monkeypatch):
    from test_route_round_extension import stopped, round_extension
    real = flow.continuity_packet
    monkeypatch.setattr(flow, 'continuity_packet', lambda packet, history, **kwargs: packet)
    root, job, policy, authority, args = stopped(tmp_path)
    before = {p.name: p.read_bytes() for p in root.glob('*.json')}
    monkeypatch.setattr(flow, 'continuity_packet', real)
    extension = round_extension(root, job, policy, authority)
    result = flow.run_cvp_route_scope(**args, max_correction_rounds=2, round_extension=extension)
    assert result['rounds'][-1]['outcome'] == 'pass'
    assert (args['proposer'].calls, args['reviewer'].calls, args['reconsiderer'].calls) == (1, 3, 2)
    assert all((root/name).read_bytes() == value for name, value in before.items())
    history = args['reviewer'].payloads[-1][CONTEXT]['rounds']
    assert len(history) == 2 and all(r['finding_dispositions'] for r in history)
    assert CONTEXT not in args['reviewer'].payloads[0]
    assert CONTEXT not in args['reconsiderer'].payloads[0]
    assert len(args['reconsiderer'].payloads[-1][CONTEXT]['rounds']) == 2


def test_deleted_observation_and_reason_remain_visible_without_becoming_current_target(tmp_path):
    packet = fixture_packet()
    kept = observation_proposal(packet)
    initial = deepcopy(kept)
    extra = deepcopy(initial['observation_supports'][0])
    extra.update(local_observation_key='OBS-REMOVED', supports='待审核的另一支持作用')
    initial['observation_supports'].append(extra)

    def review(p):
        answer = approved(p)
        if reviewer.calls == 1:
            row = next(r for r in answer['change_reviews'] if r['target_key'] == 'OBS-REMOVED')
            row.update(decision='correct', finding_codes=['wrong_support'],
                reason='观察并不支持此 CVP', correction='删除 OBS-REMOVED，保留 OBS-1')
        else:
            assert not any(t['target_key'] == 'OBS-REMOVED' for t in p['review_targets'])
            old = p[CONTEXT]['rounds'][0]
            assert next(r for r in old['change_reviews'] if r['target_key'] == 'OBS-REMOVED')['correction'] == '删除 OBS-REMOVED，保留 OBS-1'
            assert old['finding_dispositions'][0]['reason'] == '按第一轮意见删除该观察'
        return answer

    reviewer = Answer(review, model_id='opus')
    def correct(p):
        return {'schema_version': 'wang_argument_route_reconsideration_v3',
            'route_proposal_sha256': p['route_proposal_sha256'],
            'route_review_sha256': p['route_review_sha256'],
            'finding_dispositions': [{'target_kind': 'observation', 'target_key': 'OBS-REMOVED',
                'disposition': 'accepted', 'reason': '按第一轮意见删除该观察'}],
            'revised_proposal': kept}
    result = flow.run_cvp_route_scope(route_packet=packet, existing_routes=[], output_dir=tmp_path,
        proposer=Answer(initial, model_id='gpt'), reviewer=reviewer,
        reconsiderer=Answer(correct, model_id='gpt'), max_correction_rounds=1)
    assert result['rounds'][-1]['outcome'] == 'pass'


@pytest.mark.parametrize('suffix', ['.json', '.call-intent.json', '.request-encoding.json'])
def test_already_dispatched_legacy_slot_keeps_input_even_without_response(tmp_path, suffix):
    response = tmp_path/'raw-answer.json'
    (tmp_path/('raw-answer'+suffix)).write_text('{}')
    packet = {'existing': 'exact input'}
    saved = tmp_path/'input.json'
    assert continuity_packet(packet, [], packet_path=saved, response_path=response) == packet
    assert not saved.exists()


def test_saved_new_history_cannot_be_silently_changed(tmp_path):
    args = dict(packet_path=tmp_path/'packet.json', response_path=tmp_path/'raw-answer.json')
    continuity_packet({'a': 1}, [], **args)
    with pytest.raises(ValueError):
        continuity_packet({'a': 1}, [{'forged': 'review'}], **args)


def test_history_never_turns_new_finding_into_approval(tmp_path):
    from test_route_round_extension import stopped
    root, _, _, _, args = stopped(tmp_path, pass_after=99)
    assert args['reviewer'].payloads[-1][CONTEXT]['rounds']
    assert not (root/'route-cvp-run.json').exists()
    assert json.loads((root/'route-review-001.json').read_text())['validation']['outcome'] == 'findings'
