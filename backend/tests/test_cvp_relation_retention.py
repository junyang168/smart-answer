"""Exact no-write retention, archived reviewed recovery and fail-closed cases."""
from copy import deepcopy
from dataclasses import replace

import pytest

from backend.api.canonical_repository.cvp_relation_retention import derive
from backend.api.canonical_repository.postgres_store import (
    ChangeOperation, ChangeSetConflict, canonical_json, record_content_sha, sha256_json,
)
from backend.api.canonical_repository.transaction_validation import TransactionValidation
from backend.tests.test_cvp_sql_transaction_postgres import task, apply
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


def fixture(plan):
    old = {'viewpoint_relation_id':'OLD','schema_version':'wang_viewpoint_relation_v1',
        'revision':1,'visibility':'internal','review_status':'system_approved',
        'effective_state':'active','source_viewpoint_id':'A','target_viewpoint_id':'B',
        'validated_source_viewpoint_revision_id':'RA','validated_target_viewpoint_revision_id':'RB',
        'relation_type':'extends','temporal_assertion':None,'supporting_claim_ids':[],
        'supporting_claim_relation_ids':[], 'reason':'old explanation',
        'review_provenance':{'review_artifact_sha256':'old'}}
    incoming = {**old,'viewpoint_relation_id':'NEW','reason':'new explanation',
                'review_provenance':{'review_artifact_sha256':'new'}}
    op = ChangeOperation(operation='create',collection='viewpoint_relations',object_id='NEW',
        payload=incoming,before_revision=None,before_sha256=None,after_revision=1,
        after_sha256=record_content_sha(incoming),removed_fields=())
    facts={('viewpoint_relations','OLD'):old}
    for vid,rid in [('A','RA'),('B','RB')]:
        facts['canonical_viewpoints',vid]={'viewpoint_id':vid,'current_revision_id':rid,
            'identity_status':'active','revision':1,'review_status':'system_approved','visibility':'internal'}
        facts['viewpoint_revisions',rid]={'viewpoint_revision_id':rid,'revision':1,
            'review_status':'system_approved','visibility':'internal'}
    pins={key:(1,record_content_sha(p),False) for key,p in facts.items() if key[1]!='OLD'}
    pins['viewpoint_relations','NEW']=None
    return replace(plan,operations=plan.operations+(op,)),facts,pins


class Cursor:
    def __init__(self,facts):self.facts=facts;self.rows=[];self.ambiguous=False;self.bad_history=False
    def execute(self,sql,args):
        if 'FROM wang_knowledge.edges' in sql:
            self.rows=[('OLD',1,self.facts['viewpoint_relations','OLD'])]
            if self.ambiguous:self.rows*=2
        elif 'FROM wang_knowledge.object_versions' in sql:
            p=self.facts['viewpoint_relations','OLD'];h=record_content_sha(p)
            self.rows=[] if self.bad_history else [('OLD-KCS',h,p,h,1,'applied')]
        else:
            col='viewpoint_relations' if "collection='viewpoint_relations'" in sql else (
                'canonical_viewpoints' if "collection='canonical_viewpoints'" in sql else 'viewpoint_revisions')
            p=self.facts[col,args[0]]
            self.rows=[(1,record_content_sha(p),p,None)]
            if col=='viewpoint_relations':self.rows[0]+=(p['review_status'],p['visibility'])
        return self
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0] if self.rows else None


def test_pure_retention_keeps_all_other_operations_and_original_provenance(postgres_store):
    plan,_=task(postgres_store);plan,facts,pins=fixture(plan)
    before=deepcopy(plan.as_dict());old=deepcopy(facts)
    derived,readset,receipt=derive(Cursor(facts),plan,pins)
    assert derived.operations==plan.operations[:-1]
    assert derived.unchanged==plan.unchanged+1
    assert derived.source_sha256==plan.source_sha256
    assert receipt['relation_writes']==0 and receipt['retained'][0]['retained_id']=='OLD'
    assert ('viewpoint_relations','OLD') in readset
    assert plan.as_dict()==before and facts==old


