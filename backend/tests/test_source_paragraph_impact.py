from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_paragraph_impact as p
from backend.tests.test_source_reconciliation import scenario
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


@pytest.fixture
def scoped(scenario):
    manifest, states, _ = scenario
    # Extend the synthetic source with an unrelated unchanged paragraph and
    # independently referenced Evidence/Claim. No professor text in this test.
    for b, text in [(manifest.before_source, '甲是假设。'),
                    (manifest.current_sources[0], '甲是假设，不是断言。')]:
        from pathlib import Path
        Path(b.path).write_text(json.dumps([{'text': text}, {'text': '乙不变。'}], ensure_ascii=False))
        b.file_sha256 = hashlib.sha256(Path(b.path).read_bytes()).hexdigest()
    before = c.source_snapshot(manifest.before_source)
    after = c.source_snapshot(manifest.current_sources[0])
    states['source_documents/SRC-test'].update(source_body_sha256=before['body_sha256'],
        source_sha256=before['body_sha256'], source_file_sha256=before['file']['file_sha256'])
    for k, para, quote in [('source_fragments/FR-test', 'S0001', '甲是假设。'),
                           ('source_fragments/FR-other', 'S0002', '乙不变。')]:
        value = deepcopy(states['source_fragments/FR-test'])
        value.update(fragment_id=c.split_key(k)[1], paragraph_key=para, verbatim_excerpt=quote,
            source_sha256=before['body_sha256'], anchor_state='source_version_bound',
            paragraph_text_sha256=hashlib.sha256(before['paragraphs'][para]['text'].encode()).hexdigest(),
            verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest())
        states[k] = value
    evidence = deepcopy(states['evidence_steps/E-test'])
    evidence.update(evidence_step_id='E-other', source_fragment_ids=['FR-other'], produced_claim_ids=['CL-other'])
    states['evidence_steps/E-other'] = evidence
    claim = deepcopy(states['claims/CL-test'])
    claim.update(claim_id='CL-other', evidence_step_ids=['E-other'])
    states['claims/CL-other'] = claim
    manifest.permissions.append(c.Permission(collection='source_fragments', object_id='FR-other', fields=['source_sha256']))
    return manifest, states, before, after


def test_disjoint_semantic_graph_and_exact_binding_epoch(scoped):
    manifest, states, before, after = scoped
    original = deepcopy(states)
    plan = p.partition(manifest, states, before, after)
    semantic = plan['proof']['semantic_inventory']
    assert 'claims/CL-test' in semantic['affected_keys']
    assert 'claims/CL-other' not in semantic['affected_keys'] + semantic['context_keys']
    assert 'claims/CL-other' in plan['proof']['complete_affected_keys']
    bound = c.index(plan['binding_postimages'])['source_fragments/FR-other']
    assert bound == {**states['source_fragments/FR-other'], 'source_sha256': after['body_sha256'], 'revision': 2}
    assert plan['proof']['content_approval'] is False
    assert plan['proof']['group_completion'] is False
    assert p.verify(plan, manifest, states, before, after) == plan
    assert states == original


@pytest.mark.parametrize('mutation', ['quote', 'paragraph', 'epoch', 'hash', 'state', 'permission', 'timing', 'insert', 'unbounded'])
def test_binding_scope_fails_closed(scoped, mutation):
    m, states, before, after = deepcopy(scoped)
    fragment = states['source_fragments/FR-other']
    if mutation == 'quote': fragment['verbatim_excerpt'] = 'foreign'
    if mutation == 'paragraph': fragment['paragraph_key'] = 'S0099'
    if mutation == 'epoch': fragment['source_sha256'] = 'foreign'
    if mutation == 'hash': fragment['paragraph_text_sha256'] = '0' * 64
    if mutation == 'state': fragment['review_status'] = 'human_hold'
    if mutation == 'permission': m.permissions[-1].fields.append('verbatim_excerpt')
    if mutation == 'timing': after['paragraphs']['S0001']['index'] = 17
    if mutation == 'insert': after['paragraphs']['S0003'] = {'text': '新增'}
    if mutation == 'unbounded': after['paragraphs']['S0002']['text'] = '乙也变化。'
    with pytest.raises(ValueError): p.partition(m, states, before, after)


