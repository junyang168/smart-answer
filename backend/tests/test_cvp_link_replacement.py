from copy import deepcopy
import json
import os
from pathlib import Path
import uuid

import pytest

from backend.pipeline import cvp_link_replacement as flow
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.source_reconciliation_runner import Runner, Stop, write, stage_adapter
from backend.api.canonical_repository.postgres_store import canonical_json, record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store

ROOT = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
AUTH = ROOT / 'owner-g095-link-withdrawal-and-existing-coverage.json'


def synthetic():
    old = {'viewpoint_claim_link_id': 'L1', 'revision': 3, 'claim_id': 'C1',
        'pinned_claim_revision': 3, 'viewpoint_id': 'OLD', 'link_type': 'supports',
        'effective_state': 'active', 'review_status': 'system_approved',
        'validated_against_viewpoint_revision_id': 'R1', 'supporting_relation_ids': []}
    replacement = {**old, 'viewpoint_claim_link_id': 'L2', 'claim_id': 'C2', 'link_type': 'qualifies',
        'component_locator': {'statement_component': 'bounded', 'canonical_spans': []}, 'evidence_bindings': ['own']}
    values = {'claims': [{'claim_id': c, 'revision': 3, 'review_status': 'ai_consensus_reviewed'} for c in ['C1', 'C2', 'C3']],
        'canonical_viewpoints': [{'viewpoint_id': vid, 'current_revision_id': rid, 'review_status': 'system_approved'}
            for vid, rid in [('OLD', 'R1'), ('NEW', 'R2')]],
        'viewpoint_revisions': [{'viewpoint_id': vid, 'viewpoint_revision_id': rid, 'review_status': 'system_approved'}
            for vid, rid in [('OLD', 'R1'), ('NEW', 'R2')]],
        'viewpoint_claim_links': [old, replacement, {**old, 'viewpoint_claim_link_id': 'KEPT', 'claim_id': 'C3', 'link_type': 'equivalent_component'},
            {**old, 'viewpoint_claim_link_id': 'CORRECT', 'viewpoint_id': 'NEW', 'link_type': 'equivalent_component',
                'validated_against_viewpoint_revision_id': 'R2'}]}
    fields = {'claims': 'claim_id', 'canonical_viewpoints': 'viewpoint_id', 'viewpoint_claim_links': 'viewpoint_claim_link_id',
        'viewpoint_revisions': 'viewpoint_revision_id'}
    m = {'run_id': 'run', 'campaign_sha256': 'campaign', 'withdrawal_links': [old, replacement],
        'replacement_link_id': 'L2', 'candidate_viewpoint_ids': ['NEW'], 'original_reports': ['original'],
        'records': [{'collection': col, 'object_id': p[fields[col]], 'payload': p} for col, items in values.items() for p in items],
        'targets': [{'viewpoint': {'viewpoint_id': 'NEW', 'current_revision_id': 'R2'}, 'links': []}]}
    d = {'schema_version': flow.DIRECTION, 'run_id': 'run', 'campaign_sha256': 'campaign',
        'withdrawal_links': [{'link_id': p['viewpoint_claim_link_id'], 'sha256': flow.sha256_json(p)} for p in m['withdrawal_links']],
        'replacement_link_id': 'L2', 'candidate_viewpoint_ids': ['NEW'], 'original_reports': ['original'],
        'authorized_by': 'owner', 'human_content_approval': False, 'automatic_future_repairs_authorized': False,
        'authorization_quote': 'TEST ONLY: bounded withdrawal and independently reviewed new coverage'}
    answer = {'selection': {'claim_id': 'C2', 'diagnosis': 'different_target', 'viewpoint_id': 'NEW',
        'link_type': 'qualifies', 'reason': 'TEST ONLY', 'old_target_reason': 'TEST ONLY'}}
    return m, d, answer


