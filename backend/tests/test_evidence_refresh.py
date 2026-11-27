"""Independent Evidence repairs, safe source binding and real SQL readback."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path

import pytest

from backend.pipeline import evidence_refresh as e
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def verdict(choice):
    return {'model': e.MODEL, 'answers': {'unsupported_part_exists': {
        'type': 'choice', 'choice': choice,
        'probabilities': {k: float(k == choice) for k in ['yes', 'no', 'uncertain']}}}}


class Ports:
    identity = {'jev': e.MODEL, 'gpt': {'backend': 'test', 'model': 'test', 'effort': 'medium'}}

    def __init__(self, choice=None, repair=None):
        self.calls = []
        self.choice, self.proposal = choice, repair

    def judge(self, request):
        self.calls.append('jev')
        return verdict(self.choice or ('yes' if '拒絕明白' in request['state']['statement'] else 'no'))

    def repair(self, request):
        self.calls.append('gpt')
        items = []
        for item in request['input']:
            value = item['input']
            reply = self.proposal({'input': value}) if self.proposal else self.single_repair(value)
            reply.update(evidence_id=value['evidence_id'])
            reply.setdefault('action', 'keep' if reply['statement'] == value['statement'] else 'repair')
            items.append(reply)
        return {'items': items}

    @staticmethod
    def single_repair(value):
        return {'statement': '沒有聽從祂的話，顯明沒有真正相信祂。', 'speaker': 'professor',
                'stance': 'asserted', 'quotes': [{'paragraph_key': 'S0001',
                'quote': '你沒有聽從祂的話，你根本沒信。'}], 'reason': '恢復原文的行為判準。'}


@pytest.fixture
def sample(postgres_store, tmp_path):
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'script': [{'index': 1, 'start_time': 1.0, 'end_time': 2.0,
        'text': '你不知道神的旨意，怎麼遵行？你沒有聽從祂的話，你根本沒信。'}]}, ensure_ascii=False))
    doc = {'source_id': 'S', 'source_type': 'sermon_transcript', 'source_path': str(source),
           'source_file_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'title': 'test'}
    snap = e.source_snapshot({'path': str(source), 'file_sha256': doc['source_file_sha256']}, source=doc)
    p = snap['paragraphs']['S0001']
    quote = p['text']
    fragment = {'fragment_id': 'F', 'source_id': 'S', 'paragraph_key': 'S0001',
        'verbatim_excerpt': quote, 'source_sha256': snap['body_sha256'],
        'anchor_state': 'source_version_bound', 'source_segment_index': p['index'],
        'media_time': p.get('start_time'), 'media_end_time': p.get('end_time'),
        'paragraph_text_sha256': hashlib.sha256(quote.encode()).hexdigest(),
        'verbatim_excerpt_sha256': hashlib.sha256(quote.encode()).hexdigest()}
    postgres_store.apply_plan(postgres_store.plan_package({'package_id': 'evidence-refresh-test',
        'source_documents': [doc], 'source_fragments': [fragment],
        'claims': [{'claim_id': 'C', 'statement': '聽從與相信有關。', 'claim_type': 'exegesis',
                    'evidence_step_ids': ['E'], 'occurrences': [{'source_id': 'S', 'anchors': []}]}],
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '拒絕明白祂的話顯明沒有真正相信祂。',
            'source_fragment_ids': ['F'], 'produced_claim_ids': ['C'], 'speaker': 'professor',
            'stance': 'asserted', 'support_eligibility': 'withheld_unreviewed'}]}))
    return postgres_store, tmp_path / 'run', source


def test_repair_commits_real_postimage_without_old_update_or_qualification(sample, monkeypatch):
    store, root, _ = sample
    import backend.pipeline.cvp_sql_actions as old
    monkeypatch.setattr(old, 'execute_claim_management', lambda *a, **k: pytest.fail('old Update invoked'))
    original = store.get_record_state('claims', 'C')
    ports = Ports()
    result = e.run(store, ['E'], root, ports, apply=True)
    assert result['status'] == 'applied' and result['applied']
    assert ports.calls == ['jev', 'gpt']
    actual = store.get_record_state('evidence_steps', 'E')['payload']
    assert actual['statement'] == '沒有聽從祂的話，顯明沒有真正相信祂。'
    assert actual['produced_claim_ids'] == ['C']
    assert actual['support_eligibility'] == 'withheld_unreviewed'
    assert result['affected_claim_ids'] == ['C'] and result['claim_qualification'] == 'not_granted'
    assert store.get_record_state('claims', 'C') == original
    fid = actual['source_fragment_ids'][0]
    assert store.get_record_state('source_fragments', fid)['payload']['verbatim_excerpt'] == '你沒有聽從祂的話，你根本沒信。'
    assert e.run(store, ['E'], root, ports, apply=True) == result
    assert ports.calls == ['jev', 'gpt']


def test_no_write_or_gpt_for_clean_evidence(sample):
    store, root, _ = sample
    before = store.get_record_state('evidence_steps', 'E')
    ports = Ports(choice='no')
    assert e.run(store, ['E'], root, ports, apply=True)['status'] == 'verified'
    assert ports.calls == ['jev'] and store.get_record_state('evidence_steps', 'E') == before


def test_verification_does_not_write_proposals(sample):
    store, root, _ = sample
    before = store.get_record_state('evidence_steps', 'E')
    result = e.run(store, ['E'], root, Ports())
    assert result['updates'] and not result['applied']
    assert store.get_record_state('evidence_steps', 'E') == before


def test_gpt_can_reject_a_precheck_without_a_reviewer_loop(sample):
    store, root, _ = sample
    def keep(r):
        m = r['input']
        return {'statement': m['statement'], 'speaker': m['speaker'], 'stance': m['stance'],
                'quotes': [{'paragraph_key': f['paragraph_key'], 'quote': f['quote']} for f in m['fragments']],
                'reason': '有據不同意預檢。'}
    ports = Ports(choice='yes', repair=keep)
    result = e.run(store, ['E'], root, ports, apply=True)
    assert result['status'] == 'verified' and not result['applied']
    assert result['evidence'][0]['action'] == 'keep'
    assert ports.calls == ['jev', 'gpt']


@pytest.mark.parametrize('fault', ['quote', 'paragraph', 'extra_field'])
def test_bad_repair_never_writes(sample, fault):
    store, root, _ = sample
    before = store.get_record_state('evidence_steps', 'E')
    def bad(r):
        p = Ports.single_repair(r['input'])
        if fault == 'quote': p['quotes'][0]['quote'] = '耶穌沒有說過的另一句話。'
        if fault == 'paragraph': p['quotes'][0]['paragraph_key'] = 'S9999'
        if fault == 'extra_field': p['produced_claim_ids'] = []
        return p
    with pytest.raises(Exception):
        e.run(store, ['E'], root, Ports(repair=bad), apply=True)
    assert store.get_record_state('evidence_steps', 'E') == before


def test_source_drift_stops_commit(sample):
    store, root, source = sample
    before = store.get_record_state('evidence_steps', 'E')
    def change(r):
        source.write_text('changed source')
        return Ports.single_repair(r['input'])
    with pytest.raises(ValueError, match='source changed'):
        e.run(store, ['E'], root, Ports(repair=change), apply=True)
    assert store.get_record_state('evidence_steps', 'E') == before


def test_concurrent_evidence_write_is_not_overwritten(sample):
    store, root, _ = sample
    def change(r):
        p = deepcopy(store.get_record_state('evidence_steps', 'E')['payload'])
        p['statement'] = 'concurrent legitimate update'
        store.apply_plan(store.plan_package({'package_id': 'concurrent', 'evidence_steps': [p]}))
        return Ports.single_repair(r['input'])
    with pytest.raises(Exception):
        e.run(store, ['E'], root, Ports(repair=change), apply=True)
    assert store.get_record_state('evidence_steps', 'E')['payload']['statement'] == 'concurrent legitimate update'


def test_unknown_call_is_not_reissued(tmp_path):
    calls = []
    def lost(req):
        calls.append(req)
        raise TimeoutError('reply lost')
    with pytest.raises(TimeoutError):
        e.recorded_call(tmp_path, 'gpt', {'input': 1}, lost)
    with pytest.raises(ValueError, match='no saved reply'):
        e.recorded_call(tmp_path, 'gpt', {'input': 1}, lost)
    assert len(calls) == 1


def test_local_preflight_failure_does_not_create_an_unknown_call(tmp_path):
    def unavailable():
        raise ValueError('client unavailable before send')
    with pytest.raises(ValueError, match='before send'):
        e.recorded_call(tmp_path, 'gpt', {'input': 1}, lambda r: pytest.fail('sent'), preflight=unavailable)
    assert not list(tmp_path.glob('gpt/*/started.json'))
    assert e.recorded_call(tmp_path, 'gpt', {'input': 1}, lambda r: {'ok': True}) == {'ok': True}


def test_multiple_findings_use_one_gpt_batch(sample):
    store, root, _ = sample
    row = deepcopy(store.get_record_state('evidence_steps', 'E')['payload'])
    extras = []
    for eid in ['D', 'X']:
        p = deepcopy(row)
        p.update(evidence_step_id=eid, produced_claim_ids=[])
        extras.append(p)
    store.apply_plan(store.plan_package({'package_id': 'more-evidence', 'evidence_steps': extras}))
    ports = Ports()
    result = e.run(store, ['E', 'D', 'X'], root, ports, apply=True)
    assert result['applied'] and len(result['updates']) == 3
    # Identical Jev inputs are shared even between distinct Evidence IDs.
    assert ports.calls.count('gpt') == 1
    assert len(result['new_fragments']) == 1


def test_needs_source_blocks_the_whole_transaction(sample):
    store, root, _ = sample
    before = store.get_record_state('evidence_steps', 'E')
    def undecided(r):
        value = r['input']
        return {'action': 'needs_source', 'statement': value['statement'],
                'speaker': value['speaker'], 'stance': value['stance'],
                'quotes': [{'paragraph_key': f['paragraph_key'], 'quote': f['quote']} for f in value['fragments']],
                'reason': '原文沒有解答，需核對錄音。'}
    result = e.run(store, ['E'], root, Ports(repair=undecided), apply=True)
    assert result['status'] == 'needs_attention' and not result['applied']
    assert store.get_record_state('evidence_steps', 'E') == before


def test_resume_after_commit_only_reads_back(sample, monkeypatch):
    store, root, _ = sample
    original = e.readback
    monkeypatch.setattr(e, 'readback', lambda *a: (_ for _ in ()).throw(RuntimeError('interrupted readback')))
    ports = Ports()
    with pytest.raises(RuntimeError, match='interrupted readback'):
        e.run(store, ['E'], root, ports, apply=True)
    monkeypatch.setattr(e, 'readback', original)
    monkeypatch.setattr(store, 'apply_plan', lambda *a, **k: pytest.fail('duplicate SQL apply'))
    result = e.run(store, ['E'], root, ports, apply=True)
    assert result['applied'] and ports.calls == ['jev', 'gpt']


def test_verified_proposal_can_apply_without_repeating_models(sample):
    store, root, _ = sample
    ports = Ports()
    assert not e.run(store, ['E'], root, ports)['applied']
    assert e.run(store, ['E'], root, ports, apply=True)['applied']
    assert ports.calls == ['jev', 'gpt']


def test_sql_commit_with_lost_receipt_recovers_from_one_changeset(sample, monkeypatch):
    store, root, _ = sample
    original = store.apply_plan
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('lost SQL receipt')
    monkeypatch.setattr(store, 'apply_plan', lost)
    ports = Ports()
    with pytest.raises(RuntimeError, match='lost SQL receipt'):
        e.run(store, ['E'], root, ports, apply=True)
    monkeypatch.setattr(store, 'apply_plan', lambda *a, **k: pytest.fail('duplicate SQL apply'))
    assert e.run(store, ['E'], root, ports, apply=True)['applied']
    assert ports.calls == ['jev', 'gpt']


@pytest.mark.parametrize('choice', ['yes', 'no', 'uncertain'])
def test_no_hidden_probability_threshold(choice):
    response = verdict(choice)
    response['answers']['unsupported_part_exists']['probabilities'] = {
        k: .4 if k == choice else .3 for k in ['yes', 'no', 'uncertain']}
    assert e.judgment(response) == choice


def test_invalid_distribution_is_not_a_pass():
    response = verdict('no')
    response['answers']['unsupported_part_exists']['probabilities']['no'] = 1.5
    with pytest.raises(ValueError, match='distribution'):
        e.judgment(response)


@pytest.mark.parametrize('failures,expected', [('handshake', 2), ('twice', 2), ('read', 1), ('unknown', 1), ('http', 1), ('redirect', 1)])
def test_jev_retries_only_proven_pre_send_tls_handshake(monkeypatch, tmp_path, failures, expected):
    import io
    from types import SimpleNamespace
    from urllib.error import HTTPError, URLError

    ssl = {'__name__': 'ssl'}
    exec('def do_handshake():\n    raise TimeoutError("handshake timed out")', ssl)
    redirect = {'__name__': 'urllib.request', 'URLError': URLError, 'handshake': ssl['do_handshake']}
    exec('def http_error_302():\n    try: handshake()\n    except TimeoutError as exc: raise URLError(exc) from exc', redirect)
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    sent = []

    class ReadTimeout(io.BytesIO):
        def read(self):
            raise TimeoutError('response timed out')

    def open_request(request, *, timeout):
        sent.append(request)
        if failures in {'handshake', 'twice'} and (len(sent) == 1 or failures == 'twice'):
            try:
                ssl['do_handshake']()
            except TimeoutError as exc:
                raise URLError(exc) from exc
        if failures == 'redirect':
            redirect['http_error_302']()
        if failures == 'read':
            return ReadTimeout()
        if failures == 'unknown':
            raise URLError(TimeoutError('phase unknown'))
        if failures == 'http':
            raise HTTPError(request.full_url, 429, 'rate limited', None, None)
        return io.BytesIO(b'{"answers":{}}')

    monkeypatch.setattr('urllib.request.urlopen', open_request)
    models = e.Models(SimpleNamespace(backend='codex_subscription', model='test', reasoning_effort='medium'), tmp_path)
    if failures == 'handshake':
        assert models.judge({'state': {}}) == {'answers': {}}
        assert sent[0] is sent[1]
    else:
        with pytest.raises((TimeoutError, URLError)):
            models.judge({'state': {}})
    assert len(sent) == expected
