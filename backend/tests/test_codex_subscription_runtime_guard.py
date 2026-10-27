import hashlib
import subprocess

import pytest

from backend.pipeline import codex_subscription_client as m
from backend.pipeline.codex_subscription_preflight import verify_runtime


@pytest.mark.parametrize("version", ["0.144.0-alpha.4", "0.159.1", "0.159.2-alpha.1"])
def test_stale_binary_rejected_before_auth_or_model_not_account_failure(tmp_path, monkeypatch, version):
    path = tmp_path / "codex"
    path.write_bytes(b"old binary")
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: str(path))
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        assert argv[-1] == "--version"
        return subprocess.CompletedProcess(argv, 0, "codex-cli " + version + "\n", "")
    monkeypatch.setattr(m.subprocess, "run", run)
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", executable=str(path))
    with pytest.raises(m.CodexSubscriptionError, match="client configuration failure"):
        client._verify_chatgpt_login()
    assert len(calls) == 1
    assert client.cli_identity is None


@pytest.mark.parametrize("version", ["0.159.2", "0.160.0"])
def test_current_binary_identity_then_chatgpt_login_same_executable(tmp_path, monkeypatch, version):
    path = tmp_path / "codex"
    path.write_bytes(b"current binary")
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: str(path))
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        assert argv[0] == str(path)
        assert "OPENAI_API_KEY" not in kw["env"]
        response = "codex-cli " + version if argv[-1] == "--version" else "Logged in using ChatGPT"
        return subprocess.CompletedProcess(argv, 0, response, "")
    monkeypatch.setattr(m.subprocess, "run", run)
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", executable=str(path), environment={"OPENAI_API_KEY": "forbidden"})
    client._verify_chatgpt_login()
    assert client.cli_identity["file_sha256"] == hashlib.sha256(b"current binary").hexdigest()
    assert client.executable == str(path)
    assert len(calls) == 2
    client._verify_chatgpt_login()
    assert len(calls) == 2


@pytest.mark.parametrize("result", [{"ok": False}, {}, {"ok": True, "other": 1}])
def test_probe_response_must_be_exact_and_no_fallback(monkeypatch, result):
    calls = []
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="high")
    monkeypatch.setattr(client, "generate_json", lambda *a, **kw: calls.append(kw) or result)
    with pytest.raises(m.CodexSubscriptionError, match="diagnostic"):
        client.verify_model_access()
    assert len(calls) == 1
    assert calls[0]["timeout_seconds"] == 60


def test_probe_failure_not_retried_or_replaced_with_other_model(monkeypatch):
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="high")
    calls = []
    def fail(*a, **k):
        calls.append(client.model)
        raise m.CodexSubscriptionError("quota or transport")
    monkeypatch.setattr(client, "generate_json", fail)
    with pytest.raises(m.CodexSubscriptionError, match="quota"):
        client.verify_model_access()
    assert calls == ["gpt-6.1-sol"]


def test_probe_success_explicit_separate_from_corpus_dryrun(monkeypatch):
    client = m.CodexSubscriptionClient(model="gpt-6.1-sol", reasoning_effort="high")
    client.cli_identity = {"path": "/current/codex", "version": "codex-cli 0.159.2", "file_sha256": "sha"}
    monkeypatch.setattr(client, "generate_json", lambda *a, **k: {"ok": True})
    assert client.verify_model_access() == {
        "status": "verified", "backend": "codex_subscription", "model": "gpt-6.1-sol",
        "reasoning_effort": "high", "cli": client.cli_identity, "model_calls": 1, "corpus_writes": 0}


def test_missing_or_unparseable_version_never_infers_account_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: None)
    with pytest.raises(m.CodexSubscriptionError, match="executable unavailable"):
        m.verify_cli_identity("missing", "gpt-6.1-sol", {})
    path = tmp_path / "codex"
    path.write_bytes(b"binary")
    monkeypatch.setattr(m.shutil, "which", lambda *a, **k: str(path))
    monkeypatch.setattr(m.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "unknown", ""))
    with pytest.raises(m.CodexSubscriptionError, match="Cannot verify"):
        m.verify_cli_identity(str(path), "gpt-6.1-sol", {})


def test_runtime_proof_reused_without_model_and_invalidated_by_binary_or_login(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    home.mkdir()
    (home / 'auth.json').write_text('login-state-not-a-real-credential')
    client = m.CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='high', environment={'CODEX_HOME': str(home)})
    client.cli_identity = {'path': '/current/codex', 'version': 'codex-cli 0.159.2', 'file_sha256': 'binary1'}
    local_checks, calls = [], []
    monkeypatch.setattr(client, '_verify_chatgpt_login', lambda: local_checks.append(True))
    monkeypatch.setattr(client, 'generate_json', lambda *a, **k: calls.append(client.model) or {'ok': True})
    cache = tmp_path / 'proofs'
    first = verify_runtime(client, cache)
    second = verify_runtime(client, cache)
    assert first['new_model_calls'] == 1 and second['new_model_calls'] == 0
    assert len(calls) == 1 and len(local_checks) == 2
    client.cli_identity['file_sha256'] = 'binary2'
    assert verify_runtime(client, cache)['new_model_calls'] == 1
    (home / 'auth.json').write_text('new-login-state')
    assert verify_runtime(client, cache)['new_model_calls'] == 1
    assert len(calls) == 3