@pytest.mark.parametrize('mutation', ['payload', 'key', 'approval', 'root', 'source', 'drop'])
def test_proof_is_full_recomputed_not_an_approval_flag(scoped, mutation):
    m, states, before, after = scoped
    plan = deepcopy(p.partition(m, states, before, after))
    if mutation == 'payload': plan['binding_postimages'][0]['payload']['review_status'] = 'system_approved'
    if mutation == 'key': plan['proof']['binding_only_rows'][0]['record_key'] = 'source_fragments/foreign'
    if mutation == 'approval': plan['proof']['content_approval'] = True
    if mutation == 'root': plan['proof']['semantic_roots'] = []
    if mutation == 'source': plan['proof']['after_body_sha256'] = 'foreign'
    if mutation == 'drop': plan['proof']['complete_affected_keys'].pop()
    with pytest.raises(ValueError, match='proof drift'): p.verify(plan, m, states, before, after)


def test_unrelated_retained_payload_drift_does_not_inherit_a_binding_proof(scoped):
    m, states, before, after = scoped
    plan = p.partition(m, states, before, after)
    states['claims/CL-other']['statement'] = 'Changed without approval'
    with pytest.raises(ValueError, match='proof drift'):
        p.verify(plan, m, states, before, after)


def test_preview_retains_full_own_source_and_no_execution_authority(scoped):
    m, states, before, after = scoped
    plan = p.partition(m, states, before, after)
    frozen = {'manifest': m.model_dump(), 'before_records': c.rows(states), 'before': before,
              'after': [after], 'sentence_inventory': [], 'context_sources': []}
    preview = p.semantic_preview(frozen, plan)
    assert preview['before_source'] == before and preview['current_sources'] == [after]
    assert preview['source_epoch_binding_proof']['binding_only_rows'] == plan['proof']['binding_only_rows']
    assert preview['scope_preview_only'] and preview['content_write_authority'] is False
    assert 'campaign_claim_ids' not in preview['manifest_projection']
    assert m.model_dump() == frozen['manifest']


def binding_material(scoped):
    m, states, before, after = scoped
    frozen = {'manifest': m.model_dump(), 'before_records': c.rows(states), 'before': before,
              'after': [after], 'sentence_inventory': [], 'context_sources': []}
    partition = p.partition(m, states, before, after)
    ops = [{'collection': row['collection'], 'object_id': row['object_id'], 'operation': 'update',
            'before_revision': states[c.key(row['collection'], row['object_id'])]['revision'],
            'after_revision': row['payload']['revision'], 'payload': deepcopy(row['payload'])}
           for row in partition['binding_postimages']]
    return frozen, partition, ops


@pytest.fixture
def executable(scoped):
    from pathlib import Path
    m, states, before, after = deepcopy(scoped)
    m.source_paragraph_profile = p.PROFILE
    doc = next(v for v in m.permissions if v.collection == 'source_documents')
    doc.fields = ['source_sha256', 'source_body_sha256', 'source_file_sha256', 'source_text_sha256',
                  'anchor_binding_sha256', 'editorial_structure_sha256', 'editorial_topology_sha256']
    authorization = json.loads(Path(m.authorization.path).read_text())
    authorization['permissions'] = [v.model_dump() for v in m.permissions]
    Path(m.authorization.path).write_text(json.dumps(authorization, ensure_ascii=False))
    m.authorization.file_sha256 = hashlib.sha256(Path(m.authorization.path).read_bytes()).hexdigest()
    frozen = c.freeze(m, states)
    p.attach(frozen)
    inv = frozen['source_paragraph_execution']['partition']['proof']['semantic_inventory']
    data = {}
    statement = after['paragraphs']['S0001']['text']
    for k in inv['affected_keys']:
        if c.split_key(k)[0] not in {'claims', 'source_fragments', 'evidence_steps', 'observations', 'questions'}:
            continue
        new = deepcopy(states[k]); new['revision'] += 1; new['review_status'] = 'candidate'
        if k.startswith('source_fragments/'):
            new.update(verbatim_excerpt=statement, source_sha256=after['body_sha256'],
                verbatim_excerpt_sha256=hashlib.sha256(statement.encode()).hexdigest(),
                paragraph_text_sha256=hashlib.sha256(statement.encode()).hexdigest())
        elif k.startswith('questions/'):
            new.update(text='甲只是一个假设吗？', answer_state='answered')
        else: new['statement'] = statement
        data[k] = new
    semantic = c.Proposal(records=[c.Row.model_validate(r) for r in c.rows(data)],
        dispositions=[c.Disposition(record_key=k, action='update' if k in data else 'retain', reason='Synthetic full own-source test')
                      for k in inv['affected_keys']],
        sentence_dispositions=[c.SentenceDisposition(sentence_id=s['sentence_id'], action='represented',
            record_keys=['claims/CL-test'], reason='Synthetic sentence coverage') for s in frozen['sentence_inventory']])
    return m, states, frozen, semantic


