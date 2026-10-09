from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import os
import pytest
from backend.pipeline import cvp_pending_link_management as management
from backend.pipeline import source_reconciliation as c
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


@pytest.fixture
def candidate(monkeypatch):
    old={'viewpoint_claim_link_id':'L','claim_id':'C','viewpoint_id':'V','revision':2,'review_status':'system_approved',
        'link_type':'supports','effective_state':'active','pinned_claim_revision':3,
        'validated_against_viewpoint_revision_id':'R','occurrence_refs':['O'],'supporting_relation_ids':[]}
    new={**old,'revision':3,'review_status':'candidate','effective_state':'retired'}
    frozen={'before_records':c.rows({'viewpoint_claim_links/L':old}),'manifest':{}}
    permission=SimpleNamespace(key='viewpoint_claim_links/L',create=False,fields=['effective_state'])
    monkeypatch.setattr(c.Manifest,'model_validate',lambda *_:SimpleNamespace(permissions=[permission]))
    monkeypatch.setattr(c,'validate_proposal',lambda *a,**k:None)
    corrected=c.Proposal.model_validate({'records':c.rows({'viewpoint_claim_links/L':new}),
        'dispositions':[{'record_key':'viewpoint_claim_links/L','action':'update','reason':'exact saved proposal'}]})
    packet={'proposal':corrected.model_dump()}
    raw={'response':{'verdicts':[{'record_key':'viewpoint_claim_links/L','approved':True,'problems':[],'reason':'per-record preliminary approval, not package authority'}]}}
    return locals()


def project(f):
    return management.candidate_projection(f['frozen'],f['packet'],f['raw'],f['corrected'],
        pending_keys=['viewpoint_claim_links/L'],protected_ids=f.get('protected',set()))


def test_exact_candidate_is_not_partial_approval_or_extra_gpt_budget(candidate):
    f=candidate;before=deepcopy(f['raw']);result=project(f)
    assert result['viewpoint_claim_links/L']['effective_state']=='retired'
    assert f['raw']==before


@pytest.mark.parametrize('fault',['refusal','problems','missing-verdict','duplicate','corrected-change','protected',
    'field-scope','create','old-inactive','claim','view','type','claim-version','cvr','occurrence','relation','state','equivalence-retire'])
def test_projection_cannot_retry_refused_candidate_change_endpoints_or_expand_source_scope(candidate,fault):
    f=candidate;r=f['raw']['response']['verdicts'];p=f['permission'];record=f['corrected'].records[0].payload
    if fault=='refusal':r[0]['approved']=False
    elif fault=='problems':r[0]['problems']=['genuine_content_problem']
    elif fault=='missing-verdict':r.clear()
    elif fault=='duplicate':r*=2
    elif fault=='corrected-change':record['effective_state']='held'
    elif fault=='protected':f['protected']={'L'}
    elif fault=='field-scope':p.fields+=['claim_id']
    elif fault=='create':p.create=True
    elif fault=='old-inactive':f['frozen']['before_records'][0]['payload']['effective_state']='held'
    elif fault=='state':record['effective_state']='held';f['packet']['proposal']=f['corrected'].model_dump()
    elif fault=='equivalence-retire':f['frozen']['before_records'][0]['payload']['link_type']='equivalent_component';record['link_type']='equivalent_component';f['packet']['proposal']=f['corrected'].model_dump()
    else:
        k={'claim':'claim_id','view':'viewpoint_id','type':'link_type','claim-version':'pinned_claim_revision',
           'cvr':'validated_against_viewpoint_revision_id','occurrence':'occurrence_refs','relation':'supporting_relation_ids'}[fault]
        record[k]=['foreign'] if k.endswith('_refs') or k=='supporting_relation_ids' else 'foreign'
        f['packet']['proposal']=f['corrected'].model_dump()
    with pytest.raises(ValueError):project(f)


def test_actual_independent_refusal_never_compiles_partial_writes():
    p=c.Proposal.model_validate({'records':[], 'dispositions':[{'record_key':'viewpoint_claim_links/L','action':'retain','reason':'fixture'}]})
    material={'proposal':p.model_dump(),'packet':{'proposal':p.model_dump(),'proposal_sha256':c.sha256_json(p.model_dump())}}
    raw={'response':{'proposal_sha256':material['packet']['proposal_sha256'],
        'verdicts':[{'record_key':'viewpoint_claim_links/L','approved':False,'problems':['refused'],'reason':'real refusal'}]}}
    with pytest.raises(ValueError,match='independent refusal'):
        management.compile_package(material,raw,Path('/unused'))


