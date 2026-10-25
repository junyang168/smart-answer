from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest
from backend.pipeline import source_reconciliation as c, source_reconciliation_runner as r
from backend.pipeline.claim_issue_management import retained_impact_context
from types import SimpleNamespace as NS
from backend.api.canonical_repository.postgres_store import sha256_json

@pytest.mark.parametrize('fault',[None,'missing','duplicate','pin','retired','hold','permission','post','foreign','impact','disposition'])
def test_auxiliary_receipt_requires_exact_unchanged_context(fault):
    key='knowledge_relations/R';required='canonical_viewpoints/V'
    old={'relation_id':'R','revision':1,'review_status':'candidate'}
    frozen={'before_records':[{'collection':'knowledge_relations','object_id':'R','payload':old}],
        'inventory':{'affected_keys':[key,required]},'claim_issue_context':{'issues':[{'id':'I'}]},
        'manifest':{'permissions':[]},'sql_scope':{'read_pins':[{'collection':'knowledge_relations','object_id':'R',
        'pin':[1,sha256_json({k:v for k,v in old.items() if k!='revision'}),False]}]}}
    proposal=NS(records=[],cvp_impacts=[NS(record_key=k,action='retain') for k in [key,required]],
        dispositions=[NS(record_key=key,action='retain')])
    post={key:deepcopy(old)};pin=frozen['sql_scope']['read_pins'][0]['pin']
    if fault=='missing':proposal.cvp_impacts.pop()
    if fault=='duplicate':proposal.cvp_impacts.append(deepcopy(proposal.cvp_impacts[0]))
    if fault=='pin':pin[0]=2
    if fault=='retired':pin[2]=True
    if fault=='hold':old['review_status']='human_hold'
    if fault=='permission':frozen['manifest']['permissions']=[{'collection':'knowledge_relations','object_id':'R'}]
    if fault=='post':post[key]['relation_id']='foreign'
    if fault=='foreign':proposal.cvp_impacts[0].record_key='knowledge_relations/foreign'
    if fault=='impact':proposal.cvp_impacts[0].action='needs_direction'
    if fault=='disposition':proposal.dispositions[0].action='unresolved'
    if fault:
        with pytest.raises(ValueError):retained_impact_context(frozen,proposal,post)
    else:
        receipt=retained_impact_context(frozen,proposal,post)
        assert receipt['required_cvp_record_keys']==[required]
        assert receipt['record_keys']==[key]
        assert not receipt['semantic_approval'] and not receipt['content_write_authority']

@pytest.mark.skipif(os.getenv('WKP357_EMPTY_STATEMENT_SAVED') != '1', reason='explicit current SQL saved proof')
@pytest.mark.parametrize('fault',[None,'missing','duplicate','pin','retired','hold','permission','post','proposed','foreign','impact','disposition','cache'])
def test_exact_saved_auxiliary_context(fault,tmp_path):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-5baf4616c077c3c118f2f36dc6254f84351ecca3d2c98d7114bf16e13e7def8f/claim-issues/eaececc27b862e2a2f0b72790ba47eb3b546aa44b759ac0533a62dd8c0dcb2e6/action/CVPA-7125653d81ab8fc2b1957ce9cd1d97bd73ee370054298c7226833f6925709269')
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    frozen=r.read(root/'scoped-materials.json');c.guard_files(frozen)
    load_dotenv('.env');store=PostgresKnowledgeStore()
    with store.connect() as conn,conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class Cursor:
            def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
            def __getattr__(self,n):return getattr(cursor,n)
        store._assert_transaction_read_pins(Cursor(),decoded_pins(frozen['sql_scope']['read_pins']))
    for stage in ['proposal','initial_review','correction']:
        raw=r.validate_raw(root,stage,r.body(r.read(root/(stage+'.packet.json'))))
    proposal=r.effective_proposal(frozen,raw['response'])
    post=c.index(frozen['before_records']);post.update(c.index(proposal.records))
    receipt=retained_impact_context(frozen,proposal,post)
    assert receipt and not receipt['content_write_authority'] and not receipt['semantic_approval']
    key=receipt['record_keys'][0];col,ident=c.split_key(key)
    pin=next(p['pin'] for p in frozen['sql_scope']['read_pins'] if p['collection']==col and p['object_id']==ident)
    impact=next(i for i in proposal.cvp_impacts if i.record_key==key)
    disp=next(d for d in proposal.dispositions if d.record_key==key)
    if fault=='missing':proposal.cvp_impacts=[i for i in proposal.cvp_impacts if i.record_key!=receipt['required_cvp_record_keys'][0]]
    if fault=='duplicate':proposal.cvp_impacts.append(deepcopy(impact))
    if fault=='pin':pin[0]+=1
    if fault=='retired':pin[2]=True
    if fault=='hold':post[key]['review_status']='human_hold'
    if fault=='permission':frozen['manifest']['permissions'].append({'collection':col,'object_id':ident,'fields':[]})
    if fault=='post':post[key]=dict(post[key],unexpected=True)
    if fault=='proposed':proposal.records.append(c.Row.model_validate(next(x for x in frozen['before_records'] if c.key(x['collection'],x['object_id'])==key)))
    if fault=='foreign':impact.record_key=col+'/foreign'
    if fault=='impact':impact.action='needs_direction'
    if fault=='disposition':disp.action='unresolved'
    if fault not in {None,'cache'}:
        with pytest.raises(ValueError):retained_impact_context(frozen,proposal,post)
    else:
        packet=c.review_packet(frozen,proposal)
        obj=r.Runner(tmp_path,None);obj.root=tmp_path
        full=obj.review_input(frozen,raw['response'],proposal,'final_review')
        assert full['retained_auxiliary_impact_context']==receipt
        assert len(proposal.records)==5
        assert full['proposal_sha256']==packet['proposal_sha256']
        r.write(tmp_path/'final_review.packet.json',full)
        assert obj.review_input(frozen,raw['response'],proposal,'final_review')==full
        if fault is None and (root/'final_review.raw.json').exists():
            final=r.validate_raw(root,'final_review',r.body(r.read(root/'final_review.packet.json')))
            assert c.validate_review(full,final['response'])
            material_sha=frozen['artifact_sha256']
            package=c.compile_package(frozen,proposal,final,frozen_sha256=material_sha)
            package['source_repair_application']={'run_root':str(root),'terminal_stage':'final_review',
                'review_sha256':final['artifact_sha256'],'proposal_sha256':sha256_json(proposal.model_dump())}
            r.validate_reviewed_records(package,frozen,frozen_sha256=material_sha)
        if fault=='cache':
            full['retained_auxiliary_impact_context']['semantic_approval']=True
            r.write(tmp_path/'final_review.packet.json',full,immutable=False)
            with pytest.raises(ValueError):obj.review_input(frozen,raw['response'],proposal,'final_review')
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
