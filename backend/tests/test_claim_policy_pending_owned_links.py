"""Source-first never manufactures or approves missing owned link postimages."""
from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import claim_source_write_slice as m, source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r


@pytest.mark.skipif(os.getenv('WKP357_PENDING_OWNED_LINKS_SAVED') != '1', reason='explicit current SQL saved material')
@pytest.mark.parametrize('fault', [None, 'omit', 'duplicate', 'pin', 'hold', 'stale', 'writable',
                                  'foreign', 'retained', 'no_direction', 'proposed_link', 'extra_claim'])
def test_actual_policy_source_pending_links(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-521c60c1fdc0b9fb1fe5e9704781ee845fbe9edc587efbcfd6e2d8acd4f65bad/claim-issues/e42884673494cf441996c92efab947c61dbd7090fa8e812dd6ec6765e5095000/action/CVPA-47abb6ef1c83c1205fae32fc647db99eba25aed41c574ddd20fe8fca68f21fd4')
    files={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}
    frozen=r.read(root/'scoped-materials.json');c.guard_files(frozen)
    load_dotenv('.env');store=PostgresKnowledgeStore()
    with store.connect() as conn,conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
            def __getattr__(self,n):return getattr(cursor,n)
        store._assert_transaction_read_pins(Cursor(),decoded_pins(frozen['sql_scope']['read_pins']))
    for stage in ('proposal','initial_review','correction'):
        r.validate_raw(root,stage,r.body(r.read(root/(stage+'.packet.json'))))
    original=r.effective_proposal(frozen,r.read(root/'correction.raw.json')['response'])
    immutable=deepcopy(original.model_dump())
    direction={'schema_version':m.VERSION,'mode':'policy_claim_source_revision','human_content_approval':False,
        'claim_policy':frozen['claim_issue_context']['policy'],'withdraw_relation_ids':[],
        'source_write_keys':sorted(c.index(original.records)),'defer_owned_links':True}
    if fault=='retained':next(d for d in original.dispositions if d.record_key.startswith('viewpoint_claim_links/') and d.action=='unresolved').action='retain'
    if fault=='no_direction':direction.pop('defer_owned_links')
    if fault=='proposed_link':original.records.append(deepcopy(next(row for row in frozen['before_records'] if row['collection']=='viewpoint_claim_links')))
    if fault in {'retained','no_direction','proposed_link'}:
        with pytest.raises((ValueError,AttributeError)):m.projection(frozen,original,direction)
    else:
        scoped,proposal=m.projection(frozen,original,direction)
        link=scoped['source_slice_pending_owned_links'][0]
        row=next(x['payload'] for x in scoped['before_records'] if c.key(x['collection'],x['object_id'])==link)
        if fault=='omit':scoped['source_slice_pending_owned_links'].pop()
        if fault=='duplicate':scoped['source_slice_pending_owned_links'].append(link)
        if fault=='pin':next(p['pin'] for p in scoped['sql_scope']['read_pins'] if c.key(p['collection'],p['object_id'])==link)[0]+=1
        if fault=='hold':row['review_status']='human_hold'
        if fault=='stale':row['pinned_claim_revision']+=1
        if fault=='writable':scoped['manifest']['permissions'].append({'collection':'viewpoint_claim_links','object_id':c.split_key(link)[1],'fields':['component_locator']})
        if fault=='foreign':scoped['source_slice_pending_owned_links'][0]='viewpoint_claim_links/foreign'
        if fault=='extra_claim':proposal.records.append(deepcopy(proposal.records[0]))
        if fault:
            with pytest.raises(ValueError):c.review_packet(scoped,proposal)
        else:
            packet=c.review_packet(scoped,proposal)
            assert len(packet['proposal']['records'])==1
            assert len(scoped['source_slice_pending_owned_links'])==2
            assert all(c.index(scoped['before_records'])[k]==c.index(frozen['before_records'])[k] for k in scoped['source_slice_pending_owned_links'])
            # A synthetic response tests the compiler domain, NOT actual approval.
            raw={'artifact_sha256':'a'*64, 'model':'transaction-domain-fixture','completed_at':'2026-10-11T18:15:00Z',
                 'response':{'proposal_sha256':packet['proposal_sha256'],'verdicts':[
                {'record_key':d.record_key,'approved':True,'problems':[],'reason':'transaction fixture only'} for d in proposal.dispositions]}}
            compiled=c.compile_package(scoped,proposal,raw)
            assert len(compiled['claims'])==1
            assert compiled.get('viewpoint_claim_links',[])==[]
            assert original.model_dump()==immutable
    assert files=={p:c.file_sha256(p) for p in root.iterdir() if p.is_file()}