@pytest.mark.parametrize('typ', ['qualifies', 'supports', 'equivalent_full', 'equivalent_component'])
def test_replacement_new_id_preserves_old_payload_claim_evidence_and_target_text(typ):
    m, d, a = synthetic(); before = deepcopy(m)
    flow.validate_direction(d, m); a['selection']['link_type'] = typ
    p = flow.postimage(m, a)
    assert p['viewpoint_claim_link_id'] not in ['L1', 'L2'] and p['viewpoint_id'] == 'NEW'
    assert p['claim_id'] == 'C2' and p['pinned_claim_revision'] == 3
    assert p['evidence_bindings'] == ['own'] and m == before
    assert p['component_locator'] == (None if typ == 'equivalent_full' else m['withdrawal_links'][1]['component_locator'])


@pytest.mark.parametrize('field', ['run_id', 'campaign_sha256', 'withdrawal_links', 'replacement_link_id',
    'candidate_viewpoint_ids', 'original_reports', 'authorized_by', 'human_content_approval',
    'automatic_future_repairs_authorized', 'authorization_quote'])
def test_owner_scope_does_not_expand(field):
    m, d, _ = synthetic(); d[field] = 'wrong' if field != 'authorization_quote' else ''
    with pytest.raises(ValueError): flow.validate_direction(d, m)


@pytest.mark.parametrize('problem', ['equivalent', 'held', 'relation', 'stale', 'orphan', 'lost-coverage', 'claim-hold'])
def test_no_hold_promotion_or_orphan_claim_cvp(problem):
    m, d, _ = synthetic()
    if problem == 'equivalent': m['withdrawal_links'][0]['link_type'] = 'equivalent_component'
    if problem == 'held': m['withdrawal_links'][0]['effective_state'] = 'held'
    if problem == 'relation': m['withdrawal_links'][0]['supporting_relation_ids'] = ['relation']
    if problem == 'stale': m['withdrawal_links'][0]['pinned_claim_revision'] = 1
    if problem in {'orphan', 'lost-coverage'}:
        omit = 'KEPT' if problem == 'orphan' else 'CORRECT'
        m['records'] = [r for r in m['records'] if r['object_id'] != omit]
    if problem == 'claim-hold': m['records'][0]['payload']['review_status'] = 'human_review_required'
    d['withdrawal_links'] = [{'link_id': p['viewpoint_claim_link_id'], 'sha256': flow.sha256_json(p)} for p in m['withdrawal_links']]
    with pytest.raises(ValueError): flow.validate_direction(d, m)


@pytest.mark.parametrize('change', [{'claim_id': 'foreign'}, {'viewpoint_id': 'OLD'}, {'viewpoint_id': 'foreign'},
    {'link_type': None}, {'diagnosis': 'unresolved'}, {'diagnosis': 'no_existing_target'}])
def test_model_choice_cannot_become_unreviewed_write(change):
    m, _, a = synthetic(); a['selection'].update(change)
    with pytest.raises(ValueError): flow.postimage(m, a)


def test_duplicate_membership_and_independent_rejection_are_not_pass():
    m, _, a = synthetic(); m['targets'][0]['links'] = [{'claim_id': 'C2'}]
    with pytest.raises(ValueError): flow.postimage(m, a)
    raw = {'response': {'verdicts': [{'claim_id': c, 'approved': c == 'C1', 'problems': [] if c == 'C1' else ['wrong'], 'reason': 'TEST ONLY'} for c in ['C1', 'C2']]}}
    with pytest.raises(Stop): flow.approved(m, raw)


def test_fixed_models_and_no_unapproved_repeat_stage():
    for stage in ('proposal', 'correction'):
        assert (flow.adapter(stage).model_id, flow.adapter(stage).backend) == ('gpt-6.1-sol', 'codex_subscription')
    for stage in ('initial_review', 'final_review'):
        assert (flow.adapter(stage).model_id, flow.adapter(stage).backend) == ('claude-opus-5-5', 'claude_subscription')
    with pytest.raises(ValueError): flow.adapter('second_correction')