@pytest.mark.skipif(os.environ.get('WKP_CVP_PENDING_LINK_REAL_ACCEPTANCE')!='1',reason='exact saved parent authentication/live SQL read only')
def test_actual_parent_cached_projection_complete_body_and_no_new_calls(monkeypatch,postgres_store):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.viewpoint_resolution_runtime import _saved_validation
    load_dotenv('.env');store=PostgresKnowledgeStore()
    run='CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83'
    before=store.get_cvp_group(run);token=_saved_validation.set(True)
    try:material=management.prepare_material(store,run)
    finally:_saved_validation.reset(token)
    assert store.get_cvp_group(run)==before
    assert len(material['target_record_keys'])==4 and len(material['frozen']['before_records'])==90
    assert sorted(len(x['paragraphs']) for x in material['frozen']['after']+material['frozen']['context_sources'])==[17,18,80]
    adapter=management.scoped_adapter(model_adapter('initial_review'),material['packet'])
    assert adapter.model_id=='claude-opus-5-5' and adapter.backend=='claude_subscription'
    assert adapter.request_bytes(material['packet'])<600000
    assert adapter.request_encoding_proof(material['packet'])['exact_roundtrip_verified'] is True
    print('ACTUAL_PENDING_DOMAIN_PREFLIGHT',{'targets':material['target_record_keys'],
        'bytes':adapter.request_bytes(material['packet']),'new_gpt_calls':0,'new_opus_calls':0,'registry_writes':0})
    # Fake approval is confined to disposable SQL transaction testing, NOT a
    # production artifact or a substitute for the pending actual Opus call.
    raw={'artifact_sha256':'f'*64,'completed_at':'2026-10-09T22:45:00+00:00',
         'response':{'proposal_sha256':material['packet']['proposal_sha256'],
             'verdicts':[{'record_key':k,'approved':True,'problems':[],
                         'reason':'SYNTHETIC DISPOSABLE TEST ONLY'} for k in material['target_record_keys']]}}
    package=management.compile_package(material,raw,Path('/unused-synthetic-test'))
    from backend.api.canonical_repository import reviewed_candidate_contract
    monkeypatch.setattr(reviewed_candidate_contract,'validate_store_package_authorization',lambda _:None)
    from backend.api.canonical_repository import postgres_store as store_module
    monkeypatch.setattr(store_module,'validate_store_package_authorization',lambda _:None)
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    from backend.api.canonical_repository.transaction_validation import TransactionValidation
    target=postgres_store;ids=[r['object_id'] for r in material['read_pins']]
    with store.connect() as src,target.connect() as dest:
        for table,where in [('objects','object_id=ANY(%s)'),('edges','edge_id=ANY(%s)')]:
            cursor=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),(ids,))
            cols=[x.name for x in cursor.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),
                SQL(',').join(map(Identifier,cols)),SQL(',').join(Placeholder()*len(cols)))
            for row in cursor.fetchall():dest.execute(insert,tuple(Jsonb(x) if isinstance(x,(dict,list)) else x for x in row))
        meta={k:v for k,v in before.items() if k not in {'status','error'}}
        dest.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES (%s,'batch','isolated','cvp_group','cli','running',%s::jsonb)",(run,canonical_json(meta)))
    plan=target.plan_package(package,transactional=True,source_kind=management.VERSION)
    assert len(plan.operations)==12 and not any(o.collection in {'claims','evidence_steps','source_documents','source_fragments','canonical_viewpoints','viewpoint_revisions'} for o in plan.operations)
    evidence=deepcopy(before['reviewed']['evidence'])
    action=target.prepare_cvp_action(run,action_id='fixture-pending-links',kind='owner_link_correction',
        authorization={'synthetic_only':True},materials=material)
    from backend.pipeline.cvp_sql_flow import decoded_pins,pin_rows
    pins=decoded_pins(material['read_pins'])
    for op in plan.operations:pins.setdefault((op.collection,op.object_id),None)
    evidence.update(action=action,package={'payload':package},read_pins=pin_rows(pins))
    evidence['execution']={'actor':'pytest','reason':'synthetic isolated only'}
    digest=target.review_cvp_group(run,plan=plan,evidence=evidence)
    context=TransactionValidation(run,digest,pins)
    original=target._assert_transaction_edges_and_reviews
    def late(*a,**k):original(*a,**k);raise ValueError('late isolated pending validation')
    monkeypatch.setattr(target,'_assert_transaction_edges_and_reviews',late)
    with pytest.raises(ValueError,match='late isolated'):
        target.apply_plan(plan,transaction_context=context,metadata={'actor':'pytest','reason':'synthetic isolated only'})
    assert target.get_change_set_status(plan.fingerprint_sha256) is None
    for row in material['frozen']['before_records']:assert target.get_record_state(row['collection'],row['object_id'])['payload']==row['payload']
    monkeypatch.setattr(target,'_assert_transaction_edges_and_reviews',original)
    result=target.apply_plan(plan,transaction_context=context,metadata={'actor':'pytest','reason':'synthetic isolated only'})
    history=target.get_change_history(result['change_set_id'])
    assert len(history['operations'])==12
    for op in history['operations']:
        actual=target.get_record_state(op['collection'],op['object_id'])
        assert actual['payload']==op['after'] and actual['content_sha256']==record_content_sha(op['after'])
    changed=set(material['target_record_keys'])
    for row in material['frozen']['before_records']:
        if c.key(row['collection'],row['object_id']) not in changed:
            assert target.get_record_state(row['collection'],row['object_id'])['payload']==row['payload']
    assert target.apply_plan(plan,transaction_context=context,metadata={'actor':'pytest','reason':'synthetic isolated only'})['status']=='already_applied'
    assert len(target.get_cvp_group(run)['reviewed']['evidence']['pending_cvp_management_record_keys'])==24


@pytest.mark.parametrize('verdict,link,blocked',[
    ('audit_objection_rejected','target',False),
    ('audit_objection_upheld','target',True),
    ('unresolved','target',True),
    ('audit_objection_upheld','other',False),
])
def test_actual_pending_link_repair_cannot_loop_new_semantic_objection(tmp_path,verdict,link,blocked):
    from backend.pipeline.source_reconciliation_runner import write
    from backend.pipeline.cvp_sql_flow import binding
    p=tmp_path/'material.json';write(p,{'target_record_keys':['viewpoint_claim_links/target']})
    value={'reviewed':{'evidence':{'approval':{'pending_link_material':binding(p)}}}}
    packet={'items':[{'item_id':'4:V:C','connections':[{'viewpoint_claim_link_id':link}]}]}
    report={'actions':[{'item_id':'4:V:C','verdict':verdict}]}
    if blocked:
        with pytest.raises(ValueError,match='no second automatic link repair'):
            management.guard_repeated_semantic_repair(value,packet,report)
    else:management.guard_repeated_semantic_repair(value,packet,report)