def test_execution_proposal_review_compile_plan_whole_path(executable, monkeypatch):
    from backend.pipeline import source_reconciliation_runner as runner
    from backend.api.canonical_repository.postgres_store import build_change_set_plan, record_content_sha
    m, states, frozen, semantic = executable
    packet = runner.build_proposal_packet(frozen)
    assert packet['source_paragraph_profile'] == p.PROFILE
    proposal = runner.effective_proposal(frozen, semantic.model_dump())
    assert runner.model_proposal_dump(frozen, proposal) == semantic.model_dump()
    reviewed = c.review_packet(frozen, proposal)
    assert reviewed['proposal'] == semantic.model_dump()
    required = [d['record_key'] for d in reviewed['proposal']['dispositions']] + [reviewed['source_epoch_binding_scope']['record_key']]
    raw = {'stage':'initial_review', 'artifact_sha256':'e'*64, 'completed_at':'2026-10-12T03:00:00Z',
           'response': {'proposal_sha256': reviewed['proposal_sha256'], 'verdicts':[
               {'record_key':k,'approved':True,'reason':'Synthetic transaction test, not provider approval','problems':[]}
               for k in required]}}
    package = c.compile_package(frozen, proposal, raw)
    # Pure compiler test only. The separate actual commonSQL test below supplies
    # saved request/raw/application authority; this fixture is not provider proof.
    monkeypatch.setattr('backend.api.canonical_repository.postgres_store.validate_store_package_authorization', lambda value: None)
    existing = {c.split_key(k):{'payload':v,'revision':v['revision'], 'content_sha256':record_content_sha(v), 'retired':False}
                for k,v in states.items()}
    plan = build_change_set_plan(package, existing, source_kind=c.VERSION).as_dict()
    post = c.assert_plan_postimages(package, plan, states)
    assert post['claims/CL-test']['statement'] == frozen['after'][0]['paragraphs']['S0001']['text']
    assert post['claims/CL-other'] == states['claims/CL-other']
    assert post['source_fragments/FR-other']['review_status'] == states['source_fragments/FR-other']['review_status']
    assert post['source_documents/SRC-test']['review_status'] == states['source_documents/SRC-test']['review_status']


def test_paragraph_lossless_json_postimages_compile_without_raw_rewrite(executable):
    _,_,frozen,semantic=executable
    wire={'records':[{'collection':r.collection,'object_id':r.object_id,
                     'payload_json':json.dumps(r.payload,ensure_ascii=False)} for r in semantic.records],
          'dispositions':[d.model_dump() for d in semantic.dispositions],
          'sentence_dispositions':[d.model_dump() for d in semantic.sentence_dispositions],
          'unadmitted_candidates_json':[]}
    original=deepcopy(wire)
    assert p.compose_proposal(frozen,wire)==p.compose_proposal(frozen,semantic.model_dump())
    assert wire==original


