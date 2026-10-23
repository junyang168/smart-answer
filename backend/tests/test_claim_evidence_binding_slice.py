from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_source_write_slice as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r


@pytest.fixture
def saved():
    if os.getenv('WKP357_SAVED_BINDING_SLICE_TEST') != '1':
        pytest.skip('explicit saved SQL readonly opt-in')
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    load_dotenv('.env')
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-b9bdf23b7ec4f63b20fa5d69818109697a8fb808138b18557dffe2e225369031/claim-issues/7aa531195fd6871077f6dc53cbb730ca7528fb3ac4576e2cb5e58c828fe9237f/action/CVPA-65dad50d41936bd1f43acac20b43dd06ab6be10a9d047f19453267d1b0d9b480')
    files={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}
    f=r.read(root/'scoped-materials.json');c.guard_files(f)
    store=PostgresKnowledgeStore()
    with store.connect() as conn,conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
            def __getattr__(self,name):return getattr(cursor,name)
        store._assert_transaction_read_pins(Cursor(),decoded_pins(f['sql_scope']['read_pins']))
    for stage in ('proposal','initial_review','correction'):
        r.validate_raw(root,stage,r.body(r.read(root/(stage+'.packet.json'))))
    p=r.effective_proposal(f,r.read(root/'correction.raw.json')['response'])
    d={'schema_version':m.VERSION,'mode':'policy_evidence_binding','human_content_approval':False,
       'claim_policy':f['claim_issue_context']['policy'],'source_write_keys':sorted(c.index(p.records)),
       'withdraw_relation_ids':[]}
    yield f,p,d
    assert files=={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}


@pytest.mark.parametrize('fault',[None,'omit','extra','policy','withdraw','cause','words','ownership',
    'qualification','hold','pin','pending_write','pending_hold','pending_foreign','unresolved_claim','duplicate'])
def test_actual_binding_slice_preserves_every_pending_and_raw(saved,fault):
    f,p,d=saved;original=deepcopy(p.model_dump());before=c.index(f['before_records'])
    claim=next(row for row in p.records if row.collection=='claims')
    ev=next(row for row in p.records if row.collection=='evidence_steps')
    pending=next(x for x in p.dispositions if x.action=='unresolved')
    if fault=='omit':d['source_write_keys'].pop()
    if fault=='extra':d['source_write_keys'].append('questions/foreign')
    if fault=='policy':d['claim_policy']={}
    if fault=='withdraw':d['withdraw_relation_ids']=['foreign']
    if fault=='cause':p.claim_assessments[0].cause='source_unresolved'
    if fault=='words':claim.payload['statement']='foreign'
    if fault=='ownership':ev.payload['produced_claim_ids']=['foreign']
    if fault=='qualification':ev.payload['support_eligibility']='eligible'
    if fault=='hold':next(row for row in f['before_records'] if row['object_id']==claim.object_id)['payload']['review_status']='human_hold'
    if fault=='pin':next(row for row in f['sql_scope']['read_pins'] if row['object_id']==claim.object_id)['pin'][0]+=1
    if fault=='pending_write':f['manifest']['permissions'].append({'collection':'questions','object_id':c.split_key(pending.record_key)[1],'fields':['questioner']})
    if fault=='pending_hold':next(row for row in f['before_records'] if c.key(row['collection'],row['object_id'])==pending.record_key)['payload']['review_status']='human_hold'
    if fault=='pending_foreign':pending.record_key='questions/foreign'
    if fault=='unresolved_claim':next(x for x in p.dispositions if x.record_key=='claims/'+claim.object_id).action='unresolved'
    if fault=='duplicate':p.dispositions.append(deepcopy(p.dispositions[0]))
    if fault:
        with pytest.raises((ValueError,KeyError)):m.projection(f,p,d)
    else:
        scoped,q=m.projection(f,p,d)
        packet=c.review_packet(scoped,q)
        assert len(q.records)==2 and c.index(q.records)==c.index(p.records)
        assert p.model_dump()==original
        assert pending.record_key in scoped['inventory']['context_keys']
        assert all(row.payload==before[c.key(row.collection,row.object_id)] for row in q.records)==False
        assert not (Path(f['sql_scope'].get('root','/nonexistent'))/'final_review.raw.json').exists()
        assert len(packet['proposal']['dispositions'])==2