def test_saved_runtime_proof_tamper_rejected_not_model_retry(tmp_path, monkeypatch):
    import json
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'auth.json').write_text('login')
    client = m.CodexSubscriptionClient(model='gpt-6.1-sol', environment={'CODEX_HOME': str(home)})
    client.cli_identity = {'path': '/current/codex'}
    monkeypatch.setattr(client, '_verify_chatgpt_login', lambda: None)
    calls = []
    monkeypatch.setattr(client, 'generate_json', lambda *a, **k: calls.append(True) or {'ok': True})
    first = verify_runtime(client, tmp_path / 'proofs')
    path = m.Path(first['proof_path'])
    proof = json.loads(path.read_text())
    proof['result']['model'] = 'other-model'
    path.write_text(json.dumps(proof))
    with pytest.raises(m.CodexSubscriptionError, match='mismatch'):
        verify_runtime(client, tmp_path / 'proofs')
    assert len(calls) == 1


def test_runtime_diagnostic_failure_has_persistent_once_guard(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();(home/'auth.json').write_text('login')
    client=m.CodexSubscriptionClient(model='gpt-6.1-sol',environment={'CODEX_HOME':str(home)})
    client.cli_identity={'path':'/current/codex','file_sha256':'new binary'}
    monkeypatch.setattr(client,'_verify_chatgpt_login',lambda:None)
    calls=[]
    def fail(*a,**kw):
        calls.append(True)
        raise m.CodexSubscriptionError('quota or unknown')
    monkeypatch.setattr(client,'generate_json',fail)
    with pytest.raises(m.CodexSubscriptionError):verify_runtime(client,tmp_path/'proofs')
    with pytest.raises(m.CodexSubscriptionError,match='no automatic retry'):
        verify_runtime(client,tmp_path/'proofs')
    assert len(calls)==1
    assert len(list((tmp_path/'proofs').glob('*.call-failure.json')))==1


@pytest.mark.parametrize('change',['binary','login','path'])
def test_canonical_app_upgrade_only_not_account_or_executable_substitution(tmp_path,monkeypatch,change):
    from backend.pipeline import viewpoint_s201129_source_continuation as mod
    from backend.pipeline import codex_subscription_preflight as pre
    old={'cli':{'path':'/same/codex','file_sha256':'old'},'model':'gpt-6.1-sol',
         'backend':'codex_subscription','reasoning_effort':'high','login_state_sha256':'login'}
    import copy
    actual=copy.deepcopy(old)
    if change=='binary':actual['cli']['file_sha256']='new'
    elif change=='login':actual['login_state_sha256']='changed'
    else:actual['cli']['path']='/foreign/codex'
    monkeypatch.setattr(mod,'read_artifact',lambda path:{'identity':old,'proof_path':str(tmp_path/'old.json')})
    monkeypatch.setattr(mod.repair,'file_sha256',lambda path:actual['login_state_sha256'])
    class Client:
        environment={};model='gpt-6.1-sol';backend='codex_subscription';reasoning_effort='high'
        cli_identity=actual['cli']
        def __init__(self,**kwargs):assert kwargs['executable']==old['cli']['path']
        def _verify_chatgpt_login(self):pass
    monkeypatch.setattr(m,'CodexSubscriptionClient',Client)
    calls=[]
    monkeypatch.setattr(pre,'verify_runtime',lambda client,path:calls.append(path) or {
        'identity':actual,'proof_path':str(tmp_path/'new.json'),'reused':False,'new_model_calls':1})
    if change=='binary':
        assert mod.canonical_runtime()['new_model_calls']==1
        assert len(calls)==1
    else:
        with pytest.raises(ValueError):mod.canonical_runtime()
        assert calls==[]


@pytest.mark.parametrize('drift',[None,'login','binary','proof','missing'])
def test_release_runtime_consumption_never_generates_diagnostic(tmp_path,monkeypatch,drift):
    import json
    from backend.pipeline.codex_subscription_preflight import consume_saved_runtime
    home=tmp_path/'home';home.mkdir();(home/'auth.json').write_text('offline test login')
    client=m.CodexSubscriptionClient(model='gpt-6.1-sol',reasoning_effort='high',environment={'CODEX_HOME':str(home)})
    client.cli_identity={'path':'/pytest-only-codex','file_sha256':'binary1'}
    monkeypatch.setattr(client,'_verify_chatgpt_login',lambda:None)
    monkeypatch.setattr(client,'generate_json',lambda *a,**k:{'ok':True})
    result=verify_runtime(client,tmp_path/'proofs')
    monkeypatch.setattr(client,'generate_json',lambda *a,**k:pytest.fail('no diagnostic fallback'))
    if drift=='login':(home/'auth.json').write_text('changed login')
    elif drift=='binary':client.cli_identity['file_sha256']='changed binary'
    elif drift=='missing':m.Path(result['proof_path']).unlink()
    elif drift=='proof':
        path=m.Path(result['proof_path']);value=json.loads(path.read_text())
        value['result']['model']='other';path.write_text(json.dumps(value))
    if drift:
        with pytest.raises((m.CodexSubscriptionError,FileNotFoundError)):
            consume_saved_runtime(client,result)
    else:
        assert consume_saved_runtime(client,result)['new_model_calls']==0


@pytest.mark.parametrize('drift', [None, 'login', 'binary', 'model', 'path',
                                  'previous', 'proof', 'missing', 'owner', 'direction', 'alias', 'copy'])
def test_explicit_canonical_renewal_consumes_exact_proof_without_new_call(tmp_path, monkeypatch, drift):
    import copy
    import json
    from backend.pipeline import viewpoint_s201129_source_continuation as mod
    from backend.pipeline.cvp_sql_flow import binding

    home = tmp_path/'home'; home.mkdir(); (home/'auth.json').write_text('renewed login')
    client = m.CodexSubscriptionClient(model='gpt-6.1-sol', reasoning_effort='high',
                                      environment={'CODEX_HOME': str(home)})
    client.cli_identity = {'path': '/same/codex', 'file_sha256': 'renewed binary'}
    monkeypatch.setattr(client, '_verify_chatgpt_login', lambda: None)
    calls = []
    monkeypatch.setattr(client, 'generate_json', lambda *a, **k: calls.append(True) or {'ok': True})
    verified = verify_runtime(client, tmp_path/'proofs')
    old = copy.deepcopy(verified)
    old['identity']['login_state_sha256'] = 'previous login'
    old['identity']['cli']['file_sha256'] = 'previous binary'
    monkeypatch.setattr(mod.repair, 'BASE', tmp_path)
    previous = tmp_path/'s201129-owner-local-reextraction-20261005-v2/runtime-proof.json'
    previous.parent.mkdir(); previous.write_text(json.dumps(old))
    renewal = {'schema_version': 'codex_runtime_renewal.v1', 'authorized_by': 'owner',
               'owner_direction': 'Use current login and one startup verification.',
               'previous_runtime': binding(previous), 'current_runtime': verified,
               'current_proof': binding(verified['proof_path'])}
    if drift == 'alias':
        alias = tmp_path/'old-alias'; alias.symlink_to(previous.parent, target_is_directory=True)
        renewal['previous_runtime']['path'] = str(alias/previous.name)
        proof_alias = tmp_path/'proof-alias'; proof_alias.symlink_to(tmp_path/'proofs', target_is_directory=True)
        renewal['current_proof']['path'] = str(proof_alias/m.Path(verified['proof_path']).name)
        verified['proof_path'] = renewal['current_proof']['path']
    elif drift == 'copy':
        copy_path = tmp_path/'copied-previous.json';copy_path.write_bytes(previous.read_bytes())
        renewal['previous_runtime']['path'] = str(copy_path)
    elif drift == 'login': (home/'auth.json').write_text('another login')
    elif drift == 'binary': client.cli_identity['file_sha256'] = 'another binary'
    elif drift == 'model': verified['identity']['model'] = 'another model'
    elif drift == 'path': verified['identity']['cli']['path'] = '/foreign/codex'
    elif drift == 'previous': renewal['previous_runtime']['file_sha256'] = '0'*64
    elif drift == 'proof': m.Path(verified['proof_path']).write_text('{}')
    elif drift == 'missing': m.Path(verified['proof_path']).unlink()
    elif drift == 'owner': renewal['authorized_by'] = 'model'
    elif drift == 'direction': renewal['owner_direction'] = ''
    path = tmp_path/'renewal.json'; path.write_text(json.dumps(renewal))
    monkeypatch.setenv('WKP_CODEX_RUNTIME_RENEWAL', str(path))
    monkeypatch.setattr(m, 'CodexSubscriptionClient', lambda **kw: client)
    monkeypatch.setattr(client, 'generate_json', lambda *a, **k: pytest.fail('no diagnostic fallback'))
    if drift not in (None, 'alias'):
        with pytest.raises((ValueError, RuntimeError, FileNotFoundError)):
            mod.canonical_runtime()
    else:
        result = mod.canonical_runtime()
        assert result['reused'] and result['new_model_calls'] == 0
        assert mod.canonical_runtime() == result
    assert calls == [True]
