"""Exact missing Claim drafts reach only their original independent initial review."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from backend.pipeline import source_reconciliation as c, source_reconciliation_runner as r
from backend.pipeline.claim_issue_management import empty_statement_initial_draft


@pytest.mark.skipif(os.getenv('WKP357_EMPTY_STATEMENT_SAVED') != '1', reason='explicit current saved SQL opt-in')
@pytest.mark.parametrize('fault', [None, 'pin', 'retired', 'hold', 'issue_sha', 'issue_id',
    'assessment', 'permission', 'create', 'proposed', 'extra_issue', 'missing', 'other_writable', 'strict'])
def test_saved_empty_statement_initial_only(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-5baf4616c077c3c118f2f36dc6254f84351ecca3d2c98d7114bf16e13e7def8f/claim-issues/eaececc27b862e2a2f0b72790ba47eb3b546aa44b759ac0533a62dd8c0dcb2e6/action/CVPA-7125653d81ab8fc2b1957ce9cd1d97bd73ee370054298c7226833f6925709269')
    files = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    frozen = r.read(root/'scoped-materials.json')
    c.guard_files(frozen)
    load_dotenv('.env'); store = PostgresKnowledgeStore()
    with store.connect() as conn, conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
            def __getattr__(self,n):return getattr(cursor,n)
        store._assert_transaction_read_pins(Cursor(),decoded_pins(frozen['sql_scope']['read_pins']))
    raw = r.validate_raw(root,'proposal',r.body(r.read(root/'proposal.packet.json')))
    proposal = r.effective_proposal(frozen,raw['response'])
    assert not proposal.records
    original = deepcopy(frozen)
    issue = frozen['claim_issue_context']['issues'][0]
    cid=issue['claim_id']; key=c.key('claims',cid)
    row=next(x for x in frozen['before_records'] if c.key(x['collection'],x['object_id'])==key)
    pin=next(x['pin'] for x in frozen['sql_scope']['read_pins'] if c.key(x['collection'],x['object_id'])==key)
    perm=next(x for x in frozen['manifest']['permissions'] if c.key(x['collection'],x['object_id'])==key)
    if fault=='pin':pin[0]+=1
    if fault=='retired':pin[2]=True
    if fault=='hold':row['payload']['review_status']='human_hold'
    if fault=='issue_sha':issue['claim_sha256']='0'*64
    if fault=='issue_id':proposal.claim_assessments[0].issue_id='CI-foreign'
    if fault=='assessment':proposal.claim_assessments[0].cause='already_sufficient'
    if fault=='permission':perm['fields'].remove('statement')
    if fault=='create':perm['create']=True
    if fault=='proposed':proposal.records.append(c.Row.model_validate(row))
    if fault=='extra_issue':frozen['claim_issue_context']['issues'].append(deepcopy(issue))
    if fault=='missing':frozen['before_records'].remove(row)
    if fault=='other_writable':
        link=next(d.record_key for d in proposal.dispositions if d.action=='unresolved' and d.record_key.startswith('viewpoint_claim_links/'))
        # A proposed unresolved connection is not unchanged readonly context.
        proposal.records.append(c.Row.model_validate(next(x for x in frozen['before_records'] if c.key(x['collection'],x['object_id'])==link)))
    if fault:
        with pytest.raises((ValueError,KeyError)):
            if fault=='strict':c.review_packet(frozen,proposal)
            elif fault in {'proposed','other_writable'}:empty_statement_initial_draft(frozen,proposal)
            else:r.initial_review_base(frozen,proposal)
    else:
        packet=r.initial_review_base(frozen,proposal)
        receipt=packet['unresolved_proposal_review']
        assert receipt['claim_draft_diagnostic']['claim_id']==cid
        assert receipt['content_write_authority'] is False
        assert receipt['mandatory_original_unique_correction'] is True
        response={'proposal_sha256':packet['proposal_sha256'],'verdicts':[
            {'record_key':d.record_key,'approved':True,'problems':[],'reason':'initial only'} for d in proposal.dispositions]}
        assert c.validate_review(packet,response) is False
        with pytest.raises(ValueError):c.compile_package(frozen,proposal,{'response':response})
        assert frozen==original
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
