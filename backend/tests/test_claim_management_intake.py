"""ID-list intake exercises the real Update executor on disposable SQL."""
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline import claim_management_intake as intake
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


@pytest.fixture
def claims(action):
    store, _, _, _, tmp, _ = action
    store.apply_plan(store.plan_package({'package_id': 'list-case',
        'claims': [{'claim_id': cid, 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E' + cid]}
                   for cid in ['C', 'D']],
        'evidence_steps': [{'evidence_step_id': 'E' + cid, 'statement': '甲是假设。',
            'produced_claim_ids': [cid], 'source_fragment_ids': ['F' + cid],
            'support_eligibility': 'eligible_candidate', 'speaker': 'professor', 'stance': 'asserted'} for cid in ['C', 'D']],
        'source_fragments': [{'fragment_id': 'F' + cid, 'source_id': 'S', 'paragraph_key': 'S0001',
                             'verbatim_excerpt': '甲是假设，不是断言。'} for cid in ['C', 'D']]}))
    return store, tmp / 'list-update'


def pass_review(packet):
    return {'complete_review': True, 'findings': [], 'prior_findings': [], 'binding_assessments': [
        {'claim_key': key, 'claim_use': 'professor_judgment', 'sufficient': True,
         'evidence_keys': list(evidence), 'reason': 'synthetic own bound evidence judgment'}
        for key, evidence in packet['own_evidence_bindings'].items()]}


def test_ids_become_complete_updates_and_resume_without_model_replay(claims, monkeypatch):
    store, root = claims
    ports, starts = {}, []
    def execute(manifest, output):
        plan, jobs = batch.load(manifest, output, store=store)
        assert plan['max_workers'] == 2
        for key, (request, execution) in jobs.items():
            starts.append(key)
            port = ports.setdefault(key, SyntheticPorts(store, execution, opus=pass_review))
            assert execute_claim_management(store, request, root=execution, ports=port)['status'] == 'completed'
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C', 'D', 'C'], root, instruction='Update these Claims', max_workers=2, store=store)
    assert result['status'] == 'completed' and list(result['claims']) == ['C', 'D']
    assert all(row['qualification']['ready'] for row in result['claims'].values())
    assert all(port.calls == ['opus'] for port in ports.values())
    assert intake.run(['C', 'D'], root, instruction='Update these Claims', max_workers=2, store=store) == result
    assert len(starts) == 2


@pytest.mark.parametrize('ids', [[], [''], [' C'], ['claims/C'], ['C\n'], 'C', ['C', None]])
def test_invalid_list_never_prepares_action(claims, ids):
    store, root = claims
    with pytest.raises(ValueError, match='claim_ids'):
        intake.prepare(ids, root, instruction='Update', store=store)
    assert not (root / 'manifest.json').exists()


def test_complete_list_validated_before_any_dispatch(claims):
    store, root = claims
    prepared = intake.prepare(['C', 'MISSING'], root, instruction='Update', store=store)
    assert prepared['excluded'] == {'MISSING': {'status': 'paused', 'reason': 'missing_claim'}}
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    assert [r.target_keys for r, _ in jobs.values()] == [['claims/C']]


@pytest.mark.parametrize('change', ['ids', 'instruction', 'workers', 'source', 'request', 'policy'])
def test_recovery_cannot_change_authority_or_bound_inputs(claims, change):
    store, root = claims
    prepared = intake.prepare(['C'], root, instruction='Update', store=store)
    kwargs = {'instruction': 'Update', 'store': store}
    ids = ['C']
    if change == 'ids': ids = ['C', 'D']
    if change == 'instruction': kwargs['instruction'] = 'different direction'
    if change == 'workers': kwargs['max_workers'] = 2
    if change == 'policy': kwargs['policy'] = cm.DEFAULT_POLICY
    if change == 'source':
        Path(store.get_record_state('source_documents', 'S')['payload']['source_path']).write_text('{"script":[{"text":"different"}]}')
    if change == 'request':
        item = json.loads(Path(prepared['manifest']['path']).read_text())['jobs'][0]
        Path(item['request']['path']).write_text('{}')
    with pytest.raises(ValueError):
        intake.prepare(ids, root, **kwargs)


def test_permissions_do_not_turn_related_claims_into_hidden_targets(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'related-list-case', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'C', 'to_id': 'D', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Update', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    request = next(iter(jobs.values()))[0]
    assert request.target_keys == ['claims/C']
    assert [(p.collection, p.source_id) for p in request.creation_permissions] == [
        ('claims', 'S'), ('evidence_steps', 'S'), ('source_fragments', 'S')]
    assert not any(p.key == 'claims/D' for p in request.permissions)
    assert all(not set(p.fields) & (cm.source.PROVENANCE | {'visibility', 'claim_id', 'source_id'})
               for p in request.permissions)


def test_review_revision_and_commit_run_through_the_list_entry(claims, monkeypatch):
    store, root = claims
    ports = []
    citation = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def resolution(fid):
        return {'finding_id': fid, 'disposition': 'resolved', 'reason': 'synthetic source correction',
                'citations': [citation], 'affected_target_keys': ['claims/C'],
                'affected_write_keys': ['claims/C'], 'impact_reason': 'target wording corrected'}
    def review(packet):
        assert packet['original_goal']['review_questions'] == ['Cleanup this Claim']
        result = pass_review(packet)
        if not packet['unresolved']:
            result['findings'] = [{'target_key': 'claims/C', 'component': 'statement', 'category': 'content',
                'reason': 'synthetic missing qualification', 'citations': [citation],
                'acceptance_condition': 'preserve the stated qualification',
                'affected_target_keys': ['claims/C'], 'affected_write_keys': [], 'impact_reason': 'target wording'}]
        else:
            result['prior_findings'] = [resolution(fid) for fid in packet['unresolved']]
        return result
    def propose(packet):
        assert packet['original_goal']['review_questions'] == ['Cleanup this Claim']
        return {'changes': [{'record_key': 'claims/C', 'fields_json': json.dumps({'statement': '甲是假设，不是断言。'}),
                             'reason': 'synthetic complete qualification', 'citations': [citation]}],
                'creations': [], 'retirements': [],
                'findings': [], 'finding_resolutions': [resolution(fid) for fid in packet['unresolved']]}
    def execute(manifest, output):
        for request, execution in batch.load(manifest, output, store=store)[1].values():
            port = SyntheticPorts(store, execution, proposal=propose, opus=review)
            ports.append(port)
            result = execute_claim_management(store, request, root=execution, ports=port)
            assert result['status'] == 'completed', result
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C'], root, instruction='Cleanup this Claim', store=store)
    assert result['all_actions_completed']
    assert ports[0].calls == ['opus', 'gpt', 'opus']
    assert store.get_record_state('claims', 'C')['payload']['statement'] == '甲是假设，不是断言。'


def test_unknown_reply_keeps_its_reservation_and_other_claim_finishes(claims, monkeypatch):
    store, root = claims
    ports = {}
    def unknown(packet):
        raise RuntimeError('synthetic unknown transport result')
    def execute(manifest, output):
        for key, (request, execution) in batch.load(manifest, output, store=store)[1].items():
            port = ports.setdefault(key, SyntheticPorts(store, execution,
                opus=unknown if request.target_keys == ['claims/C'] else pass_review))
            execute_claim_management(store, request, root=execution, ports=port)
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C', 'D'], root, instruction='Update', store=store)
    assert result['status'] == 'paused' and result['claims']['D']['qualification']['ready']
    assert not (result['claims']['C']['qualification'] or {}).get('ready')
    action_id = result['claims']['C']['action_id']
    saved = store.get_cvp_group('CMA-' + cm.sha256_json(action_id))['call_reservations']
    again = intake.run(['C', 'D'], root, instruction='Update', store=store)
    assert again['claims']['C']['result']['reason'] == 'call_uncertain'
    assert store.get_cvp_group('CMA-' + cm.sha256_json(action_id))['call_reservations'] == saved
    assert all(port.calls == ['opus'] for port in ports.values())


def test_all_unavailable_ids_return_explicit_results_without_batch_dispatch(claims, monkeypatch):
    store, root = claims
    payload = store.get_record_state('claims', 'C')['payload']
    payload['review_status'] = 'human_review_required'
    # Seed a protected status only in this disposable test database.
    from backend.api.canonical_repository.postgres_store import record_content_sha
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET payload=%s::jsonb,content_sha256=%s WHERE collection='claims' AND object_id='C'",
                     (json.dumps(payload), record_content_sha(payload)))
    def unexpected(*args, **kwargs):
        raise AssertionError('unavailable Claim must not dispatch')
    monkeypatch.setattr(batch, 'run', unexpected)
    result = intake.run(['C', 'MISSING'], root, instruction='Update', store=store)
    assert not result['all_actions_completed'] and result['status'] == 'paused'
    assert {key: row['result']['reason'] for key, row in result['claims'].items()} == {
        'C': 'human_hold', 'MISSING': 'missing_claim'}


def test_status_detects_inherited_inner_worker_lock(claims):
    import fcntl
    store, root = claims
    intake.prepare(['C'], root, instruction='Update', store=store)
    with (root / 'batch/controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert batch.status(root)['live_lock_holder']
    assert not batch.status(root)['live_lock_holder']


def test_policy_is_saved_with_task_and_can_resume_from_another_checkout(claims, tmp_path):
    store, root = claims
    prepared = intake.prepare(['C'], root, instruction='Update', store=store)
    alternate = tmp_path / 'same-policy-another-checkout.json'
    alternate.write_bytes(intake.DEFAULT_POLICY.read_bytes())
    assert intake.prepare(['C'], root, instruction='Update', store=store, policy=alternate) == prepared
    for request, _ in batch.load(prepared['manifest']['path'], root / 'batch', store=store)[1].values():
        assert Path(request.policy.path) == root / 'policy.json'


def test_failed_current_source_check_replaces_stale_completed_report(claims):
    store, root = claims
    intake.prepare(['C'], root, instruction='Update', store=store)
    batch.progress(root / 'result.json', {'status': 'completed', 'all_actions_completed': True})
    Path(store.get_record_state('source_documents', 'S')['payload']['source_path']).write_text('{"script":[]}')
    with pytest.raises(ValueError):
        intake.run(['C'], root, instruction='Update', store=store)
    assert batch.status(root)['result']['all_actions_completed'] is False


def test_detached_cli_freezes_input_ids_and_uses_the_same_entry(claims, monkeypatch, tmp_path):
    store, root = claims
    ids_path = tmp_path / 'provided.json'
    ids_path.write_text('["C", "D", "C"]')
    original = intake.prepare
    monkeypatch.setattr(intake, 'prepare', lambda values, output, **kwargs:
                        original(values, output, store=store, **kwargs))
    commands = []
    monkeypatch.setattr(batch, 'detach', lambda command, output: commands.append((command, output)))
    monkeypatch.setattr('sys.argv', ['batch', '--claim-ids', str(ids_path), '--root', str(root),
                                   '--instruction', 'Update', '--max-workers', '2', '--detach'])
    batch.main()
    ids_path.write_text('["MISSING"]')
    command, output = commands[0]
    assert output == root
    assert json.loads(Path(command[command.index('--claim-ids') + 1]).read_text()) == ['C', 'D']


def test_existing_intake_keeps_its_frozen_policy_when_new_default_changes(claims):
    store, root = claims
    legacy = cm.DEFAULT_POLICY.parent / 'wang_claim_management_joint_update_v6.json'
    saved = intake.prepare(['C'], root, instruction='Update', store=store, policy=legacy)
    assert intake.prepare(['C'], root, instruction='Update', store=store) == saved
    request = next(iter(batch.load(saved['manifest']['path'], root / 'batch', store=store)[1].values()))[0]
    assert request.allowed_operations == ['Update'] and not request.creation_permissions
    assert json.loads(Path(request.policy.path).read_text())['model_output_contract'] == 'claim_changes_v2'


@pytest.mark.parametrize('legacy_follow_up',[False,True])
@pytest.mark.parametrize('occupied_support',[False,True])
def test_new_judgment_can_create_its_own_fragment_and_evidence_without_borrowing(claims,legacy_follow_up,occupied_support):
    from copy import deepcopy
    from backend.api.canonical_repository.claim_identity_allocation import reserve,allocated_ids
    from backend.pipeline.cvp_sql_commit import binding
    store,root=claims;doc=deepcopy(store.get_record_state('source_documents','S')['payload'])
    path=Path(doc['source_path']);path.write_text(json.dumps({'script':[{'text':'甲是假设，不是断言。'},{'text':'乙是结论。'}]},ensure_ascii=False))
    doc['source_file_sha256']=binding(path)['file_sha256']
    store.apply_plan(store.plan_package({'package_id':'complete-professor-source','source_documents':[doc]}))
    occupied={}
    if occupied_support:
        store.apply_plan(store.plan_package({'package_id':'unrelated-support',
            'source_fragments':[{'fragment_id':'NEW-F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'甲是假设，不是断言。'}],
            'evidence_steps':[{'evidence_step_id':'NEW-E','statement':'甲是假设，不是断言。','source_fragment_ids':['NEW-F'],'produced_claim_ids':[]}]}))
        occupied={key:store.get_record_state(*key) for key in [('source_fragments','NEW-F'),('evidence_steps','NEW-E')]}
    prepared=intake.prepare(['C'],root,instruction='Full Update including missing independent source judgments',store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    citation={'source_id':'S','paragraph_key':'S0002','exact_quote':'乙是结论。'}
    def decisions(packet):
        return [{'finding_id':fid,'disposition':'resolved','reason':'the new judgment has its own source evidence',
            'citations':[citation],'affected_target_keys':['claims/C'],'affected_write_keys':[],
            'impact_reason':'complete source judgment now recorded'} for fid in packet['unresolved']]
    def review(packet):
        value=pass_review(packet);value['prior_findings']=decisions(packet)
        if not packet['unresolved']:
            value['findings']=[{'target_key':'claims/C','component':'independent_judgement_without_claim','category':'mapping',
                'reason':'the second paragraph has no Claim or bound Evidence','acceptance_condition':'create its source fragment, Evidence and Claim',
                'citations':[citation],'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'source teaching omitted'}]
        return value
    def proposal(packet):
        cid=allocated_ids(packet['original_goal']['claim_creation_allocations'])[0]
        rows=[('source_fragments','NEW-F',{'source_id':'S','paragraph_key':'S0002','verbatim_excerpt':citation['exact_quote'],'anchor_state':'current'}),
              ('evidence_steps','NEW-E',{'statement':citation['exact_quote'],'source_fragment_ids':['NEW-F'],'produced_claim_ids':[cid],
                  'speaker':'professor','stance':'asserted','support_eligibility':'eligible_candidate'}),
              ('claims',cid,{'statement':citation['exact_quote'],'claim_type':'exegesis','attribution':'professor','evidence_step_ids':['NEW-E'],
                  'occurrences':[{'source_id':'S','anchors':[]}]})]
        return {'changes':[],'creations':[{'collection':col,'object_id':oid,'fields_json':json.dumps(payload),
            'reason':'record professor source judgment with its own evidence','citations':[citation]} for col,oid,payload in rows],
            'retirements':[],'findings':[],'finding_resolutions':decisions(packet)}
    if legacy_follow_up:
        policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text());policy.budget.gpt_calls=0
        policy_path=root/'legacy-policy.json';policy_path.write_text(policy.model_dump_json())
        request.policy=cm.source.FileBinding.model_validate(binding(policy_path))
        request.creation_permissions=[g for g in request.creation_permissions if g.collection=='claims']
        auth=json.loads(Path(request.authorization.path).read_text());auth.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=request.policy.model_dump(mode='json'))
        auth_path=root/'legacy-authorization.json';auth_path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
        old_ports=SyntheticPorts(store,execution,opus=review,proposal=proposal)
        assert execute_claim_management(store,request,root=execution,ports=old_ports)['status']=='paused'
        assert old_ports.calls==['opus']
        old=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
        store.pause_claim_management_action(old['run_id'],reason='permission_required',detail='Claim Create omitted the necessary same-source support Create grants')
        old=store.get_cvp_group(old['run_id']);previous={k:v for k,v in old.items() if k not in {'run_id','status','error'}}
        request=request.model_copy(deep=True);request.predecessor_action_id=request.action_id;request.action_id+='-support-follow-up'
        request.creation_permissions += [cm.CreationPermission(collection=col,source_id='S',fields=sorted(fields)) for col,fields in intake.SUPPORT_CREATION_FIELDS.items()]
        policy.initial_candidate='proposal';policy.budget=policy.budget.model_copy(update={'gpt_calls':1,'opus_calls':1,'max_revisions':1})
        policy_path=root/'follow-policy.json';policy_path.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(policy_path))
        execution=root/'follow-execution'
        records=store.read_claim_context([('claims','C'),('source_documents','S')]);before={cm.source.key(*k):cm.model_state(v) for k,v in records.items()}
        auth.update(request=request.model_dump(mode='json',exclude={'authorization','policy'}),policy=request.policy.model_dump(mode='json'),
            claim_creation_allocations={'S':reserve(store,action_id=request.action_id,source_id='S',target_id='C',root=execution)},
            owner_call_extension={'basis':'permission_required','predecessor_action_id':request.predecessor_action_id,'prior_metadata_sha256':cm.sha256_json(previous),
                'authorization_quote':'engineering repair grants one bounded correction and review','budget':policy.budget.model_dump(mode='json'),
                'current_objects_sha256':cm.sha256_json(before),'related_action_metadata_sha256':{}})
        auth_path=root/'follow-authorization.json';auth_path.write_text(json.dumps(auth));request.authorization=cm.source.FileBinding.model_validate(binding(auth_path))
    ports=SyntheticPorts(store,execution,opus=review,proposal=proposal)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==(['gpt','opus'] if legacy_follow_up else ['opus','gpt','opus'])
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))
    fid=next(k.split('/',1)[1] for k in parent['created_keys'] if k.startswith('source_fragments/'))
    eid=next(k.split('/',1)[1] for k in parent['created_keys'] if k.startswith('evidence_steps/'))
    assert fid.startswith('CMF-') and eid.startswith('CME-')
    assert store.get_record_state('source_fragments',fid)['payload']['paragraph_key']=='S0002'
    assert store.get_record_state('evidence_steps',eid)['payload']['source_fragment_ids']==[fid]
    created=[k for k in parent['created_keys'] if k.startswith('claims/')]
    assert len(created)==1 and store.claim_management_qualification(created[0].split('/',1)[1])['ready']
    assert store.claim_management_qualification('C')['ready']
    for key,state in occupied.items():assert store.get_record_state(*key)==state
