"""Wiring/cache/safety tests; these do not claim a real model semantic pass."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy, DEFAULT_CVP_POLICY_PATH, cvp_policy_prompt_sha256s
from backend.pipeline.viewpoint_semantic_contract import (
    semantic_prompt, policy_prompt, CONTRACT_PATH, CONTEXTUAL_SEMANTICS,
)

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / 'backend/pipeline/prompts'


@pytest.mark.parametrize('role', ['proposal', 'review', 'correction', 'consolidation', 'grouping'])
def test_every_new_role_uses_same_rules_and_real_prompt_hash(role):
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    policy = load_cvp_policy(DEFAULT_CVP_POLICY_PATH)
    composed = policy_prompt(policy, role, PROMPTS)
    assert composed.startswith(CONTRACT_PATH.read_text())
    assert composed.count('## 统一语义规则：') == 1
    assert runner._role_prompt(policy, role) == composed
    assert cvp_policy_prompt_sha256s(policy, prompt_dir=PROMPTS)[role] == sha256_json(composed)
    assert '任一方向不成立，它就是' not in composed
    assert '任一方向成立就不是 member' not in composed
    assert '两个 component 互相蕴含，就是同一个观点' not in composed
    assert '举不出，就是同一个真值条件的两种说法' not in composed


@pytest.mark.parametrize('filename', ['wang_cvp_resolution_policy_v1.json',
                                     'wang_cvp_resolution_policy_v2.json',
                                     'wang_cvp_resolution_policy_v3.json'])
def test_frozen_policies_and_prompts_stay_exact_legacy(filename):
    policy = load_cvp_policy(DEFAULT_CVP_POLICY_PATH.with_name(filename))
    for role, name in policy['prompts'].items():
        assert policy_prompt(policy, role, PROMPTS) == (PROMPTS/name).read_text()


@pytest.mark.parametrize('old_name,new_name',[('v2','v4'),('v3','v5')])
def test_new_policy_preserves_actual_models_scope_ceiling_and_efforts(old_name,new_name):
    old = load_cvp_policy(DEFAULT_CVP_POLICY_PATH.with_name('wang_cvp_resolution_policy_'+old_name+'.json'))
    new = load_cvp_policy(DEFAULT_CVP_POLICY_PATH.with_name('wang_cvp_resolution_policy_'+new_name+'.json'))
    for field in ['proposal', 'review', 'grouping', 'correction', 'consolidation',
                  'batch_size', 'max_request_bytes', 'call_timeout_seconds', 'validator_version']:
        assert old[field] == new[field]
    assert old != new


def test_unknown_semantic_contract_is_not_silently_downgraded():
    with pytest.raises(ValueError): semantic_prompt('role', 'unknown')


def test_all_factories_compile_policy_prompt_not_hardcoded_file(monkeypatch):
    from backend.pipeline import viewpoint_batch_resolution_runner as r
    monkeypatch.setattr(r, '_subscription_client', lambda provider,model,effort:
        SimpleNamespace(model=model, backend=provider+'_subscription', reasoning_effort=effort))
    policy = load_cvp_policy(DEFAULT_CVP_POLICY_PATH)
    factories = {'proposal':r.build_proposer, 'review':r.build_reviewer,
                 'correction':r.build_reconsiderer, 'consolidation':r.build_consolidator,
                 'grouping':r.build_grouper}
    for role, factory in factories.items():
        args = {} if role=='grouping' else {'provider':policy[role]['provider']}
        adapter = factory(policy[role]['model'], 'high', cvp_policy=policy, **args)
        assert adapter.prompt_sha256 == sha256_json({'prompt':policy_prompt(policy,role,PROMPTS)})
        assert adapter._shared_record_payload


def test_independent_reader_loads_rules_without_production_imports():
    spec = importlib.util.spec_from_file_location('contextual_audit', ROOT/'scripts/audit-library.py')
    audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
    assert audit.contextual_semantic_rules(CONTEXTUAL_SEMANTICS) == CONTRACT_PATH.read_text()
    new = audit._viewpoint_prompt('V', {'core_proposition':'命题'}, ['真实输入'])
    old = audit._viewpoint_prompt('V', {'core_proposition':'命题'}, ['真实输入'], semantic_contract_version='legacy_v1')
    assert new.startswith(CONTRACT_PATH.read_text())
    assert old == audit._legacy_viewpoint_prompt('V', {'core_proposition':'命题'}, ['真实输入'])
    assert new != old and '真实输入' in new
    import ast
    tree = ast.parse(__import__('inspect').getsource(audit.contextual_semantic_rules))
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(tree))


def test_fallback_reconstructs_the_actual_audit_version(monkeypatch):
    from backend.pipeline import viewpoint_content_blocked_fallback as f
    calls = []
    monkeypatch.setattr(f,'capture',lambda *args,**kwargs: calls.append((args,kwargs)))
    f.capture_for_audit({'meta':{}},'4','V')
    import hashlib
    f.capture_for_audit({'meta':{'semantic_contract_version':CONTEXTUAL_SEMANTICS,
        'semantic_contract_file_sha256':hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()}},'4','V')
    assert calls == [(('4','V'),{}), (('4','V'),{'semantic_contract_version':CONTEXTUAL_SEMANTICS})]


@pytest.mark.parametrize('version,sha',[(CONTEXTUAL_SEMANTICS,None),(CONTEXTUAL_SEMANTICS,'changed'),('unknown','changed')])
def test_fallback_does_not_reinterpret_saved_audit_with_changed_rules(monkeypatch,version,sha):
    from backend.pipeline import viewpoint_content_blocked_fallback as f
    monkeypatch.setattr(f,'capture',lambda *args,**kwargs:pytest.fail('no current input under changed contract'))
    with pytest.raises(ValueError,match='contract identity/physical SHA'):
        f.capture_for_audit({'meta':{'semantic_contract_version':version,'semantic_contract_file_sha256':sha}},'4','V')


def test_new_supports_adapter_does_not_relabel_historical_config():
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter, PROMPT
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high')
    old=link_review_adapter(client,PROMPT)
    new=link_review_adapter(client,PROMPT,contextual_semantics=True)
    assert old.prompt_sha256 == sha256_json({'prompt':PROMPT})
    assert new.prompt_sha256 != old.prompt_sha256
    assert new._prompt.startswith(CONTRACT_PATH.read_text())
    assert new._shared_record_payload and not old._shared_record_payload
    assert new.max_request_bytes == old.max_request_bytes == 500000
    with pytest.raises(ValueError): link_review_adapter(client,'other owner scope',contextual_semantics=True)


def test_new_semantic_transport_is_lossless_without_old_cache_relabeling():
    import json
    from backend.api.canonical_repository.viewpoint_request_encoding import decode_shared_records,decode_shared_text,RECORD_FORMAT,FORMAT
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter,PROMPT
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high')
    record={'claim':{'statement':'保留必要条件及充分条件。'*200},'sources':[{'text':'完整原文\n  空白ψυχή'*500}]}
    packet={'targets':[deepcopy(record) for _ in range(12)]}
    before=deepcopy(packet)
    old=link_review_adapter(client,PROMPT)
    new=link_review_adapter(client,PROMPT,contextual_semantics=True)
    from backend.api.canonical_repository.viewpoint_request_encoding import encode_shared_records
    wire=json.loads(encode_shared_records(packet))
    restored=decode_shared_records(wire) if wire.get('format')==RECORD_FORMAT else decode_shared_text(wire) if wire.get('format')==FORMAT else wire
    assert restored==before==packet
    assert new.generation_config_sha256!=old.generation_config_sha256
    # This synthetic packet may fall back to v1 when references cost more;
    # either choice restores all fields. The actual g037 packet uses v2.
    proof=new.request_encoding_proof(packet)
    assert proof['semantic_payload_sha256']==sha256_json(packet)


@pytest.mark.parametrize('compact', [False,True])
def test_historical_source_adjudication_config_is_exactly_reused(tmp_path,compact):
    from backend.pipeline import viewpoint_audit_source_adjudication as a
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high')
    old=StructuredJsonReviewerAdapter(client=client,prompt=a.PROMPT,response_model=a.Findings,
        schema_name='wang_audit_source_findings_v1',compact_payload=compact)
    cache=tmp_path/'raw.json'
    write_immutable(cache,{'prompt_sha256':old.prompt_sha256,'generation_config_sha256':old.generation_config_sha256})
    selected=a.source_review_adapter(client,cache)
    assert selected.prompt_sha256 == old.prompt_sha256
    assert selected.generation_config_sha256 == old.generation_config_sha256


@pytest.mark.parametrize('verdict', ['audit_objection_rejected','audit_objection_upheld','unresolved'])
@pytest.mark.parametrize('reworded', [False,True])
def test_existing_unchanged_target_never_gets_fresh_automatic_arbitration(monkeypatch,tmp_path,verdict,reworded):
    from backend.pipeline import viewpoint_adjudication_history as h
    old={'item_id':'4:V:C','claim':{'statement':'原话','revision':3},
         'sources':[{'file_sha256':'exact'}], 'audit_finding':{'reason':'旧理由'}}
    current=deepcopy(old)
    if reworded: current['audit_finding']={'reason':'新理由','issue':'scope_mismatch'}
    prior={'items':[old]}
    monkeypatch.setattr(h,'read_artifact',lambda path:prior)
    monkeypatch.setattr(h,'validated_history',lambda root:(prior,{'4:V:C':{'verdict':verdict}}))
    with pytest.raises(h.AdjudicationAlreadyRecorded):
        h.assert_no_readjudication({'items':[current]},[tmp_path])


@pytest.mark.parametrize('field', ['claim','connections','evidence','sources','viewpoint'])
def test_changed_target_is_not_treated_as_old_approval(monkeypatch,tmp_path,field):
    from backend.pipeline import viewpoint_adjudication_history as h
    old={'item_id':'4:V:C',field:{'version':'old'},'audit_finding':{'reason':'old'}}
    new=deepcopy(old); new[field]={'version':'changed'}
    monkeypatch.setattr(h,'read_artifact',lambda path:{'items':[old]})
    monkeypatch.setattr(h,'validated_history',lambda root:pytest.fail('unrelated old target'))
    assert h.assert_no_readjudication({'items':[new]},[tmp_path]) is None  # No approval returned.


def history_fixture(tmp_path):
    from backend.pipeline import viewpoint_audit_source_adjudication as a
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    def seal(body): return body | {'artifact_sha256':sha256_json(body)}
    source=tmp_path/'source'; source.write_text('own original quote')
    audit=tmp_path/'audit.json'; write_immutable(audit, {'layers':{}})
    root=tmp_path/'history'
    packet=seal({'audit_binding':binding(audit), 'items':[{
        'item_id':'4:v:c', 'claim':{'claim_id':'c','revision':3},
        'audit_finding':{'reason':'exact prior objection'},
        'sources':[{'source_id':'s','path':str(source),'file_sha256':binding(source)['file_sha256'],
                    'paragraphs':[{'paragraph_key':'S0001','text':'own original quote'}]}]}]})
    client=SimpleNamespace(model='gpt-6.1-sol',backend='codex_subscription',reasoning_effort='high')
    adapter=StructuredJsonReviewerAdapter(client=client,prompt=a.PROMPT,response_model=a.Findings,
        schema_name='wang_audit_source_findings_v1',compact_payload=True)
    response={'findings':[{'item_id':'4:v:c','verdict':'audit_objection_rejected',
        'source_id':'s','paragraph_key':'S0001','exact_quote':'own original quote','reason':'source explains the boundary'}]}
    raw=seal({'model_id':adapter.model_id,'backend':adapter.backend,'prompt_sha256':adapter.prompt_sha256,
        'generation_config_sha256':adapter.generation_config_sha256,'request_payload_sha256':sha256_json(packet),
        'response_sha256':sha256_json(response),'response':response})
    handoff=seal({'packet_sha256':packet['artifact_sha256'],'maximum_model_calls':1,'master_data_mutations':0,
        'actions':[{'item_id':'4:v:c','verdict':'audit_objection_rejected','gpt':response['findings'][0]}]})
    for name, value in [('packet.json',packet),('raw-gpt.json',raw),('gpt-final-handoff.json',handoff)]:
        write_immutable(root/name,value)
    return root,packet


def test_real_sha_bound_history_is_recognized_without_model_call(tmp_path,monkeypatch):
    from backend.pipeline import viewpoint_adjudication_history as h
    from backend.pipeline import viewpoint_audit_source_adjudication as a
    root,packet=history_fixture(tmp_path)
    assert h.validated_history(root)[0]==packet
    monkeypatch.setattr(a,'build_packet',lambda *args:packet)
    monkeypatch.setattr(h,'campaign_history',lambda *args:[root])
    monkeypatch.setattr(a,'subscription_client',lambda *args:pytest.fail('no new client/model call'))
    with pytest.raises(h.AdjudicationAlreadyRecorded):
        a.run(tmp_path/'audit.json',tmp_path/'new',object(),final_gpt=True)
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    assert read_artifact(tmp_path/'new/failure.json')['new_model_calls']==0
    assert not (tmp_path/'new/raw-gpt.json').exists()


@pytest.mark.parametrize('field',['model_id','backend','prompt_sha256','generation_config_sha256',
                                 'request_payload_sha256','response_sha256','raw_artifact',
                                 'action','source','audit'])
def test_history_provenance_drift_cannot_grant_or_hide_reuse(tmp_path,field):
    import json
    from backend.pipeline import viewpoint_adjudication_history as h
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    root,_=history_fixture(tmp_path)
    if field in {'source','audit'}:
        (tmp_path/('audit.json' if field=='audit' else 'source')).write_text('changed')
    else:
        path=root/('gpt-final-handoff.json' if field=='action' else 'raw-gpt.json')
        value=read_artifact(path)
        if field=='action': value['actions'][0]['verdict']='audit_objection_upheld'
        elif field=='raw_artifact': value['artifact_sha256']='invalid'
        else: value[field]='invalid'
        if field!='raw_artifact': value['artifact_sha256']=sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'})
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError): h.validated_history(root)


def test_new_consolidation_has_real_unresolved_exit_legacy_schema_unchanged():
    from backend.api.canonical_repository.viewpoint_batch_resolution import (
        decode_consolidation, BatchResolutionError, IdentityConsolidationResponse,
        ContextualIdentityConsolidationResponse,
    )
    old={'schema_version':'wang_canonical_viewpoint_identity_consolidation_v1',
         'verdicts':[{'local_key':'a','verdict':'new','reason':'independent claim'}]}
    new={**old,'schema_version':'wang_canonical_viewpoint_identity_consolidation_v2','unresolved_items':[]}
    assert decode_consolidation(new,['a'])==IdentityConsolidationResponse.model_validate(old)
    new['verdicts']=[]
    new['unresolved_items']=[{'local_key':'a','reason':'source does not resolve who is meant'}]
    with pytest.raises(BatchResolutionError,match='unresolved identity'): decode_consolidation(new,['a'])
    with pytest.raises(BatchResolutionError,match='cover candidates'): decode_consolidation(new,['other'])
    new['verdicts']=old['verdicts']
    with pytest.raises(ValueError,match='contradictory'): ContextualIdentityConsolidationResponse.model_validate(new)
    with pytest.raises(ValueError): IdentityConsolidationResponse.model_validate(new)


def test_new_consolidator_response_version_is_not_used_for_old_policy(monkeypatch):
    from backend.pipeline import viewpoint_batch_resolution_runner as r
    monkeypatch.setattr(r,'_subscription_client',lambda *args:SimpleNamespace(
        model='claude-opus-5-5',backend='claude_subscription',reasoning_effort='high'))
    for filename,version in [('v3','v1'),('v5','v2')]:
        policy=load_cvp_policy(DEFAULT_CVP_POLICY_PATH.with_name(f'wang_cvp_resolution_policy_{filename}.json'))
        adapter=r.build_consolidator('claude-opus-5-5','high',cvp_policy=policy)
        assert adapter._schema_name==f'wang_canonical_viewpoint_identity_consolidation_{version}'


def test_frozen_prefix_helper_never_uses_new_default_semantic_identity(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_batch_resolution_runner as r
    from backend.pipeline.viewpoint_model_cache_handoff import verify_prefix
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    policies=[]
    def factory(*args,cvp_policy,**kwargs):
        policies.append(cvp_policy)
        adapter=SimpleNamespace()
        adapter.compact_copy=lambda:adapter
        return adapter
    for name in ('build_proposer','build_reviewer','build_consolidator','build_reconsiderer'):
        monkeypatch.setattr(r,name,factory)
    monkeypatch.setattr(r,'run_batch',lambda **kwargs:None)
    write_immutable(tmp_path/'prior-model-cache.json',{'raw_bindings':{}})
    with pytest.raises(ValueError,match='mandatory model review'):
        verify_prefix({'scope_label':'frozen','claims':[],'registry_context':[]},'batch',tmp_path,500000)
    old=load_cvp_policy(DEFAULT_CVP_POLICY_PATH.with_name('wang_cvp_resolution_policy_v3.json'))
    assert policies==[old]*4
