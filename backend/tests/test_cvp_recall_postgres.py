"""Real current-read queries in a socket-only disposable PostgreSQL."""
import pytest

from backend.api.canonical_repository.postgres_store import ChangeSetConflict, canonical_json, record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


def insert(store,collection,oid,payload):
    with store.connect() as c:
        c.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload)
            VALUES(%s,%s,1,%s,%s::jsonb)''',(collection,oid,record_content_sha(payload),canonical_json(payload)))


def seed(store):
    insert(store,'claims','C1',{'claim_id':'C1','statement':'待处理'})
    insert(store,'claims','C2',{'claim_id':'C2','statement':'只读成员','evidence_step_ids':['E2']})
    insert(store,'evidence_steps','E2',{'evidence_step_id':'E2','statement':'不应自动读取的成员证据'})
    insert(store,'canonical_viewpoints','V1',{'viewpoint_id':'V1','identity_status':'active','current_revision_id':'R1'})
    insert(store,'viewpoint_revisions','R1',{'viewpoint_id':'V1','viewpoint_revision_id':'R1',
        'core_proposition':'判断','scope':{},'proposition_signature':{}})
    for lid,cid in [('L1','C1'),('L2','C2')]:
        insert(store,'viewpoint_claim_links',lid,{'viewpoint_claim_link_id':lid,'viewpoint_id':'V1',
            'claim_id':cid,'effective_state':'active','link_type':'member'})
        with store.connect() as c:
            c.execute('''INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload)
                VALUES('viewpoint_claim_links',%s,'V1',%s,'member',1,'{}'::jsonb)''',(lid,cid))


def test_current_queries_are_scoped_and_never_expand_member_evidence(postgres_store):
    store=postgres_store;seed(store)
    assert len(store.read_cvp_heads())==1
    assert set(store.read_cvp_links(claim_ids=['C1']))=={('viewpoint_claim_links','L1')}
    assert set(store.read_cvp_links(viewpoint_ids=['V1']))=={
        ('viewpoint_claim_links','L1'),('viewpoint_claim_links','L2')}
    records=store.read_current_records([('claims','C1'),('claims','C2')])
    assert set(records)=={('claims','C1'),('claims','C2')}
    store.verify_current_read_pins({k:(v['revision'],v['content_sha256'],False) for k,v in records.items()})
    assert store.read_current_records([])=={} and store.read_cvp_links()=={}
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM wang_knowledge.change_sets').fetchone()==(0,)


def test_changed_or_corrupt_current_rows_are_rejected(postgres_store):
    store=postgres_store;seed(store)
    before=store.read_current_records([('claims','C1')])['claims','C1']
    with store.connect() as c:
        c.execute("UPDATE wang_knowledge.objects SET revision=revision+1 WHERE collection='claims' AND object_id='C1'")
    with pytest.raises(ChangeSetConflict):
        store.verify_current_read_pins({('claims','C1'):(before['revision'],before['content_sha256'],False)})
    with store.connect() as c:
        c.execute("UPDATE wang_knowledge.objects SET payload=payload || '{\"statement\":\"corrupt\"}'::jsonb WHERE collection='claims' AND object_id='C1'")
    with pytest.raises(ChangeSetConflict,match='SHA drift'):store.read_current_records([('claims','C1')])


def test_index_excludes_retired_identity_and_rejects_missing_head(postgres_store):
    store=postgres_store;seed(store)
    view={'viewpoint_id':'V2','identity_status':'retired','current_revision_id':'does-not-exist'}
    insert(store,'canonical_viewpoints','V2',view)
    assert len(store.read_cvp_heads())==1
    with store.connect() as c:
        c.execute("UPDATE wang_knowledge.objects SET retired_at=now() WHERE collection='viewpoint_revisions' AND object_id='R1'")
    with pytest.raises(ChangeSetConflict,match='missing'):store.read_cvp_heads()
