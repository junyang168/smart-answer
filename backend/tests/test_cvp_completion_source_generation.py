"""Completion epoch authority, not semantic/provider approval fixtures."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, ChangeSetConflict, record_content_sha,
)


@pytest.fixture
def epoch(tmp_path):
    source=tmp_path/'source.json';source.write_text('new source fixture')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    proof=tmp_path/'proof.json';proof.write_text('synthetic transaction proof, not provider approval')
    before={'source_id':'S','source_path':str(source),'source_file_sha256':'old'}
    after={**before,'source_file_sha256':digest(source)}
    binding={'path':str(proof),'file_sha256':digest(proof)}
    meta={'transaction_contract':'wang_cvp_sql_transaction_v2','run_id':'R',
          'reviewed_sha256':'review','authorization':binding,
          'approval':{'terminal_review':binding,'final_review_program_binding':binding}}
    row=[before,after,2,3,record_content_sha(before),record_content_sha(after),
         'applied','wang_source_sql_reconciliation_v2',meta]
    current={('source_documents','S'):{'payload':after,'revision':3,
             'content_sha256':record_content_sha(after),'retired':False}}
    return [{'path':str(source),'file_sha256':'old'}],current,row


class Cursor:
    def __init__(self,rows):self.rows=rows
    def execute(self,sql,args):
        assert sql.lstrip().startswith('SELECT') and args==('S',3)
        return self
    def fetchall(self):return self.rows


def test_exact_immediate_approved_epoch(epoch):
    sources,current,row=epoch
    PostgresKnowledgeStore._assert_completion_source_generations(Cursor([row]),sources,current)


@pytest.mark.parametrize('fault',[
    'missing','ambiguous','audit_missing','retired','physical','content','before',
    'revision','status','kind','contract','review','authorization','terminal','program','after',
])
def test_epoch_does_not_inherit_unproved_approval(epoch,fault):
    sources,current,row=deepcopy(epoch);rows=[row]
    if fault=='missing':rows=[]
    elif fault=='ambiguous':rows=[row,row]
    elif fault=='audit_missing':current={}
    elif fault=='retired':current[('source_documents','S')]['retired']=True
    elif fault=='physical':current[('source_documents','S')]['payload']['source_file_sha256']='foreign'
    elif fault=='content':current[('source_documents','S')]['content_sha256']='foreign'
    elif fault=='before':row[0]['source_file_sha256']='another epoch'
    elif fault=='revision':row[2]=1
    elif fault=='status':row[6]='prepared'
    elif fault=='kind':row[7]='knowledge_package'
    elif fault=='contract':row[8]['transaction_contract']='foreign'
    elif fault=='review':row[8]['reviewed_sha256']=''
    elif fault=='authorization':row[8]['authorization']={**row[8]['authorization'],'file_sha256':'foreign'}
    elif fault=='terminal':row[8]['approval']['terminal_review']={**row[8]['approval']['terminal_review'],'file_sha256':'foreign'}
    elif fault=='program':row[8]['approval'].pop('final_review_program_binding')
    elif fault=='after':row[1]={**row[1],'title':'not current'}
    with pytest.raises(ChangeSetConflict):
        PostgresKnowledgeStore._assert_completion_source_generations(Cursor(rows),sources,current)


@pytest.mark.skipif(os.environ.get('WKP357_ACTUAL_SOURCE_EPOCH')!='1',reason='explicit current saved SQL readonly acceptance')
def test_actual_g135_current_epoch_and_original_binding_readonly():
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    run='CVPG-7695d0725db039e052920efa75442e8f0e56ebdf2c85026f43d7585b4fecfc71'
    audit_path=root/'execution'/run/'audit-d46f321c251bcb1b1fba/audit.json'
    original=audit_path.read_bytes();audit=json.loads(original)
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    proofs=verify_saved_full_audit(audit_path)
    assert proofs
    store=PostgresKnowledgeStore();group=store.get_cvp_group(run)
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        current={}
        for pin in audit['meta']['registry_read_scope']['read_pins']:
            if pin['collection']=='source_documents':
                key=(pin['collection'],pin['object_id']);state=store.get_record_state(*key)
                assert [state['revision'],state['content_sha256'],state['retired']]==pin['pin']
                current[key]=state
        store._assert_completion_source_generations(cursor,group['reviewed']['evidence']['sources'],current)
    assert audit_path.read_bytes()==original and store.get_cvp_group(run)==group
