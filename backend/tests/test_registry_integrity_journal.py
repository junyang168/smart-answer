from copy import deepcopy
import uuid

import pytest
from backend.api.canonical_repository import integrity_journal as j


def sig(revision=1,*,retired=False):
    return dict(revision=revision,content_sha256=str(revision%10)*64,
                retired=retired,row_sha256=str(revision%10)*64)


def generation(sequence):
    return dict(schema_version='wang_registry_integrity_generation_v1',
                journal_id=str(uuid.UUID(int=1)),sequence=sequence,storage=['db','engine','0',['changes','head','objects']],
                catalog_sha256='c'*64,installation_sha256='d'*64)


class Cursor:
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def execute(self,sql,params=None):self.calls.append((sql,params))
    def fetchall(self):return self.rows


def test_exact_declared_transaction_is_bounded_and_not_semantic_approval():
    rows=[(11,'100','INSERT',None,None,'claims','C',None,sig()),
          (12,'100','UPDATE','claims','D','claims','D',sig(),sig(2)),
          (13,'100','DELETE','claims','E',None,None,sig(),None)]
    before={('claims','C'):None,('claims','D'):sig(),('claims','E'):sig()}
    after={('claims','C'):sig(),('claims','D'):sig(2),('claims','E'):None}
    cursor=Cursor(rows)
    result=j.verify_transaction_delta(cursor,generation(10),generation(13),before,after)
    assert result==dict(status='exact_transaction_write_set_observed',rows=3,written_keys=3,semantic_approval_granted=False)
    assert len(cursor.calls)==1 and 'sequence>%s AND sequence<=%s' in cursor.calls[0][0]
    assert before[('claims','D')]==sig()


@pytest.mark.parametrize('kind',['extra','gap','tx','order','before','after','rename','op','missing','old-null','new-null','signature'])
def test_delta_rejects_non_target_incomplete_or_drifted_writes(kind):
    row=[11,'100','UPDATE','claims','C','claims','C',sig(),sig(2)]
    rows=[row];before={('claims','C'):sig()};after={('claims','C'):sig(2)}
    end=11
    if kind=='extra':row[3]=row[5]='evidence_steps'
    elif kind=='gap':rows=[]
    elif kind=='tx':rows.append([12,'101','UPDATE','claims','C','claims','C',sig(2),sig(3)]);after[('claims','C')]=sig(3);end=12
    elif kind=='order':row[0]=12
    elif kind=='before':row[-2]=sig(3)
    elif kind=='after':after[('claims','C')]=sig(3)
    elif kind=='rename':row[6]='other'
    elif kind=='op':row[2]='TRUNCATE'
    elif kind=='missing':before[('claims','D')]=sig();after[('claims','D')]=sig()
    elif kind=='old-null':row[-2]=None
    elif kind=='new-null':row[-1]=None
    else:row[-1]['retired']='false'
    with pytest.raises(ValueError):j.verify_transaction_delta(Cursor(rows),generation(10),generation(end),before,after)


@pytest.mark.parametrize('kind',['large','backward','installation','catalog','journal','storage','boolean'])
def test_lineage_or_unbounded_interval_stops_before_SQL(kind):
    before=generation(10);after=generation(11)
    if kind=='large':after['sequence']=10+j.MAX_DELTA_ROWS+1
    elif kind=='backward':after['sequence']=9
    elif kind=='boolean':before['sequence']=True
    else:after[{'installation':'installation_sha256','catalog':'catalog_sha256','journal':'journal_id','storage':'storage'}[kind]]='changed'
    cursor=Cursor([])
    with pytest.raises(ValueError):j.verify_transaction_delta(cursor,before,after,{('claims','C'):sig()},{('claims','C'):sig(2)})
    assert not cursor.calls


def test_actual_signature_chain_can_include_internal_repeated_write_only_in_declared_set():
    rows=[(11,'100','UPDATE','claims','C','claims','C',sig(),sig(2)),
          (12,'100','UPDATE','claims','C','claims','C',sig(2),sig(3))]
    assert j.verify_transaction_delta(Cursor(rows),generation(10),generation(12),
        {('claims','C'):sig()},{('claims','C'):sig(3)})['written_keys']==1


def test_installer_is_not_automatic_migration_or_a_baseline_certificate():
    from backend.api.canonical_repository.postgres_store import MIGRATIONS_DIR
    assert j.INSTALL_PATH.parent!=MIGRATIONS_DIR
    assert set(j._declared_bodies())==set(j.FUNCTIONS)
    assert 'UPDATE wang_knowledge.objects' not in j.INSTALL_PATH.read_text()
    assert 'baseline certification' in j.INSTALL_PATH.read_text()


@pytest.mark.parametrize('kind',['empty','repeat','invalid','missing','extra','retired'])
def test_exact_signature_access_preserves_absence_retirement_and_rejects_bad_population(kind):
    keys=[('claims','C')];rows=[('claims','C',sig(retired=kind=='retired'))]
    if kind=='empty':keys=[]
    elif kind=='repeat':keys*=2
    elif kind=='invalid':keys=['claims/C']
    elif kind=='missing':rows=[]
    elif kind=='extra':rows.append(('claims','unrelated',sig()))
    cursor=Cursor(rows)
    if kind!='retired':
        with pytest.raises(ValueError):j.read_object_signatures(cursor,keys)
    else:
        assert j.read_object_signatures(cursor,keys)[('claims','C')]['retired'] is True
        assert 'LEFT JOIN' in cursor.calls[0][0] and 'unnest' in cursor.calls[0][0]
        assert 'retired_at IS NULL' not in cursor.calls[0][0]