@pytest.mark.parametrize('late_failure', [False, True])
def test_common_sql_paragraph_full_saved_stages_commit_rollback_cache(executable, postgres_store, tmp_path, monkeypatch, late_failure):
    from backend.tests.test_cvp_sql_actions_postgres import source_run
    from backend.tests.test_source_reconciliation import FakePorts
    from backend.pipeline.cvp_sql_actions import execute_source_action
    m, states, frozen, semantic = executable
    store = postgres_store
    run = source_run(store, (m, states, frozen['after'][0]))
    ports = FakePorts(m, states, frozen['after'][0], tmp_path)
    from backend.api.canonical_repository.postgres_store import record_content_sha
    from backend.pipeline.cvp_sql_flow import binding
    report_path=tmp_path/'original-source-finding.json';report_path.write_text('{}')
    original_issue={'issue_id':'CI-isolated-source','owner_run_id':run,
        'action':{'action_id':'CVPA-old-diagnostic','run_id':run},
        'reports':[{'claim_id':'CL-test','claim_revision':states['claims/CL-test']['revision'],
                    'claim_sha256':record_content_sha(states['claims/CL-test']), 'report':binding(report_path)}],
        'correction_used':True,'terminal_refusal':False}
    actual_attach=p.attach
    def with_original_issue(material,*args,**kwargs):
        actual_attach(material,*args,**kwargs)
        material['source_paragraph_execution']['prior_source_issues']=[{
            'issue':deepcopy(original_issue),'original_answers':[],
            'old_action_committed':False,'original_verdicts_changed':False}]
    monkeypatch.setattr(p,'attach',with_original_issue)
    actual_adapter = ports.adapter
    def adapter(stage):
        result = actual_adapter(stage)
        def generate(packet):
            ports.calls.append(stage)
            if 'review' not in stage: return semantic.model_dump()
            keys = [d['record_key'] for d in packet['proposal']['dispositions']]
            keys.append(packet['source_epoch_binding_scope']['record_key'])
            keys.extend(i['record_key'] for i in packet.get('prior_source_issue_resolution_scope',[]))
            approved = stage != 'initial_review'  # Exercise the sole correction + actual final reconstruction.
            return {'verdicts':[{'record_key':k, 'approved':approved, 'reason':'Synthetic full lifecycle fixture, not provider approval',
                'problems':[] if approved else ['Synthetic initial disagreement']} for k in keys]}
        result.generate = generate
        return result
    ports.adapter = adapter
    actual_scoped = p.scoped_adapter
    def synthetic_scoped(adapter, packet):
        result = actual_scoped(adapter, packet)
        # Preserve this fixture's explicit transport while retaining the REAL
        # new prompt/schema/encoding identity and raw validation path.
        if hasattr(adapter, 'generate') and 'generate' in adapter.__dict__:
            result.generate = adapter.generate
        return result
    monkeypatch.setattr(p, 'scoped_adapter', synthetic_scoped)
    check = store._assert_transaction_edges_and_reviews
    before = {k:store.get_record_state(*c.split_key(k)) for k in states}
    if late_failure:
        def fail(*args): raise ValueError('late complete paragraph transaction rollback')
        monkeypatch.setattr(store, '_assert_transaction_edges_and_reviews', fail)
        with pytest.raises(ValueError, match='late complete paragraph'):
            execute_source_action(store, run, m.model_dump(), root=tmp_path/'action', ports=ports)
        assert all(store.get_record_state(*c.split_key(k)) == v for k,v in before.items())
        assert store.get_cvp_group(run)['checkpoint'] == 'reviewed'
        monkeypatch.setattr(store, '_assert_transaction_edges_and_reviews', check)
    result = execute_source_action(store, run, m.model_dump(), root=tmp_path/'action', ports=ports)
    assert result['status'] == 'applied'
    authenticated = p.committed_context(store, store.get_cvp_group(run))
    assert authenticated['source_only_approval'] is True
    assert authenticated['group_complete'] is False
    assert ports.calls == ['proposal','initial_review','correction','final_review']
    assert store.get_record_state('claims','CL-other') == before['claims/CL-other']
    fragment = store.get_record_state('source_fragments','FR-other')
    assert fragment['payload']['review_status'] == states['source_fragments/FR-other']['review_status']
    assert fragment['payload']['source_sha256'] == frozen['after'][0]['body_sha256']
    assert execute_source_action(store,run,m.model_dump(),root=tmp_path/'action',ports=ports)['status'] == 'already_applied'
    assert ports.calls == ['proposal','initial_review','correction','final_review']
    # Transaction-domain audit fixture ONLY, never provider approval. Exercise
    # the actual source receipt/SQL marker and common coverage reconstruction.
    from backend.pipeline import claim_first_management as first
    from backend.pipeline.cvp_sql_flow import pins, pin_rows, write_immutable
    from backend.pipeline.source_reconciliation_runner import body, read
    from backend.pipeline import viewpoint_content_blocked_fallback as fallback
    saved=store.get_cvp_group(run)
    action_root=Path(saved['reviewed']['evidence']['package']['payload']['source_repair_application']['run_root'])
    material=body(read(action_root/'scoped-materials.json'))
    current={(r['collection'],r['object_id']):store.get_record_state(r['collection'],r['object_id']) for r in material['sql_scope']['read_pins']}
    audit_ids=sorted(set(saved['claim_ids']) | {op['object_id'] for op in saved['reviewed']['plan']['operations'] if op['collection']=='claims'})
    extra=sorted(set(audit_ids)-set(saved['claim_ids']))
    intent={'schema_version':first.VERSION,'claim_ids':audit_ids,'viewpoint_ids':[],
            'read_pins':pin_rows(pins(current)), 'change_set_ids':saved['change_set_ids'],
            'model':'gemini-3.8-flash','workers':2,'layers':['1','2','3'],'automatic_retry':False,
            'original_group_claim_ids':saved['claim_ids'],'dependency_claim_ids':extra,
            'campaign_membership_changes':0,'source_paragraph_profile':p.PROFILE}
    audit={'meta':{'model':'gemini-3.8-flash','registry_read_scope':{'kind':'sql_transaction_group',
                  'complete_target_members':True,'claim_ids':audit_ids,'viewpoint_ids':[]}},
           'layers':{'1':{'counts':{'pass':len(audit_ids)},'findings':[]},'2':{},
                     '3':{'complete':True,'judged':len(audit_ids),
                          'results':[{'claim_id':cid,'verdict':'supported'} for cid in audit_ids]}}}
    write_immutable(action_root/'claim-first-audit/audit-intent.json',intent)
    write_immutable(action_root/'claim-first-audit/audit.json',audit)
    monkeypatch.setattr(fallback,'apply',lambda path,*a:path)
    monkeypatch.setattr(fallback,'verify_saved_full_audit',lambda *a,**kw:[])
    receipt=first.finish_claim_audit(store,run,action_root)
    assert receipt['claim_ids']==saved['claim_ids'] and receipt['group_complete'] is False
    handoff=first.coverage_context(store,store.get_cvp_group(run),{'claim_ids':saved['claim_ids']})
    assert handoff['source_slice_mode']==p.PROFILE and handoff['existing_cvp_content_approved'] is False
    # The new-review portion below is a scoped SQL domain fixture, NOT a
    # provider CVP identity approval. Production still requires the normal
    # reviewed coverage compiler before reaching the same completion boundary.
    from backend.pipeline.cvp_sql_flow import commit_package, binding, decoded_pins
    from backend.pipeline import cvp_source_impact_resolution as impact
    package={'package_id':'isolated-coverage-accounting',
             'source_documents':[{'source_id':'SRC-isolated-accounting','source_type':'sermon_transcript','title':'SQL fixture only'}]}
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json
    coverage_action=store.prepare_cvp_action(run,action_id='CVPA-'+sha256_json({'fixture':run,'kind':'coverage'}),
        kind='required_cvp_coverage',authorization={'fixture':True},materials={'claim_management_resolution':handoff})
    commit_package(store,run,package=package,approval={'fixture':'isolated transaction domain, no provider approval'},
        authorization={'fixture':True},backup=ports.backup(tmp_path/'coverage-backup'),
        sources=saved['reviewed']['evidence']['sources'],read_pins=pins(current),
        reason='isolated source lifecycle test',actor='pytest',source_kind='isolated_source_lifecycle_fixture',
        action=coverage_action,
        pending_cvp_claim_ids=[],pending_cvp_management_record_keys=[])
    value=store.get_cvp_group(run);all_current={}
    with store.connect() as conn,conn.cursor() as cursor:
        assert p.assert_issue_resolution(cursor,run,original_issue) is True
        for field in ('reports','action','correction_used','terminal_refusal'):
            foreign=deepcopy(original_issue)
            if field=='reports':foreign[field][0]['claim_sha256']='0'*64
            elif field=='action':foreign[field]['action_id']='CVPA-foreign'
            else:foreign[field]=not foreign[field]
            with pytest.raises(ValueError,match='reservation/report drift'):
                p.assert_issue_resolution(cursor,run,foreign)
    with store.connect() as conn:
        rows=conn.execute('SELECT collection,object_id FROM wang_knowledge.objects').fetchall()
    for key in rows:all_current[key]=store.get_record_state(*key)
    full=deepcopy(audit);full['layers']['4']={'complete':True,'judged':0,'results':[]}
    full['meta']['registry_read_scope']['read_pins']=pin_rows(pins(all_current))
    full_path=tmp_path/'full-transaction-domain-audit.json';write_immutable(full_path,full)
    proof=impact.build(store,run,full_path,tmp_path/'source-impact-resolution.json')
    kwargs={'audit':full,'audit_binding':binding(full_path),'read_pins':pins(all_current),
            'retained_impacts_resolution_path':proof}
    original=store.get_cvp_group(run);histories=list(original['change_set_ids'])
    actual_pin_check=store._assert_transaction_read_pins
    def late_completion(*a,**kw):raise RuntimeError('late paragraph completion rollback')
    monkeypatch.setattr(store,'_assert_transaction_read_pins',late_completion)
    with pytest.raises(RuntimeError,match='late paragraph completion'):
        store.complete_cvp_group(run,**kwargs)
    assert store.get_cvp_group(run)==original
    monkeypatch.setattr(store,'_assert_transaction_read_pins',actual_pin_check)
    store.complete_cvp_group(run,**kwargs)
    completed=store.get_cvp_group(run)
    assert completed['checkpoint']=='audited' and completed['change_set_ids']==histories
    store.complete_cvp_group(run,**kwargs)
    assert store.get_cvp_group(run)==completed
    assert ports.calls==['proposal','initial_review','correction','final_review']