@pytest.mark.parametrize('old_status',['human_approved','approved','system_approved'])
@pytest.mark.parametrize('new_status',['human_approved','approved','system_approved'])
def test_approved_labels_do_not_change_retained_semantics(postgres_store,old_status,new_status):
    plan,_=task(postgres_store);plan,facts,pins=fixture(plan)
    facts['viewpoint_relations','OLD']['review_status']=old_status
    op=plan.operations[-1]
    payload={**op.payload,'review_status':new_status}
    plan=replace(plan,operations=plan.operations[:-1]+(replace(op,payload=payload,
                 after_sha256=record_content_sha(payload)),))
    before=deepcopy(facts);incoming=deepcopy(plan.as_dict())
    derived,_,receipt=derive(Cursor(facts),plan,pins)
    assert derived.operations==plan.operations[:-1] and receipt['relation_writes']==0
    assert facts==before and plan.as_dict()==incoming


@pytest.mark.parametrize('status',[None,'human_hold','rejected','unreviewed','withheld'])
def test_unapproved_incoming_status_cannot_be_hidden_by_retention(postgres_store,status):
    plan,_=task(postgres_store);plan,facts,pins=fixture(plan)
    op=plan.operations[-1];payload={**op.payload,'review_status':status}
    plan=replace(plan,operations=plan.operations[:-1]+(replace(op,payload=payload,
                 after_sha256=record_content_sha(payload)),))
    with pytest.raises(ChangeSetConflict):derive(Cursor(facts),plan,pins)


@pytest.mark.parametrize('field,value',[
    ('review_status','human_hold'),('effective_state','inactive'),
    ('validated_source_viewpoint_revision_id','other'),('temporal_assertion',{'changed':True}),
    ('supporting_claim_ids',['another']),('visibility','public'),('extra_semantic_field','different')])
def test_semantic_or_hold_difference_refused(postgres_store,field,value):
    plan,_=task(postgres_store);plan,facts,pins=fixture(plan)
    facts['viewpoint_relations','OLD'][field]=value
    with pytest.raises(ChangeSetConflict):derive(Cursor(facts),plan,pins)


@pytest.mark.parametrize('problem',['ambiguous','bad_history','pin','head','consumer','absence'])
def test_missing_conflicting_or_foreign_proof_refused(postgres_store,problem):
    plan,_=task(postgres_store);plan,facts,pins=fixture(plan);cursor=Cursor(facts)
    if problem in {'ambiguous','bad_history'}:setattr(cursor,problem,True)
    if problem=='pin':pins['viewpoint_revisions','RA']=(2,'wrong',False)
    if problem=='head':facts['canonical_viewpoints','A']['current_revision_id']='other'
    if problem=='consumer':plan=replace(plan,operations=(replace(plan.operations[0],payload={'ref':'NEW'}),)+plan.operations[1:])
    if problem=='absence':pins.pop(('viewpoint_relations','NEW'))
    with pytest.raises(ChangeSetConflict):derive(cursor,plan,pins)


