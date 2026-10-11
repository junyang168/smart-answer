from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_source_write_slice as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r

ROOT = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-4c6083d8d5036f316dd620945c37974f4d6b3daf128752776490dec2b2f21cd4/claim-issues/90279628408c1c38f142b72f7e53419bc216fbf8d0594f6989d686b3a2fae951/action/CVPA-aee8d874759e1887d164d7e9ec7836c3ec2f559fcfa9cbf4499bd71853f8468c')


@pytest.mark.parametrize('fault', [None, 'unapplied', 'foreign', 'empty', 'mode', 'root'])
def test_claim_audit_still_requires_exact_committed_policy_slice(monkeypatch, tmp_path, fault):
    calls = []
    monkeypatch.setattr(m, 'validate_package', lambda value: calls.append(value))
    d = {'mode':'policy_claim_source_revision', 'run_id':'RUN', 'parent_root':str(tmp_path)}
    package = {'source_repair_application':{'source_write_slice':d, 'run_root':str(tmp_path)}}
    saved = {'run_id':'RUN', 'checkpoint':'applied', 'reviewed':{'evidence':{
        'package':{'payload':package}, 'pending_cvp_claim_ids':['C']}}}
    if fault == 'unapplied': saved['checkpoint'] = 'reviewed'
    if fault == 'foreign': d['run_id'] = 'foreign'
    if fault == 'empty': saved['reviewed']['evidence']['pending_cvp_claim_ids'] = []
    if fault == 'mode': d['mode'] = 'unknown'
    if fault == 'root': d['parent_root'] = str(tmp_path/'foreign')
    if fault:
        with pytest.raises(ValueError): m.validate_claim_layer_authority(saved, tmp_path)
        assert calls == []
    else:
        assert m.validate_claim_layer_authority(saved, tmp_path)
        assert calls == [package]


@pytest.fixture
def saved():
    if os.getenv('WKP357_SAVED_POLICY_SOURCE_SLICE') != '1':
        pytest.skip('explicit saved SQL readonly opt-in')
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    load_dotenv('.env')
    files = {p:c.file_sha256(p) for p in ROOT.iterdir() if p.is_file()}
    f = r.read(ROOT/'scoped-materials.json'); c.guard_files(f)
    with PostgresKnowledgeStore().connect() as conn, conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self, q, args): return cursor.execute(q.removesuffix(' FOR UPDATE'), args)
            def __getattr__(self, name): return getattr(cursor, name)
        PostgresKnowledgeStore()._assert_transaction_read_pins(Cursor(), decoded_pins(f['sql_scope']['read_pins']))
    for stage in ('proposal', 'initial_review', 'correction'):
        r.validate_raw(ROOT, stage, r.body(r.read(ROOT/(stage+'.packet.json'))))
    p = r.effective_proposal(f, r.read(ROOT/'correction.raw.json')['response'])
    d = {'schema_version':m.VERSION, 'mode':'policy_claim_source_revision', 'human_content_approval':False,
         'claim_policy':f['claim_issue_context']['policy'], 'source_write_keys':sorted(c.index(p.records)),
         'withdraw_relation_ids':[]}
    yield f, p, d
    assert files == {p:c.file_sha256(p) for p in ROOT.iterdir() if p.is_file()}


@pytest.mark.parametrize('fault', [None, 'omit', 'duplicate', 'policy', 'cause', 'withdraw', 'metadata',
    'ownership', 'qualification', 'owner_pin', 'owner_hold', 'nonreciprocal', 'pending_write', 'pending_hold',
    'pending_foreign', 'selected_unresolved', 'missing_disposition'])
def test_actual_policy_source_slice(saved, fault):
    f,p,d = saved; original = deepcopy(p.model_dump())
    before = c.index(f['before_records'])
    claim = next(x for x in p.records if x.collection == 'claims')
    ev = next(x for x in p.records if x.collection == 'evidence_steps')
    pending = next(x for x in p.dispositions if x.action == 'unresolved')
    owner = before['evidence_steps/'+ev.object_id]['produced_claim_ids'][0]
    def old(k): return next(x['payload'] for x in f['before_records'] if c.key(x['collection'],x['object_id']) == k)
    if fault == 'omit': d['source_write_keys'].pop()
    if fault == 'duplicate': p.records.append(deepcopy(p.records[0]))
    if fault == 'policy': d['claim_policy'] = {}
    if fault == 'cause': p.claim_assessments[0].cause = 'source_unresolved'
    if fault == 'withdraw': d['withdraw_relation_ids'] = ['foreign']
    if fault == 'metadata': claim.payload['evidence_step_ids'] = ['foreign']
    if fault == 'ownership': ev.payload['produced_claim_ids'] = ['foreign']
    if fault == 'qualification': ev.payload['support_eligibility'] = 'context_only'
    if fault == 'owner_pin': next(x for x in f['sql_scope']['read_pins'] if x['object_id'] == owner)['pin'][0] += 1
    if fault == 'owner_hold': old('claims/'+owner)['review_status'] = 'human_hold'
    if fault == 'nonreciprocal': old('claims/'+owner)['evidence_step_ids'] = []
    if fault == 'pending_write': f['manifest']['permissions'].append({'collection':c.split_key(pending.record_key)[0], 'object_id':c.split_key(pending.record_key)[1], 'fields':['statement']})
    if fault == 'pending_hold': old(pending.record_key)['review_status'] = 'human_hold'
    if fault == 'pending_foreign': pending.record_key = 'claims/foreign'
    if fault == 'selected_unresolved': next(x for x in p.dispositions if x.record_key == 'claims/'+claim.object_id).action = 'unresolved'
    if fault == 'missing_disposition': p.dispositions.pop()
    if fault:
        with pytest.raises((ValueError, KeyError)): m.projection(f,p,d)
    else:
        scoped, q = m.projection(f,p,d)
        packet = c.review_packet(scoped,q)
        assert c.index(q.records) == c.index(p.records)
        assert len(packet['proposal']['dispositions']) == 2
        assert p.model_dump() == original
        assert all(x.record_key in scoped['inventory']['context_keys'] for x in p.dispositions if x.action == 'unresolved')
        assert not (ROOT/'final_review.reservation.json').exists()