def test_common_plan_binding_check_preserves_all_original_review_fields(scoped):
    frozen, partition, ops = binding_material(scoped)
    states = c.index(frozen['before_records'])
    package = {'source_fragments': [r['payload'] for r in partition['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': partition}}
    post = c.assert_plan_postimages(package, {'operations': ops}, states)
    assert post['source_fragments/FR-other'] == partition['binding_postimages'][0]['payload']
    assert post['claims/CL-other'] == states['claims/CL-other']
    assert package['source_fragments'][0]['review_status'] == states['source_fragments/FR-other']['review_status']


@pytest.mark.parametrize('status', ['candidate', 'human_review_required', 'approved', 'system_approved'])
def test_binding_only_does_not_upgrade_legacy_or_protected_review_state(scoped, status):
    m, states, before, after = deepcopy(scoped)
    states['source_fragments/FR-other']['review_status'] = status
    frozen, partition, ops = binding_material((m, states, before, after))
    package = {'source_fragments': [r['payload'] for r in partition['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': partition}}
    post = c.assert_plan_postimages(package, {'operations': ops}, states)
    assert post['source_fragments/FR-other']['review_status'] == status
    assert post['claims/CL-test'] == states['claims/CL-test']


@pytest.mark.parametrize('mutation', ['approval', 'quote', 'source', 'version', 'before_version',
                                      'retire', 'duplicate', 'missing', 'current', 'proof'])
def test_common_plan_binding_counterexamples(scoped, mutation):
    frozen, partition, ops = deepcopy(binding_material(scoped))
    states = c.index(frozen['before_records'])
    package = {'source_fragments': [r['payload'] for r in partition['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': partition}}
    if mutation == 'approval': ops[0]['payload']['review_status'] = 'independent_terminal_reviewed'
    if mutation == 'quote': ops[0]['payload']['verbatim_excerpt'] = 'unreviewed'
    if mutation == 'source': ops[0]['payload']['source_sha256'] = '0' * 64
    if mutation == 'version': ops[0]['after_revision'] += 1
    if mutation == 'before_version': ops[0]['before_revision'] += 1
    if mutation == 'retire': ops[0]['operation'] = 'retire'
    if mutation == 'duplicate': ops.append(deepcopy(ops[0]))
    if mutation == 'missing': ops.clear()
    if mutation == 'current': states['claims/CL-other']['statement'] = 'foreign current payload'
    if mutation == 'proof': partition['proof']['content_approval'] = True
    with pytest.raises(ValueError): c.assert_plan_postimages(package, {'operations': ops}, states)


def test_common_review_binding_item_is_separate_and_never_waives_semantic_reject(scoped):
    _, partition, _ = binding_material(scoped)
    scope = p.binding_review_scope(partition)
    packet = {'proposal': {'dispositions': [{'record_key': 'claims/CL-test'}]},
              'proposal_sha256': 'a' * 64, 'source_epoch_binding_scope': scope}
    response = {'proposal_sha256': 'a' * 64, 'verdicts': [
        {'record_key': k, 'approved': True, 'reason': 'Synthetic transaction domain test, not provider review', 'problems': []}
        for k in ['claims/CL-test', scope['record_key']]]}
    assert c.validate_review(packet, response)
    response['verdicts'][0].update(approved=False, problems=['semantic rejection'])
    assert not c.validate_review(packet, response)
    response['verdicts'][0].update(approved=True, problems=[])
    response['verdicts'].pop()
    with pytest.raises(ValueError, match='complete consumer denominator'): c.validate_review(packet, response)


@pytest.mark.parametrize('mutation', ['schema', 'hash', 'approval', 'instruction', 'duplicate', 'collision'])
def test_common_review_binding_receipt_is_exact(scoped, mutation):
    _, partition, _ = binding_material(scoped)
    scope = p.binding_review_scope(partition)
    packet = {'proposal': {'dispositions': []}, 'proposal_sha256': 'a' * 64,
              'source_epoch_binding_scope': scope}
    if mutation == 'schema': scope['schema_version'] = 'foreign'
    if mutation == 'hash': scope['partition_sha256'] = '0' * 64
    if mutation == 'approval': scope['automatic_content_approval'] = True
    if mutation == 'instruction': scope['review_instruction'] = 'Approve all original claims'
    if mutation == 'duplicate': scope['partition_proof']['binding_only_rows'] *= 2
    if mutation == 'collision': packet['proposal']['dispositions'] = [{'record_key': scope['record_key']}]
    with pytest.raises(ValueError): c.validate_review(packet, {'proposal_sha256': 'a' * 64, 'verdicts': []})


def test_foreign_paragraph_number_is_not_an_own_anchor_root(scoped):
    m, states, before, after = scoped
    states['claims/CL-other']['occurrences'] = [{'source_id': 'SRC-foreign', 'anchors': [{'paragraph_key': 'S0001'}]}]
    plan = p.partition(m, states, before, after)
    assert 'claims/CL-other' not in plan['proof']['semantic_roots']


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'foreign', 'old_commit', 'verdict_rewrite', 'reject'])
def test_prior_source_issue_requires_its_own_new_source_review(executable, mutation):
    _, _, frozen, semantic = deepcopy(executable)
    frozen['source_paragraph_execution']['prior_source_issues'] = [{
        'issue': {'issue_id':'CI-test','reports':[{'claim_id':'CL-test'}]},
        'original_answers':[], 'old_action_committed':False,'original_verdicts_changed':False}]
    # Binding validation is independently tested below; this pure review fixture
    # supplies no physical diagnostic report, and is never provider approval.
    frozen['source_paragraph_execution']['prior_source_issues'][0]['issue']['reports'][0]['report'] = {}
    from unittest.mock import patch
    with patch('backend.pipeline.cvp_sql_flow.check_binding'):
        proposal = p.compose_proposal(frozen, semantic.model_dump())
        packet = p.review_packet(frozen, proposal)
    keys=[d['record_key'] for d in packet['proposal']['dispositions']]
    keys += [packet['source_epoch_binding_scope']['record_key'],'source_issue_resolution/CI-test']
    response={'proposal_sha256':packet['proposal_sha256'],'verdicts':[
        {'record_key':k,'approved':True,'reason':'Synthetic review denominator only','problems':[]} for k in keys]}
    assert c.validate_review(packet,response)
    scope=packet['prior_source_issue_resolution_scope']
    if mutation=='missing':packet.pop('prior_source_issue_resolution_scope')
    if mutation=='duplicate':scope.append(deepcopy(scope[0]))
    if mutation=='foreign':scope[0]['record_key']='source_issue_resolution/CI-foreign'
    if mutation=='old_commit':scope[0]['old_action_committed']=True
    if mutation=='verdict_rewrite':scope[0]['original_verdicts_changed']=True
    if mutation=='reject':
        response['verdicts'][-1].update(approved=False,problems=['Still unresolved'])
        assert c.validate_review(packet,response) is False
    else:
        with pytest.raises(ValueError):c.validate_review(packet,response)


@pytest.mark.parametrize('target', ['report', 'raw', 'old_commit', 'verdict_rewrite'])
def test_prior_diagnostic_physical_leaves_cannot_drift(executable, tmp_path, target):
    _, _, frozen, _ = deepcopy(executable)
    from backend.pipeline.cvp_sql_flow import binding
    report=tmp_path/'report.json';report.write_text('{}')
    raw=tmp_path/'raw.json';raw.write_text('{}')
    prior={'issue':{'reports':[{'report':binding(report)}]},
           'original_answers':[{'raw':binding(raw)}],
           'old_action_committed':False,'original_verdicts_changed':False}
    frozen['source_paragraph_execution']['prior_source_issues']=[prior]
    p.execution_context(frozen)
    if target=='report':report.write_text('{"altered":true}')
    if target=='raw':raw.write_text('{"altered":true}')
    if target=='old_commit':prior['old_action_committed']=True
    if target=='verdict_rewrite':prior['original_verdicts_changed']=True
    with pytest.raises(ValueError):p.execution_context(frozen)


def test_all_cvp_members_remain_full_context_even_if_their_paragraph_is_unchanged(scoped):
    m, states, before, after = scoped
    states['viewpoint_claim_links/L-test'] = {'viewpoint_claim_link_id': 'L-test',
        'claim_id': 'CL-test', 'viewpoint_id': 'CV-test'}
    states['viewpoint_claim_links/L-other'] = {'viewpoint_claim_link_id': 'L-other',
        'claim_id': 'CL-other', 'viewpoint_id': 'CV-test'}
    states['canonical_viewpoints/CV-test'] = {'viewpoint_id': 'CV-test', 'current_revision_id': 'CVR-test'}
    states['viewpoint_revisions/CVR-test'] = {'viewpoint_revision_id': 'CVR-test', 'viewpoint_id': 'CV-test'}
    plan = p.partition(m, states, before, after)
    inv = plan['proof']['semantic_inventory']
    assert 'viewpoint_claim_links/L-other' in inv['affected_keys']
    for k in ['claims/CL-other', 'evidence_steps/E-other', 'source_fragments/FR-other']:
        assert k in inv['context_keys']
    assert len(plan['binding_postimages']) == 1  # no duplicated epoch operation


@pytest.mark.skipif(os.environ.get('WANG_SOURCE_PARAGRAPH_SAVED_ACCEPTANCE') != '1',
                    reason='explicit current-source readonly acceptance only')
def test_actual_saved_source_scope_no_calls_writes_or_input_upgrade():
    from pathlib import Path
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline.source_reconciliation_runner import build_proposal_packet
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json, canonical_json
    load_dotenv('.env')
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    path = root / 'g143-source-paragraph-66-manifest.json'
    raw_manifest = path.read_bytes()
    m = c.Manifest.model_validate(json.loads(raw_manifest))
    store = ReadOnly()
    run = 'CVPG-54aa85ab845ff2d1539132d522bf23971678e6de928bc8353d88533caa310f8d'
    old_group = sha256_json(store.get_cvp_group(run))
    records = store.read_affected_context([('source_documents', m.source_id)])
    states = {c.key(*k): v['payload'] for k, v in records.items() if not v.get('retired')}
    frozen = c.freeze(m, states)
    old_frozen = deepcopy(frozen)
    original_packet = build_proposal_packet(frozen)
    original_files = {b.path: c.bound_bytes(b) for b in [m.before_source, *m.current_sources, m.authorization, m.accepted_membership]}
    plan = p.partition(m, states, frozen['before'], frozen['after'][0])
    preview = p.semantic_preview(frozen, plan)
    ops = [{'collection': row['collection'], 'object_id': row['object_id'], 'operation': 'update',
            'before_revision': states[c.key(row['collection'], row['object_id'])]['revision'],
            'after_revision': row['payload']['revision'], 'payload': deepcopy(row['payload'])}
           for row in plan['binding_postimages']]
    package = {'source_fragments': [row['payload'] for row in plan['binding_postimages']],
               'source_epoch_binding_material': {'frozen': frozen, 'partition': plan}}
    composed = c.assert_plan_postimages(package, {'operations': ops}, states)
    for row in plan['binding_postimages']:
        assert composed[c.key(row['collection'], row['object_id'])] == row['payload']
    untouched = set(states) - set(c.index(plan['binding_postimages']))
    assert all(composed[k] == states[k] for k in untouched)
    review_scope = p.binding_review_scope(plan)
    assert p.validate_review_scope(review_scope) == 'source_epoch_binding/' + m.source_id
    preview = p.review_material(frozen, plan)
    assert preview['source_epoch_binding_scope']['partition_proof'] == plan['proof']
    assert 'source_epoch_binding_proof' not in preview
    assert len(preview['records']) < len(original_packet['records'])
    assert len(canonical_json(preview).encode()) < 600000
    assert len(plan['binding_postimages']) == 370
    assert len(preview['records']) == 130
    assert len(preview['context_sources']) == 1
    assert len([k for k in preview['inventory']['affected_keys'] if k.startswith('canonical_viewpoints/')]) == 1
    assert preview['campaign_member_count'] == 973 and preview['campaign_group_count'] == 156
    for row in plan['binding_postimages']:
        old = states[c.key(row['collection'], row['object_id'])]
        assert {k: v for k, v in row['payload'].items() if k not in {'source_sha256', 'revision'}} == {
            k: v for k, v in old.items() if k not in {'source_sha256', 'revision'}}
    assert frozen == old_frozen and build_proposal_packet(frozen) == original_packet
    profiled=deepcopy(frozen)
    profiled['manifest']['source_paragraph_profile']=p.PROFILE
    p.attach(profiled, store, run_id=run)
    execution_packet=build_proposal_packet(profiled)
    assert execution_packet['source_paragraph_profile']==p.PROFILE
    assert len(canonical_json(execution_packet).encode())<700000
    assert len(execution_packet['prior_source_issues'])==1
    assert len(execution_packet['prior_source_issues'][0]['original_answers'])==3
    assert execution_packet['prior_source_issues'][0]['old_action_committed'] is False
    assert len(execution_packet['protected_anchor_reviews'])==1
    assert len(execution_packet['internal_candidate_context'])==15
    assert 'knowledge_relations/DK-f1e2d4fa6bd5-P05-ER030' in execution_packet['internal_candidate_context']
    assert path.read_bytes() == raw_manifest
    for path, raw in original_files.items(): assert Path(path).read_bytes() == raw
    assert sha256_json(store.get_cvp_group(run)) == old_group
    for field in ['binding_postimages', 'proof']:
        tampered = deepcopy(plan)
        if field == 'binding_postimages': tampered[field][0]['payload']['anchor_state'] = 'unverified'
        else: tampered[field]['semantic_inventory']['affected_keys'].pop()
        with pytest.raises(ValueError, match='proof drift'):
            p.verify(tampered, m, states, frozen['before'], frozen['after'][0])
