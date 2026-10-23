"""An unchanged Claim classification is not a missing statement or a write."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pytest
from backend.pipeline import source_reconciliation as c, source_reconciliation_runner as r

@pytest.mark.skipif(os.getenv('WKP357_RETAINED_LINK_INITIAL_SAVED') != '1', reason='explicit current SQL saved input')
@pytest.mark.parametrize('fault', [None,'pin','hold','issue','claim_pending','cause','writable_relation','proposed','strict'])
def test_actual_retained_claim_initial(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-521c60c1fdc0b9fb1fe5e9704781ee845fbe9edc587efbcfd6e2d8acd4f65bad/claim-issues/e42884673494cf441996c92efab947c61dbd7090fa8e812dd6ec6765e5095000/action/CVPA-47abb6ef1c83c1205fae32fc647db99eba25aed41c574ddd20fe8fca68f21fd4')
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    frozen=r.read(root/'scoped-materials.json'); c.guard_files(frozen)
    load_dotenv('.env');store=PostgresKnowledgeStore()
    with store.connect() as conn,conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
            def __getattr__(self,n):return getattr(cursor,n)
        store._assert_transaction_read_pins(Cursor(),decoded_pins(frozen['sql_scope']['read_pins']))
    raw=r.validate_raw(root,'proposal',r.body(r.read(root/'proposal.packet.json')))
    proposal=r.effective_proposal(frozen,raw['response']);original=deepcopy(frozen)
    issue=frozen['claim_issue_context']['issues'][0];key=c.key('claims',issue['claim_id'])
    row=next(x for x in frozen['before_records'] if c.key(x['collection'],x['object_id'])==key)
    pin=next(x['pin'] for x in frozen['sql_scope']['read_pins'] if c.key(x['collection'],x['object_id'])==key)
    if fault=='pin':pin[0]+=1
    if fault=='hold':row['payload']['review_status']='human_hold'
    if fault=='issue':issue['claim_sha256']='0'*64
    if fault=='claim_pending':next(d for d in proposal.dispositions if d.record_key==key).action='unresolved'
    if fault=='cause':proposal.claim_assessments[0].cause='source_unresolved'
    if fault=='proposed':proposal.records.append(c.Row.model_validate(row))
    if fault=='writable_relation':
        rel=next(d.record_key for d in proposal.dispositions if d.record_key.startswith('claim_relations/'))
        col,oid=c.split_key(rel)
        frozen['manifest']['permissions'].append({'collection':col,'object_id':oid,'fields':['reason'],'create':False})
    if fault:
        with pytest.raises(ValueError):
            if fault=='strict':c.review_packet(frozen,proposal)
            else:r.initial_review_base(frozen,proposal)
    else:
        packet=r.initial_review_base(frozen,proposal)
        receipt=packet['unresolved_proposal_review']
        assert receipt['claim_draft_diagnostic']['schema_version']=='claim_retained_link_initial_diagnostic_v1'
        assert receipt['content_write_authority'] is False
        response={'proposal_sha256':packet['proposal_sha256'],'verdicts':[
            {'record_key':d.record_key,'approved':True,'problems':[],'reason':'diagnostic only'} for d in proposal.dispositions]}
        assert c.validate_review(packet,response) is False
        with pytest.raises(ValueError):c.compile_package(frozen,proposal,{'response':response})
        assert frozen==original
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