def setup_sql(store,old_status='system_approved'):
    original,context=task(store)
    plan,facts,pins=fixture(original)
    plan=replace(plan,source_kind='cvp_batch_resolution')
    saved=store.get_cvp_group(context.run_id)
    evidence=saved['reviewed']['evidence']
    evidence['package']['sha256']=sha256_json(evidence['package']['payload'])
    for r in evidence['read_pins']:pins[r['collection'],r['object_id']]=tuple(r['pin']) if r['pin'] else None
    evidence['read_pins']=[{'collection':k[0],'object_id':k[1],'pin':list(v) if v else None} for k,v in sorted(pins.items())]
    review={'plan':plan.as_dict(),'evidence':evidence};digest=sha256_json(review)
    saved.update(reviewed=review,reviewed_sha256=digest);saved.pop('run_id')
    old=facts['viewpoint_relations','OLD'];old['review_status']=old_status;h=record_content_sha(old)
    with store.connect() as c:
        for (col,oid),p in facts.items():
            c.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES (%s,%s,1,%s,'internal',%s,%s::jsonb)",
                (col,oid,p['review_status'],record_content_sha(p),canonical_json(p)))
        c.execute("INSERT INTO wang_knowledge.change_sets(change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status) VALUES ('OLD-KCS',%s,'old','fixture',%s,'applied')",('1'*64,'2'*64))
        c.execute("INSERT INTO wang_knowledge.object_versions(collection,object_id,revision,content_sha256,payload,change_set_id) VALUES ('viewpoint_relations','OLD',1,%s,%s::jsonb,'OLD-KCS')",(h,canonical_json(old)))
        c.execute("INSERT INTO wang_knowledge.change_operations(change_set_id,operation_index,operation,collection,object_id,after_sha256,after_revision) VALUES ('OLD-KCS',0,'create','viewpoint_relations','OLD',%s,1)",(h,))
        c.execute("INSERT INTO wang_knowledge.edges(edge_collection,edge_id,from_id,to_id,relation_type,revision,payload) VALUES ('viewpoint_relations','OLD','A','B','extends',1,%s::jsonb)",(canonical_json(old),))
        c.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s",(canonical_json(saved),context.run_id))
    return plan,context.run_id,digest,facts


@pytest.mark.parametrize('old_status',['system_approved','human_approved'])
def test_sql_archive_exact_once_and_late_rollback(postgres_store,monkeypatch,old_status):
    store=postgres_store;plan,run,digest,facts=setup_sql(store,old_status)
    before=deepcopy(store.get_cvp_group(run)['reviewed'])
    next_digest=store.retain_reviewed_cvp_relations(run,plan=plan,reviewed_sha256=digest)
    from backend.pipeline.cvp_sql_flow import plan_from_document,decoded_pins
    saved=store.get_cvp_group(run);next_plan=plan_from_document(saved['reviewed']['plan'])
    assert store.retain_reviewed_cvp_relations(run,plan=next_plan,reviewed_sha256=next_digest)==next_digest
    with store.connect() as c:
        archive=c.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE metadata->>'kind'='cvp_mechanical_relation_retention_archive'").fetchall()
        assert len(archive)==1 and archive[0][0]['reviewed']==before
    ctx=TransactionValidation(run,next_digest,decoded_pins(saved['reviewed']['evidence']['read_pins']))
    def fail(*args):raise ValueError('late guard')
    guard=store._assert_transaction_edges_and_reviews
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',fail)
    with pytest.raises(ValueError,match='late guard'):apply(store,next_plan,ctx)
    assert store.get_change_history(next_plan.change_set_id) is None
    assert store.get_record('viewpoint_relations','OLD')==facts['viewpoint_relations','OLD']
    assert store.get_record('viewpoint_relations','NEW') is None
    monkeypatch.setattr(store,'_assert_transaction_edges_and_reviews',guard)
    assert apply(store,next_plan,ctx)['status']=='applied'
    assert apply(store,next_plan,ctx)['status']=='already_applied'


def test_sql_stale_pin_rolls_back_archive_and_review(postgres_store):
    store=postgres_store;plan,run,digest,_=setup_sql(store)
    before=deepcopy(store.get_cvp_group(run))
    with store.connect() as c:
        c.execute("UPDATE wang_knowledge.objects SET content_sha256=%s WHERE object_id='RA'",('f'*64,))
    with pytest.raises(ChangeSetConflict):store.retain_reviewed_cvp_relations(run,plan=plan,reviewed_sha256=digest)
    assert store.get_cvp_group(run)==before
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM wang_knowledge.pipeline_runs WHERE metadata->>'kind'='cvp_mechanical_relation_retention_archive'").fetchone()==(0,)
