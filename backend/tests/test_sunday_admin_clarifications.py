"""Actual durable graph/queue tests; no model or real WhatsApp calls."""
import json

import pytest

from backend.sunday_assistant.agent_graph import run_agent
from backend.sunday_assistant.engine import Assistant
from backend.sunday_assistant.models import ChatMessage, Extraction
from backend.sunday_assistant.store import Store
from backend.tests.test_sunday_assistant import agent, service
from backend.tests.test_sunday_assistant_graph import ScriptModel, incoming, trace, deterministic_grounding
from backend.tests.test_sunday_fellowship import fellowship



@pytest.fixture
def admin(agent):
    settings = agent.store.settings()
    settings.admin_group = '999@g.us'
    settings.admin_clarifications_enabled = True
    settings.group_questions_enabled = False
    agent.store.put('settings', settings.model_dump())
    return agent


def ask(agent, *, id='origin', question='請問最後一節要重複幾次？'):
    msg = incoming(agent, '11/8 奇異恩典，最後一節反覆', id=id)
    assert run_agent(agent, msg, model=ScriptModel(('ask_group', {'request': {'questions': [question]}})))['status'] == 'waiting_reply'
    agent.deliver_outbox()
    with agent.store.db() as db:
        return db.execute("SELECT id FROM clarifications ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_all_questions_go_only_to_admin_even_with_legacy_source_flag(admin):
    settings = admin.store.settings(); settings.group_questions_enabled = True
    admin.store.put('settings', settings.model_dump())
    case = ask(admin)
    assert admin.bridge.sent[0][1] == '999@g.us'
    assert f'#{case}' in admin.bridge.sent[0][2]
    assert not any(x[1] in settings.source_group_ids for x in admin.bridge.sent)


def test_validation_partition_prevents_questions_from_discarding_known_song(admin):
    msg = incoming(admin, '11/8 奇異恩典，最後一節反覆')
    known = {'service_changes': [service(field='hymn', value='奇異恩典', evidence='奇異恩典')]}
    mixed = {'service_changes': known['service_changes'] + [service(field='hymnNotes', value='最後一節反覆', evidence='最後一節反覆')]}
    question = {'questions': ['最後一節要重複幾次？']}
    model = ScriptModel(('read_sunday_record', {'day': '2026-11-08'}),
                        ('update_records', {'request': mixed}),
                        ('ask_group', {'request': question}),  # tries to omit known values
                        ('update_records', {'request': known, 'finish_event': False}),
                        ('ask_group', {'request': question}))
    assert run_agent(admin, msg, model=model)['status'] == 'waiting_reply'
    entry = admin.repository.services['2026-11-08']
    assert entry.hymn == '奇異恩典' and entry.hymn_notes is None
    assert 'save_independent_fields_first' in trace(admin)[2].content
    admin.deliver_outbox()
    assert len(admin.bridge.sent) == 1 and admin.bridge.sent[0][1] == '999@g.us'


def test_admin_reply_resumes_after_restart_and_patches_only_notes(admin):
    case = ask(admin)
    # A human saved the clear fields in the meantime; they must survive the answer.
    from backend.api.models import SundayServiceEntry
    admin.repository.services['2026-11-08'] = SundayServiceEntry(date='2026-11-08', hymn='奇異恩典', sermonTitle='保留人工題目')
    restarted = Assistant(Store(admin.store.root), admin.repository, bridge=admin.bridge)
    reply = incoming(restarted, '最後一節重複一次', group='999@g.us', sender='different-admin', id='answer', at='2026-11-03T16:00:00+00:00')
    assert restarted.pending_dialogues(reply)[0]['source_group_id'] == '222@g.us'
    request = {'clarification_id': case, 'service_changes': [service(field='hymnNotes', value=reply.text, evidence=reply.text)]}
    model = ScriptModel(('read_group_context', {}), ('read_sunday_record', {'day': '2026-11-08'}), ('update_records', {'request': request}))
    assert run_agent(restarted, reply, model=model)['status'] == 'done'
    entry = admin.repository.services['2026-11-08']
    assert (entry.hymn, entry.sermon_title, entry.hymn_notes) == ('奇異恩典', '保留人工題目', reply.text)
    with restarted.store.db() as db:
        assert db.execute('SELECT status,resolved_by FROM clarifications WHERE id=?', (case,)).fetchone()[:] == ('resolved', 'answer')
    assert run_agent(restarted, reply, model=ScriptModel(RuntimeError('duplicate')))['status'] == 'done'
    restarted.deliver_outbox()
    assert len(restarted.bridge.sent) == 2 and all(x[1] == '999@g.us' for x in restarted.bridge.sent)


def test_partial_admin_answer_retains_original_source_and_any_admin_can_continue(admin):
    case = ask(admin)
    reply = incoming(admin, '每段都反覆，但次數再確認', group='999@g.us', sender='admin1', id='partial', at='2026-11-03T16:00:00+00:00')
    run_agent(admin, reply, model=ScriptModel(('read_group_context', {}), ('ask_group', {'request': {'clarification_id': case, 'questions': ['每段要重複幾次？']}})))
    admin.deliver_outbox()
    reply2 = incoming(admin, '一次', group='999@g.us', sender='admin2', id='answer2', at='2026-11-04T16:00:00+00:00')
    pending = admin.pending_dialogues(reply2)
    assert len(pending) == 1 and pending[0]['source_group_id'] == '222@g.us'
    assert [m['message_id'] for m in pending[0]['source_messages']] == ['origin', 'partial']
    assert pending[0]['source_messages'][0]['sender_profile']['self_worker'] == '王明'


def test_other_source_cannot_answer_admin_case_and_admin_cannot_create_arbitrary_updates(admin):
    case = ask(admin)
    unrelated = incoming(admin, '一次', group='111@g.us', sender='stranger', id='bad', at='2026-11-03T16:00:00+00:00')
    assert not admin.pending_dialogues(unrelated)
    reply = incoming(admin, '講員陳牧師', group='999@g.us', id='unbound', at='2026-11-03T16:00:00+00:00')
    with pytest.raises(ValueError, match='必須連結'):
        admin.build_plan(reply, Extraction(service_changes=[service()]))
    # Ordinary admin chatter still finishes normally.
    assert run_agent(admin, reply, model=ScriptModel(('read_group_context', {}), ('ignore_message', {})))['status'] == 'irrelevant'


def test_delivery_rechecks_destination_and_cancels_resolved_question(admin):
    msg = incoming(admin)
    run_agent(admin, msg, model=ScriptModel(('ask_group', {'request': {'questions': ['哪位？']}})))
    settings = admin.store.settings(); settings.admin_group = '888@g.us'
    admin.store.put('settings', settings.model_dump())
    admin.deliver_outbox()
    assert not admin.bridge.sent and admin.store.outbox()[0]['status'] == 'held'
    with admin.store.db() as db:
        db.execute("UPDATE clarifications SET status='resolved'")
        db.execute("UPDATE outbox SET status='queued'")
    admin.deliver_outbox()
    assert not admin.bridge.sent and admin.store.outbox()[0]['status'] == 'cancelled'


def test_ambiguous_answer_can_ask_case_selection_without_changing_either_case(admin):
    first = ask(admin)
    second = ask(admin, id='origin2', question='請問外出何時回來？')
    reply = incoming(admin, '一次', group='999@g.us', sender='admin', id='unclear', at='2026-11-03T16:00:00+00:00')
    run_agent(admin, reply, model=ScriptModel(('read_group_context', {}), ('ask_admin_case_selection', {'question': f'請問是回答問題 #{first} 還是 #{second}？'})))
    assert not admin.repository.services
    admin.deliver_outbox()
    assert len(admin.bridge.sent) == 3 and all(x[1] == '999@g.us' for x in admin.bridge.sent)
    assert len(admin.pending_dialogues(reply.model_copy(update={'sent_at': reply.sent_at.replace(day=4)}))) == 2


def test_admin_clarification_mode_keeps_fellowship_reply_tools(fellowship):
    from backend.tests.test_sunday_fellowship import seed, event
    agent, service, entries = seed(fellowship)
    settings = agent.store.settings(); settings.admin_clarifications_enabled = True
    agent.store.put('settings', settings.model_dump())
    msg = event(agent, '下一次王明帶')
    request = {'date': '2026-11-20', 'changes': {'host': '王明'}, 'evidence': {'host': msg.text}}
    assert run_agent(agent, msg, model=ScriptModel(('read_fellowship_context', {}), ('patch_fellowship', {'request': request})))['status'] == 'done'
    assert entries['11/20/2026'].host == '王明'
    assert not agent.repository.services


def test_questions_only_plan_cannot_omit_clear_song_names(admin, monkeypatch):
    calls = []
    known = Extraction(service_changes=[service(field='hymn', value='奇異恩典', evidence='奇異恩典')])
    def coverage(*args):
        calls.append(1)
        return known
    monkeypatch.setattr('backend.sunday_assistant.grounding.clarification_known_fields', coverage)
    msg = incoming(admin, '11/8 奇異恩典，最後一節反覆')
    question = {'questions': ['最後一節重複幾次？']}
    result = run_agent(admin, msg, model=ScriptModel(
        ('ask_group', {'request': question}),
        ('read_sunday_record', {'day': '2026-11-08'}),
        ('update_records', {'request': known.model_dump(mode='json'), 'finish_event': False}),
        ('ask_group', {'request': question})))
    assert result['status'] == 'waiting_reply'
    assert admin.repository.services['2026-11-08'].hymn == '奇異恩典'
    assert calls == [1]  # cached across further graph turns
    assert 'save_independent_fields_first' in trace(admin)[0].content


def test_admin_reply_does_not_promote_old_source_over_newer_arrangement(admin):
    from backend.api.models import SundayServiceEntry
    from backend.sunday_assistant.store import dump
    case = ask(admin)
    admin.repository.services['2026-11-08'] = SundayServiceEntry(date='2026-11-08', hymn='新的歌曲')
    with admin.store.db() as db:
        db.execute('INSERT INTO ownership VALUES (?,?,?,?)', ('service:2026-11-08:hymn', dump('新的歌曲'), '2026-11-03T15:00:00+00:00', 'newer-source'))
    reply = incoming(admin, '一次', group='999@g.us', id='later-answer', at='2026-11-03T16:00:00+00:00')
    plan = admin.build_plan(reply, Extraction(clarification_id=case, service_changes=[service(field='hymn', value='奇異恩典', evidence='奇異恩典')]))
    assert not plan['operations'] and any('較新安排' in q for q in plan['questions'])


def test_quoted_admin_answer_is_bound_to_exact_case(admin):
    first = ask(admin)
    ask(admin, id='origin2', question='11/15 是哪首歌？')
    reply = incoming(admin, '一次', group='999@g.us', id='quoted', at='2026-11-03T16:00:00+00:00')
    reply = reply.model_copy(update={'quoted_message_id': f'remote-clarify:{first}:{first}'})
    assert [c['clarification_id'] for c in admin.pending_dialogues(reply)] == [first]


def test_defer_keeps_saved_songs_unblocks_service_and_later_reply_resumes(admin):
    from backend.api.models import SundayServiceEntry
    from backend.sunday_assistant.ppt_ready import PptReady
    case = ask(admin)
    admin.repository.services['2026-11-08'] = SundayServiceEntry(date='2026-11-08', hymn='奇異恩典', responseHymn='保留回應')
    ppt = PptReady(admin, generator=lambda: None, template=admin.store.root/'template')
    assert ppt.pending('2026-11-08')
    reply = incoming(admin, 'ignore 前三行，我明天问一问', group='999@g.us', sender='admin1', id='defer', at='2026-11-03T16:00:00+00:00')
    result = run_agent(admin, reply, model=ScriptModel(('read_group_context', {}), ('defer_pending', {'clarification_id': case})))
    assert result['disposition'] == 'deferred' and not result['form_changed']
    assert not ppt.pending('2026-11-08')
    entry = admin.repository.services['2026-11-08']
    assert (entry.hymn, entry.response_hymn, entry.hymn_notes) == ('奇異恩典', '保留回應', None)
    restarted = Assistant(Store(admin.store.root), admin.repository, bridge=admin.bridge)
    assert restarted.defer_dialogue(reply, case) == result  # durable receipt, no re-send
    later = incoming(restarted, '最後一節重複一次', group='999@g.us', sender='admin2', id='resolved-later', at='2026-11-04T16:00:00+00:00')
    assert restarted.pending_dialogues(later)[0]['status'] == 'deferred'
    request = {'clarification_id': case, 'service_changes': [service(field='hymnNotes', value=later.text, evidence=later.text)]}
    assert run_agent(restarted, later, model=ScriptModel(('read_group_context', {}), ('read_sunday_record', {'day':'2026-11-08'}), ('update_records', {'request':request})))['status'] == 'done'
    with restarted.store.db() as db:
        assert db.execute('SELECT status FROM clarifications WHERE id=?',(case,)).fetchone()[0] == 'resolved'
    assert restarted.repository.services['2026-11-08'].hymn_notes == later.text


def test_relevant_pending_reply_cannot_be_discarded_as_chatter(admin, monkeypatch):
    case = ask(admin)
    def verify(agent, msg, action, case_id=None):
        return {'supported':action=='defer', 'questions':[] if action=='defer' else ['這是暫緩目前安排，不是閒聊']}
    monkeypatch.setattr('backend.sunday_assistant.grounding.verify_pending_disposition', verify)
    reply = incoming(admin, 'ignore 前三行，我明天问一问', group='999@g.us', id='was-ignored', at='2026-11-03T16:00:00+00:00')
    result = run_agent(admin, reply, model=ScriptModel(('read_group_context', {}), ('ignore_message', {}), ('defer_pending', {'clarification_id':case})))
    assert result['disposition']=='deferred'
    assert 'pending_reply_relevant' in trace(admin)[1].content


def test_merely_answering_later_does_not_authorize_unblocking_case(admin, monkeypatch):
    case = ask(admin)
    monkeypatch.setattr('backend.sunday_assistant.grounding.verify_pending_disposition', lambda *_: {'supported':False,'questions':['沒有先略過指示，仍需等待回答']})
    reply = incoming(admin, '明天回答你', group='999@g.us', id='not-a-deferral', at='2026-11-03T16:00:00+00:00')
    with pytest.raises(RuntimeError):
        run_agent(admin, reply, model=ScriptModel(('read_group_context', {}), ('defer_pending', {'clarification_id':case}), RuntimeError('end test after rejection')))
    with admin.store.db() as db:
        assert db.execute('SELECT status FROM clarifications WHERE id=?',(case,)).fetchone()[0] == 'waiting'
    assert not admin.repository.services
