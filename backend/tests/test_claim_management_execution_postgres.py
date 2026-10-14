"""Complete v2 paths on disposable SQL with explicit synthetic model responses.

HTTP/subscription stubs are not independent production approvals. The tests
exercise the real saved intent/raw validators, transactions and state machine.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
from backend.pipeline.source_reconciliation_runner import binding, write
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres

SPEC = importlib.util.spec_from_file_location('claim_operation_auditor', Path(__file__).parents[2] / 'scripts/audit-library.py')
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def passing(packet):
    return {'proposal_sha256': packet['candidate_sha256'], 'verdicts': [
        {'record_key': key, 'approved': True, 'problems': [], 'reason': 'synthetic complete source judgment'} for key in packet['denominator']],
        'findings': [], 'prior_findings': [dict(finding_id=key, disposition='resolved', reason='synthetic corrected source',
            citations=[{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}])
            for key in packet['unresolved']], 'answered_questions': packet.get('required_questions', packet['original_goal']['required_questions'])}


class SyntheticPorts:
    def __init__(self, store, tmp_path, *, proposal=None, opus=None, gemini=None):
        self.store, self.tmp_path = store, tmp_path
        self.calls = []
        self.proposal, self.opus, self.gemini = proposal, opus, gemini

    def claim_management_adapter(self, role, policy):
        adapter = claim_management_model_adapter(role, policy)
        def generate(packet):
            self.calls.append(role)
            if role == 'opus':
                return self.opus(packet) if self.opus else passing(packet)
            if self.proposal:
                return self.proposal(packet)
            result = cm.existing_candidate(packet['before'])
            payload = {'claim_id': 'C', 'statement': '甲是假设，不是断言。', 'claim_type': 'exegesis',
                       'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': []}
            result.records.append(cm.source.Row(collection='claims', object_id='C', payload=payload))
            result.dispositions.append(cm.ObjectDisposition(record_key='claims/C', action='create', reason='synthetic own source',
                citations=[cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]))
            return result.model_dump(mode='json')
        adapter.generate = generate
        return adapter

    def backup(self, root):
        raise AssertionError('Claim operations must not call the whole-registry backup port')

    def claim_management_audit(self, root, audit_input):
        self.calls.append('gemini')
        store = AUDIT.Store(self.store.database_url)
        docs = store._query("SELECT json_build_object('object_id',object_id,'payload',payload)::text "
                            "FROM wang_knowledge.objects WHERE collection='source_documents'")
        sources = AUDIT.SourceIndex(docs, self.tmp_path)
        model = AUDIT.IndependentModel(audit_input.get('model', 'gemini-3.8-flash'), 'synthetic-no-provider-call', cache_dir=root / 'independent-audit/cache',
            preserve_full_context=True, durable_once=True, max_request_bytes=audit_input['budget']['max_request_bytes'],
            max_output_tokens=audit_input['budget']['max_output_tokens'], json_schema=True)
        def fake_judge(prompt, schema):
            packet = json.loads(prompt.rsplit('\n', 1)[1])
            response = self.gemini(packet) if self.gemini else passing(packet)
            wire = json.dumps({'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {
                'temperature': 0, 'responseMimeType': 'application/json', 'responseJsonSchema': schema,
                'maxOutputTokens': audit_input['budget']['max_output_tokens']}}).encode()
            import hashlib
            intent = {'model': model.model, 'prompt': prompt, 'schema': schema, 'request_bytes': len(wire),
                      'request_sha256': hashlib.sha256(wire).hexdigest(), 'maximum_calls': 1, 'automatic_retry': False}
            cached = model._cache_path(prompt)
            AUDIT.write_audit_immutable(cached.with_name(cached.stem + '.call-intent.json'), intent)
            AUDIT.write_audit_immutable(cached.with_name(cached.stem + '.provider-response.json'), {
                'request': intent, 'provider_response': {'candidates': [{'finishReason': 'STOP',
                    'content': {'parts': [{'text': json.dumps(response)}]}}]}})
            AUDIT.write_audit_immutable(cached, response)
            return response
        model.judge = fake_judge
        report = AUDIT.audit_claim_operation(store, sources, model, audit_input)
        AUDIT.write_audit_immutable(root / 'independent-audit/audit.json', report)


def test_create_complete_commit_readback_independent_audit_and_no_replay(action,monkeypatch):
    store, request, parent, _, tmp_path, _ = action
    ports = SyntheticPorts(store, tmp_path)
    result = execute_claim_management(store, request, root=tmp_path / 'original-root', ports=ports)
    assert result['status'] == 'completed', result
    assert result['qualification_confirmed'] is True
    assert ports.calls == ['gpt', 'opus', 'gemini']
    assert store.get_record_state('claims', 'C')['payload']['review_status'] == 'candidate'
    assert store.claim_management_qualification('C')['ready'] is True
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM wang_knowledge.change_sets WHERE source_kind=%s", (cm.VERSION,)).fetchone() == (1,)
    assert execute_claim_management(store, request, root=tmp_path / 'original-root', ports=ports) == result
    assert ports.calls == ['gpt', 'opus', 'gemini']
    # Consuming a saved audit validates physical sources once, through the
    # same path that rejects forged packet snapshots.
    from backend.pipeline.cvp_sql_actions import validate_claim_stage_receipt, validate_claim_audit
    from backend.pipeline.source_reconciliation_runner import read, body
    saved = store.get_cvp_group(next(iter(store.get_cvp_group(parent['run_id'])['latest_rounds'].values())))
    receipt = body(read(Path(saved['stage_results']['audit']['path'])))
    load = cm.load_authority
    reads = []
    def measured_load(*args,**kwargs):
        reads.append(set(kwargs['selected_source_ids']))
        return load(*args,**kwargs)
    monkeypatch.setattr(cm,'load_authority',measured_load)
    assert validate_claim_stage_receipt(receipt)[3] is True
    assert reads == [{'S'}]
    altered = deepcopy(saved['materials'])
    altered['sources']['S']['paragraphs']['S0001']['text'] = 'forged source packet'
    report = json.loads(Path(receipt['audit']['path']).read_text())
    with pytest.raises(ValueError,match='source/policy drift'):
        validate_claim_audit(report,altered,cm.Candidate.model_validate(saved['candidate']))


def test_gpt_unknown_call_pauses_without_resending(action):
    store, request, _, _, tmp_path, _ = action
    def failed(packet):
        raise RuntimeError('synthetic unknown transport outcome')
    ports = SyntheticPorts(store, tmp_path, proposal=failed)
    result = execute_claim_management(store, request, root=tmp_path / 'original-root', ports=ports)
    assert result['reason'] == 'model_failed'
    assert store.get_record_state('claims', 'C') is None
    recovered = execute_claim_management(store, request, root=tmp_path / 'original-root', ports=ports)
    assert recovered['reason'] == 'call_uncertain'
    assert ports.calls == ['gpt']


@pytest.mark.parametrize('failure',['late_validation','committed_reply_lost'])
def test_claim_transaction_recovers_without_backup_or_repeated_models(action,monkeypatch,failure):
    store,request,_,_,tmp_path,_=action
    ports=SyntheticPorts(store,tmp_path)
    if failure=='late_validation':
        original=store._assert_transaction_edges_and_reviews
        def fail(*args,**kwargs):raise RuntimeError('synthetic late transaction failure')
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',fail)
    else:
        original=store.apply_plan
        def fail(plan,*args,**kwargs):
            result=original(plan,*args,**kwargs)
            if plan.source_kind==cm.VERSION:
                raise RuntimeError('synthetic committed reply lost')
            return result
        monkeypatch.setattr(store,'apply_plan',fail)
    root=tmp_path/'original-root'
    first=execute_claim_management(store,request,root=root,ports=ports)
    assert first['status']=='paused'
    assert ports.calls==['gpt','opus']
    assert (store.get_record_state('claims','C') is None)==(failure=='late_validation')
    with store.connect() as conn:
        count=conn.execute('SELECT count(*) FROM wang_knowledge.change_sets WHERE source_kind=%s',(cm.VERSION,)).fetchone()[0]
    assert count==(0 if failure=='late_validation' else 1)
    method='_assert_transaction_edges_and_reviews' if failure=='late_validation' else 'apply_plan'
    monkeypatch.setattr(store,method,original)
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['gpt','opus','gemini']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM wang_knowledge.change_sets WHERE source_kind=%s',(cm.VERSION,)).fetchone()==(1,)
    assert store.get_record_state('claims','C')['revision']==1
    assert store.claim_management_qualification('C')['ready']


def test_zero_revision_budget_stops_before_proposal_and_preserves_existing_claim(action):
    store, original, _, _, root, _ = action
    seed_claims(store,['C'])
    before = store.get_record_state('claims','C')
    request, output = operation_request(action,'Update',['C'])
    policy = cm.Policy.model_validate_json(Path(original.policy.path).read_text())
    policy.budget.max_revisions = 0
    policy_file = root/'zero-revision-policy.json'
    policy_file.write_text(policy.model_dump_json())
    request.policy = cm.source.FileBinding.model_validate(binding(policy_file))
    direction_file = Path(request.authorization.path)
    direction = json.loads(direction_file.read_text())
    direction['policy'] = request.policy.model_dump(mode='json')
    direction_file.write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(direction_file))
    ports = SyntheticPorts(store,root,opus=rejected)
    result = execute_claim_management(store,request,root=output,ports=ports)
    assert result['status']=='paused' and result['reason']=='budget_exhausted',result
    assert execute_claim_management(store,request,root=output,ports=ports)==result
    assert ports.calls == ['opus']
    assert store.get_record_state('claims','C') == before
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    assert len(parent['latest_rounds']) == 1
    assert sum(slot['state']=='started' for slot in parent['call_reservations'].values()) == 0


def operation_request(action, operation, targets, *, confirm_current=False):
    store, original, _, _, tmp_path, _ = action
    root = tmp_path / (operation + '-' + uuid.uuid4().hex)
    auth_file = tmp_path / (root.name + '-direction.json')
    permissions = [] if operation in {'Read','Review','Create'} else [
        cm.source.Permission(collection='claims', object_id=cid, fields=['statement']) for cid in targets]
    request = cm.Request(action_id=root.name, operation=operation, allowed_operations=[operation],
        target_keys=['claims/' + cid for cid in targets], source_bindings=original.source_bindings,
        authorization={'path': str(auth_file), 'file_sha256': 'a' * 64}, policy=original.policy,
        permissions=permissions, creation_permissions=original.creation_permissions if operation in {'Create','Split','Merge'} else [],
        confirm_current=confirm_current)
    for grant in request.creation_permissions:
        grant.fields = sorted(set(grant.fields) | set(cm.KNOWLEDGE_COLLECTIONS[grant.collection][0].model_fields))
    direction = {'schema_version': cm.VERSION, 'authorized_by': 'owner', 'authorization_quote': 'Synthetic isolated test authority',
                 'human_content_approval': False, 'request': request.model_dump(mode='json', exclude={'authorization','policy'}),
                 'policy': request.policy.model_dump()}
    auth_file.write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(auth_file))
    return request, root


def seed_claims(store, ids):
    store.apply_plan(store.plan_package({'package_id': 'synthetic-' + uuid.uuid4().hex, 'claims': [
        {'claim_id': cid, 'statement': '甲是假设。' + cid, 'claim_type': 'exegesis',
         'occurrences': [{'source_id': 'S', 'anchors': []}]} for cid in ids]}))


@pytest.mark.parametrize('operation',['Create','Update','Retire','Restore','Split','Merge'])
def test_opus_only_completes_with_real_program_readback_and_no_gemini(action,operation):
    store,original,_,_,tmp_path,_=action
    targets=['C','D'] if operation=='Merge' else ['C']
    if operation!='Create':seed_claims(store,targets)
    if operation=='Restore':
        store.apply_plan(store.plan_retirement([('claims','C')],reason='synthetic retirement',package_id='before-restore'))
    request,root=operation_request(action,operation,[] if operation=='Create' else targets)
    request.policy=cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY))
    direction_path=Path(request.authorization.path)
    direction=json.loads(direction_path.read_text());direction['policy']=request.policy.model_dump(mode='json')
    direction_path.write_text(json.dumps(direction));request.authorization=cm.source.FileBinding.model_validate(binding(direction_path))
    def propose(packet):
        result=cm.existing_candidate(packet['before'])
        if operation in {'Retire','Restore','Split','Merge'}:
            action_name='restore' if operation=='Restore' else 'retire'
            for cid in targets:
                result.dispositions=[d for d in result.dispositions if d.record_key!='claims/'+cid]+[
                    cm.ObjectDisposition(record_key='claims/'+cid,action=action_name,reason='synthetic',citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')])]
                if operation=='Restore':
                    result.records.append(cm.source.Row(collection='claims',object_id=cid,payload=packet['before']['claims/'+cid]['payload']))
        new_ids=['C'] if operation=='Create' else ['A','B'] if operation=='Split' else ['M'] if operation=='Merge' else []
        for cid in new_ids:
            result.records.append(cm.source.Row(collection='claims',object_id=cid,payload={'claim_id':cid,'statement':'甲是假设。','claim_type':'exegesis','occurrences':[{'source_id':'S','anchors':[]}]}))
            result.dispositions.append(cm.ObjectDisposition(record_key='claims/'+cid,action='create',reason='synthetic',citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]))
        if operation in {'Split','Merge'}:result.replacements={'claims/'+cid:['claims/'+n for n in new_ids] for cid in targets}
        return result.model_dump(mode='json')
    ports=SyntheticPorts(store,tmp_path,proposal=propose)
    def forbidden(*a,**k):raise AssertionError('Gemini is disabled')
    ports.claim_management_audit=forbidden
    result=execute_claim_management(store,request,root=root,ports=ports)
    assert result['status']=='completed',result
    assert result['validation_kind']=='opus_and_program_readback'
    assert result['independent_audit_complete'] is False
    assert ports.calls==(['opus'] if operation=='Update' else ['gpt','opus'])
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    assert not any(v['role']=='gemini' for v in parent['call_reservations'].values())
    child=store.get_cvp_group(next(iter(parent['latest_rounds'].values())))
    assert child['program_readback_complete'] and not child['independent_audit_complete']
    if operation in {'Create','Update','Restore'}:
        qualified=store.claim_management_qualification('C')
        assert qualified['ready'] and qualified['independent_audit_complete'] is False
    assert execute_claim_management(store,request,root=root,ports=ports)==result


def test_argon_update_completes_without_qualifying_readonly_related_claim(action):
    store, _, _, _, tmp_path, _ = action
    seed_claims(store, ['C', 'D'])
    store.apply_plan(store.plan_package({'package_id':'synthetic-related-context', 'claim_relations':[
        {'claim_relation_id':'R', 'from_id':'C', 'to_id':'D', 'relation_type':'supports', 'reason':'synthetic context'}]}))
    request, root = operation_request(action, 'Update', ['C'])
    path = Path(cm.__file__).parent / 'policies/wang_claim_management_argon_v1.json'
    request.policy = cm.source.FileBinding.model_validate(binding(path))
    direction = json.loads(Path(request.authorization.path).read_text())
    direction['policy'] = request.policy.model_dump(mode='json')
    Path(request.authorization.path).write_text(json.dumps(direction))
    request.authorization = cm.source.FileBinding.model_validate(binding(Path(request.authorization.path)))
    original = {cid:store.get_record_state('claims',cid) for cid in ('C','D')}
    ports = SyntheticPorts(store, tmp_path)
    result = execute_claim_management(store,request,root=root,ports=ports)
    assert result['status'] == 'completed', result
    assert result['original_target_keys'] == ['claims/C']
    assert ports.calls == ['opus','gemini']
    assert store.claim_management_qualification('C')['ready']
    assert store.claim_management_qualification('D') is None
    assert {cid:store.get_record_state('claims',cid) for cid in original} == original
    parent = store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    child = store.get_cvp_group(next(iter(parent['latest_rounds'].values())))
    report = json.loads((root/child['run_id']/'independent-audit/audit.json').read_text())
    assert report['model'] == 'gemini-4-argon'
    assert 'claims/D' in report['packet']['denominator']
    from backend.pipeline.cvp_sql_actions import validate_claim_audit
    altered = deepcopy(child['materials'])
    altered['policy']['models']['gemini']['model_id'] = 'gemini-3.8-flash'
    with pytest.raises(ValueError,match='identity drift'):
        validate_claim_audit(report,altered,cm.Candidate.model_validate(child['candidate']))
    assert execute_claim_management(store,request,root=root,ports=ports) == result
    assert ports.calls == ['opus','gemini']


def test_production_auditor_uses_current_checkout_policy_and_actual_database(monkeypatch,tmp_path):
    from backend.pipeline import source_reconciliation_runtime as runtime
    project = tmp_path / 'current-checkout'
    project.mkdir()
    monkeypatch.setattr(runtime,'PROJECT',project)
    calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess,'run',run)
    monkeypatch.setenv('KNOWLEDGE_DATABASE_URL','postgresql://wrong.invalid/database')
    store=SimpleNamespace(database_url='postgresql://isolated.invalid/database')
    root=tmp_path/'operation'
    runtime.ProductionPorts(store).claim_management_audit(root,{'model':'gemini-4-argon'})
    argv,options=calls[0]
    assert options['cwd']==project
    assert argv[0]==str(project/'backend/.venv/bin/python')
    assert argv[argv.index('--model')+1]=='gemini-4-argon'
    assert options['env']['KNOWLEDGE_DATABASE_URL']==store.database_url
    assert (root/'independent-audit/stdout.log').is_file()
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1))
    with pytest.raises(ValueError,match='inspect saved logs'):
        runtime.ProductionPorts(store).claim_management_audit(root,{'model':'gemini-4-argon'})


def test_claim_model_start_does_not_require_backup_disk(monkeypatch,tmp_path):
    from backend.pipeline import source_reconciliation_runtime as runtime
    from backend.pipeline import viewpoint_backup_archive as backup
    monkeypatch.setattr(runtime,'PROJECT',tmp_path)
    monkeypatch.setattr(runtime.subprocess,'check_output',lambda *a,**k:'')
    monkeypatch.setattr(runtime.shutil,'disk_usage',lambda *a:SimpleNamespace(free=4*1024**3))
    def missing():raise ValueError('SSD unavailable')
    monkeypatch.setattr(backup,'verify_volume',missing)
    ports=runtime.ProductionPorts(SimpleNamespace(database_url='synthetic'))
    for role in ('gpt','opus','gemini'):
        ports.before_claim_model_call(role)
    # The separate old source/CVP transport keeps its original contract.
    with pytest.raises(ValueError,match='SSD unavailable'):
        ports.before_model_call('initial_review')


def rejected(packet, key='claims/C', *, category='content'):
    report = passing(packet)
    for verdict in report['verdicts']:
        if verdict['record_key'] == key:
            verdict.update(approved=False, problems=['synthetic concrete problem'])
    report['findings'] = [{'target_key': key, 'component': 'statement', 'category': category,
        'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}],
        'reason': 'synthetic statement problem', 'acceptance_condition': 'preserve the actual assumption',
        'human_question': 'confirm source lettering' if category == 'source_uncertain' else None}]
    return report


def corrected(packet):
    result = cm.existing_candidate(packet['before'], replacements=packet['replacements'])
    result.operation = 'Update'
    payload = deepcopy(packet['before']['claims/C']['payload'])
    payload['statement'] = payload['statement'] + '（保留假设条件）'
    result.records = [cm.source.Row(collection='claims', object_id='C', payload=payload)]
    result.dispositions = [d for d in result.dispositions if d.record_key != 'claims/C'] + [
        cm.ObjectDisposition(record_key='claims/C', action='update', reason='synthetic corrected assumption', citations=[
            cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')])]
    return result.model_dump(mode='json')


def test_update_starts_with_real_existing_candidate_then_revises_and_reviews(action):
    store, _, _, _, tmp_path, _ = action
    seed_claims(store, ['C'])
    request, root = operation_request(action, 'Update', ['C'])
    def opus(packet):
        return rejected(packet) if packet['round_number'] == 0 else passing(packet)
    ports = SyntheticPorts(store, tmp_path, proposal=corrected, opus=opus)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus','gpt','opus','gemini']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    assert sum(parent['revision_rounds'].values()) == 1
    assert parent['findings'] == {}
    first = [v for v in parent['finding_occurrences'].values() if v['findings']]
    assert len(first) == 1
    assert store.get_record_state('claims','C')['revision'] == 2


def test_gemini_rejection_preserves_applied_round_then_corrects_new_round(action):
    store, request, _, _, tmp_path, _ = action
    ports = SyntheticPorts(store, tmp_path)
    original = ports.claim_management_adapter
    def adapter(role, policy):
        if role != 'gpt' or 'gpt' not in ports.calls:
            return original(role, policy)
        result = claim_management_model_adapter(role, policy)
        result.generate = lambda packet: (ports.calls.append('gpt'), corrected(packet))[1]
        return result
    ports.claim_management_adapter = adapter
    calls = []
    def gemini(packet):
        calls.append(packet)
        return rejected(packet) if len(calls) == 1 else passing(packet)
    ports.gemini = gemini
    result = execute_claim_management(store, request, root=tmp_path / 'original-root', ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt','opus','gemini','gpt','opus','gemini']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    assert parent['created_keys'] == ['claims/C']
    assert store.get_record_state('claims','C')['revision'] == 2
    with store.connect() as conn:
        rows = conn.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE metadata->>'parent_run_id'=%s ORDER BY (metadata->>'round_number')::int",
                            (parent['run_id'],)).fetchall()
    assert rows[0][0]['checkpoint'] == 'applied' and rows[0][0]['outcome'] == 'revision_required'
    assert rows[1][0]['checkpoint'] == 'audited' and rows[1][0]['outcome'] == 'completed'
    assert len(rows[0][0]['change_set_ids']) == len(rows[1][0]['change_set_ids']) == 1


@pytest.mark.parametrize('confirm_current', [False, True])
def test_review_operation_is_read_only_and_confirmation_has_independent_audit(action, confirm_current):
    store, _, _, _, tmp_path, _ = action
    seed_claims(store, ['C'])
    before = store.get_record_state('claims','C')
    request, root = operation_request(action, 'Review', ['C'], confirm_current=confirm_current)
    ports = SyntheticPorts(store, tmp_path)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert result['qualification_confirmed'] is confirm_current
    assert ports.calls == (['opus','gemini'] if confirm_current else ['opus'])
    assert store.get_record_state('claims','C') == before
    assert (store.claim_management_qualification('C') is not None) is confirm_current


def test_formal_review_findings_atomically_remove_current_qualification_without_content_write(action):
    store, request, _, _, tmp_path, _ = action
    ports = SyntheticPorts(store, tmp_path)
    assert execute_claim_management(store, request, root=tmp_path / 'original-root', ports=ports)['status'] == 'completed'
    before = store.get_record_state('claims','C')
    review, root = operation_request(action, 'Review', ['C'])
    ports = SyntheticPorts(store, tmp_path, opus=lambda packet: rejected(packet))
    result = execute_claim_management(store, review, root=root, ports=ports)
    assert result['status'] == 'completed' and not result['qualification_confirmed'], result
    assert ports.calls == ['opus']
    assert store.get_record_state('claims','C') == before
    assert store.claim_management_qualification('C')['reason'] == 'formal_review_findings'
    create_parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    create_round = store.get_cvp_group(next(iter(create_parent['latest_rounds'].values())))
    assert create_round['independent_audit_complete'] is False
    assert create_round['pending_review_findings']


def test_new_relation_cannot_inherit_a_proof_with_unchanged_claim_pins(action):
    store, request, _, _, tmp_path, _ = action
    assert execute_claim_management(store, request, root=tmp_path / 'original-root',
        ports=SyntheticPorts(store, tmp_path))['status'] == 'completed'
    original = store.get_record_state('claims', 'C')
    seed_claims(store, ['D'])
    store.apply_plan(store.plan_package({'package_id': 'new-related-edge', 'claim_relations': [{
        'claim_relation_id': 'R', 'from_id': 'C', 'to_id': 'D', 'relation_type': 'supports',
        'reason': 'synthetic new current relation'}]}))
    assert store.get_record_state('claims', 'C') == original
    assert store.claim_management_qualification('C')['ready'] is False


def test_partial_source_text_change_does_not_silently_reuse_the_old_proof(action):
    store, request, _, _, tmp_path, _ = action
    assert execute_claim_management(store, request, root=tmp_path / 'original-root',
        ports=SyntheticPorts(store, tmp_path))['status'] == 'completed'
    original = store.get_record_state('claims', 'C')
    # The original quoted paragraph is unchanged; another paragraph adds a
    # limitation. A new applicability Review must precede current reuse.
    path = Path(request.source_bindings['S'].path)
    path.write_text(json.dumps({'script': [{'text': '甲是假设，不是断言。'}, {'text': '但仅针对乙。'}]}))
    assert store.get_record_state('claims', 'C') == original
    assert store.claim_management_qualification('C')['ready'] is False


@pytest.mark.parametrize('operation', ['Retire','Restore','Split','Merge'])
def test_structural_operations_commit_once_preserve_history_and_original_goal(action, operation):
    store, _, _, _, tmp_path, _ = action
    targets = ['C','D'] if operation == 'Merge' else ['C']
    seed_claims(store, targets)
    if operation == 'Restore':
        store.apply_plan(store.plan_retirement([('claims','C')], reason='synthetic before restore', package_id='retired-before'))
    request, root = operation_request(action, operation, targets)
    original = {cid: store.get_record_state('claims',cid) for cid in targets}
    def propose(packet):
        result = cm.existing_candidate(packet['before'])
        for cid in targets:
            key = 'claims/' + cid
            result.dispositions = [d for d in result.dispositions if d.record_key != key] + [
                cm.ObjectDisposition(record_key=key, action='restore' if operation == 'Restore' else 'retire', reason='synthetic structural disposition',
                    citations=[cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')])]
            if operation == 'Restore':
                result.records.append(cm.source.Row(collection='claims', object_id=cid,
                    payload={**packet['before'][key]['payload'], 'statement': '甲是假设，不是断言。'}))
        new_ids = ['A','B'] if operation == 'Split' else ['M'] if operation == 'Merge' else []
        for cid in new_ids:
            payload = {**original[targets[0]]['payload'], 'claim_id': cid, 'statement': '甲是假设。' + cid}
            result.records.append(cm.source.Row(collection='claims', object_id=cid, payload=payload))
            result.dispositions.append(cm.ObjectDisposition(record_key='claims/' + cid, action='create', reason='synthetic replacement',
                citations=[cm.Citation(source_id='S', paragraph_key='S0001', exact_quote='甲是假设，不是断言。')]))
        if new_ids:
            result.replacements = {'claims/' + cid: ['claims/' + new for new in new_ids] for cid in targets}
        return result.model_dump(mode='json')
    ports = SyntheticPorts(store, tmp_path, proposal=propose)
    result = execute_claim_management(store, request, root=root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['gpt','opus','gemini']
    for cid in targets:
        row = store.get_record_state('claims',cid)
        assert row['revision'] == original[cid]['revision'] + 1
        assert row['retired'] is (operation != 'Restore')
        if operation != 'Restore':
            assert row['payload'] == original[cid]['payload']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    child = store.get_cvp_group(next(iter(parent['latest_rounds'].values())))
    history = store.get_change_history(child['change_set_ids'][0])
    assert len({(v['collection'],v['object_id']) for v in history['operations']}) == len(history['operations'])