@pytest.mark.parametrize('second_refusal', [False, True])
def test_saved_unique_correction_preserves_original_refusal_without_calls(tmp_path, monkeypatch, second_refusal):
    m, _, a = synthetic()
    p = {'response': a, 'artifact_sha256': 'original-proposal'}
    rejected = {'response': {'verdicts': [{'claim_id': c, 'approved': False,
        'problems': ['ORIGINAL REJECTION'], 'reason': 'TEST ONLY'} for c in ['C1', 'C2']]}, 'artifact_sha256': 'original-review'}
    revised = {'response': deepcopy(a), 'artifact_sha256': 'only-correction'}
    final = {'response': {'verdicts': [{'claim_id': c, 'approved': not second_refusal,
        'problems': ['FINAL REJECTION'] if second_refusal else [], 'reason': 'TEST ONLY'} for c in ['C1', 'C2']]}}
    original = deepcopy(rejected); stages=[]
    def cached(root, stage, packet):
        stages.append(stage)
        if stage == 'correction': assert packet['initial_review'] == original['response']
        return {'proposal':p, 'initial_review':rejected, 'correction':revised, 'final_review':final}[stage]
    monkeypatch.setattr(flow, 'saved_raw', cached)
    if second_refusal:
        with pytest.raises(Stop): flow.effective_saved_review(tmp_path, m)
    else:
        assert flow.effective_saved_review(tmp_path, m) == (revised, final)
    assert stages == ['proposal', 'initial_review', 'correction', 'final_review']
    assert rejected == original


def test_initial_pass_cannot_obtain_correction_budget(tmp_path, monkeypatch):
    m, _, a = synthetic()
    p = {'response': a, 'artifact_sha256': 'p'}
    r = {'response': {'verdicts': [{'claim_id': c, 'approved': True, 'problems': [], 'reason': 'TEST ONLY'} for c in ['C1', 'C2']]}}
    stages=[]
    def cached(root, stage, packet):
        stages.append(stage);return p if stage == 'proposal' else r
    monkeypatch.setattr(flow, 'saved_raw', cached)
    (tmp_path/'correction.reservation.json').write_text('UNKNOWN TEST ONLY')
    with pytest.raises(ValueError, match='second review budget'): flow.effective_saved_review(tmp_path, m)
    assert stages == ['proposal', 'initial_review']


def test_source_codec_selection_has_no_reservation_and_never_recodes_unknown(tmp_path, monkeypatch):
    class A:
        _shared_record_payload = True
        def __init__(self, size): self.size = size
        def request_bytes(self, p): return self.size
        def shared_keys_copy(self): return A(self.size - 20)
        def shared_keys_v2_copy(self): return A(self.size - 30)
        def shared_values_copy(self): return A(self.size - 60)
        def shared_shapes_copy(self): return A(self.size - 70)
    monkeypatch.setattr('backend.pipeline.source_reconciliation_runner.request_limit', lambda *a, **k: (500, None))
    monkeypatch.setattr('backend.pipeline.viewpoint_resolution_runtime.response_adapter', lambda a, *args: a)
    path = tmp_path / 'proposal.raw.json'
    assert stage_adapter(A(640), {}, path).size == 460
    (tmp_path / 'proposal.call-intent.json').write_text('UNKNOWN TEST ONLY')
    assert stage_adapter(A(640), {}, path).size == 640


@pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_ACCEPTANCE') != '1', reason='explicit real read-only materials / isolated SQL acceptance')
@pytest.mark.parametrize('late_failure', [False, True])
@pytest.mark.parametrize('correction', [False, True])
def test_real_saved_graph_five_operation_joint_transaction_and_no_replay(postgres_store, tmp_path, monkeypatch, late_failure, correction):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env')
    live = PostgresKnowledgeStore()
    m = flow.prepare_material(live, json.loads(AUTH.read_text())['run_id'], binding(AUTH))
    store = postgres_store
    with store.connect() as conn:
        for r in m['records']:
            p = r['payload']
            conn.execute('INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (r['collection'], r['object_id'], p['revision'], p.get('review_status', 'candidate'), p.get('visibility', 'internal'), record_content_sha(p), canonical_json(p)))
            if r['collection'] == 'viewpoint_claim_links':
                conn.execute('INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (r['collection'], r['object_id'], p['viewpoint_id'], p['claim_id'], p['link_type'], p['revision'], canonical_json(p)))
    d = json.loads(AUTH.read_text()); cids = [p['claim_id'] for p in m['withdrawal_links']]
    campaign = store.initialize_cvp_campaign({'accepted_grouping_sha256': uuid.uuid4().hex * 2,
        'groups': [{'sequence': 1, 'group_key': 'isolated', 'claim_ids': cids}]})
    run = store.prepare_cvp_group(campaign_sha256=campaign, group_key='isolated', sequence=1, claim_ids=cids, materials={'test': True})
    d.update(run_id=run, campaign_sha256=campaign); auth=tmp_path/'direction.json'; write(auth,d)
    views=sorted({p['viewpoint_id'] for p in m['withdrawal_links']})
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||%s::jsonb WHERE run_id=%s',
            (canonical_json({'reviewed': {'evidence': {'viewpoint_ids': views}}}),run))
    # Input and actual SQL are real. Model answers here are explicitly simulated,
    # never production content approval or a live model call.
    class Ports:
        def adapter(self, stage):
            obj=flow.adapter(stage); obj._client.executable=None
            def answer(packet):
                if stage in {'proposal', 'correction'}:return {'selection': {'claim_id': cids[1], 'diagnosis': 'different_target',
                    'viewpoint_id': d['candidate_viewpoint_ids'][0], 'link_type': 'qualifies',
                    'reason': 'ISOLATED TEST ONLY', 'old_target_reason': 'ISOLATED TEST ONLY'}}
                rejected = correction and stage == 'initial_review'
                return {'verdicts': [{'claim_id': cid, 'approved': not rejected,
                    'problems': ['ISOLATED INITIAL REFUSAL'] if rejected else [], 'reason': 'ISOLATED TEST ONLY'} for cid in cids]}
            obj.generate=answer
            obj.max_request_bytes=1000000  # Isolated synthetic model only, no production budget change.
            return obj
        def backup(self, root):
            p=root/'test.dump';p.write_bytes(b'ISOLATED TEST ONLY')
            return {'dump':binding(p),'restore_catalog_nonempty':True,'catalog_sha256':'test','restore_list_exit_code':0}
    monkeypatch.setattr('backend.pipeline.viewpoint_backup_archive.catalog',lambda p:'test')
    monkeypatch.setattr(flow,'adapter',flow.adapter)  # Actual validator below uses the same configured model ceiling.
    original_adapter=flow.adapter
    def test_adapter(stage):
        obj=original_adapter(stage);obj.max_request_bytes=1000000;return obj
    monkeypatch.setattr(flow,'adapter',test_adapter)
    before={ (r['collection'],r['object_id']):store.get_record_state(r['collection'],r['object_id']) for r in m['records']}
    guard=store._assert_transaction_edges_and_reviews
    if late_failure:
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',lambda *a: (_ for _ in ()).throw(ValueError('injected late validation')))
        with pytest.raises(ValueError, match='injected late validation'):
            flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
        assert all(store.get_record_state(*k)==v for k,v in before.items())
        monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',guard)
    result=flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())
    history=store.get_change_history(result['change_set_id'])
    assert len(history['operations'])==5
    withdrawn={('viewpoint_claim_links',p['viewpoint_claim_link_id']) for p in m['withdrawal_links']}
    for k,v in before.items():
        now=store.get_record_state(*k)
        if k in withdrawn:assert now['retired'] and now['payload']==v['payload'] and now['content_sha256']==v['content_sha256']
        else:assert now==v
    assert flow.execute(store,run,authorization=binding(auth),root=tmp_path/'action',ports=Ports())['status']=='already_applied'
