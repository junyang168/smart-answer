"""Merge lifecycle fixtures are never provider approval or production writes."""
from copy import deepcopy
from pathlib import Path
import os
import json
import pytest
from backend.pipeline import cvp_merge_management as f
from backend.pipeline.source_reconciliation_runner import Stop,write
from backend.pipeline.cvp_sql_flow import binding
from backend.api.canonical_repository.postgres_store import sha256_json,canonical_json,record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import postgres_store,_temporary_postgres

ROOT=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
RUN='CVPG-a85c3d6eb392f97e945d88a18fbf33a76ed136eefefbb4554759e82e0e26deb2'
IDS=['CV-4525b1c29b8fd0215f9f','CV-56e1e3bed8362743478c']
OLD=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/viewpoint-backfill/wkp315-lane-repair-2026-08-31/resolution-p2s/batch-004')


def synthetic():
    claim={'claim_id':'C','revision':1,'statement':'不是甲而是乙','evidence_step_ids':['E'],
        'occurrences':[{'source_id':'S'}]}
    links=[{'viewpoint_claim_link_id':'L'+i,'claim_id':'C','revision':1,'viewpoint_id':i,
        'pinned_claim_revision':1,'link_type':'equivalent_component','effective_state':'active',
        'validated_against_viewpoint_revision_id':'R'+i,'evidence_bindings':[{'evidence_step_id':'E','source_fragment_id':'F'}],
        'occurrence_refs':['O'],'supporting_relation_ids':[],'decision_id':'D'+i} for i in ['A','B']]
    rows=[('claims','C',claim),('evidence_steps','E',{'evidence_step_id':'E','source_fragment_id':'F','support_eligibility':'eligible','valid_for_identity_review':True}),
        ('source_fragments','F',{'fragment_id':'F','source_id':'S'})]
    for i in ['A','B']:
        rows.append(('canonical_viewpoints',i,{'viewpoint_id':i,'current_revision_id':'R'+i,'identity_status':'active',
            'review_status':'system_approved','revision':1,'created_from_candidate_id':'I'+i}))
        rows.append(('viewpoint_claim_links','L'+i,links[['A','B'].index(i)]))
        rows.append(('viewpoint_revisions','R'+i,{'viewpoint_revision_id':'R'+i,'viewpoint_id':i,'revision':1}))
    m={'schema_version':f.SCHEMA,'viewpoint_ids':['A','B'],'claim_ids':['C'],'before_links':links,'relations':[],
        'records':[{'collection':c,'object_id':i,'payload':p} for c,i,p in rows],
        'sources':[{'source_id':'S','paragraphs':[{'index':0,'text':'不是甲而是乙'}]}],
        'original_generation_context':[],'owner_direction':'TEST','program_provenance_contract':'TEST',
        'read_pins':[],'consumer_keys':[],'claim_authority':[]}
    a={'outcome':'merge','core_proposition':'不是甲而是乙','proposition_signature':{'subject':'称号','predicate':'强调',
        'object':'乙而非甲','polarity':'affirmed','modality':'断言'},'scope':{'scripture_scope':[]},
        'members':[{'claim_id':'C','disposition':'linked','reason':'SIMULATED TEST ONLY','citations':[
            {'source_id':'S','paragraph_index':0,'exact_quote':'不是甲而是乙'}],
            'links':[{'link_type':'equivalent_full','spans':[{'start_char':0,'end_char':6,'exact_text':'不是甲而是乙'}],
                'evidence_bindings':[{'evidence_step_id':'E','source_fragment_id':'F'}],'reason':'SIMULATED TEST ONLY'}]}],
        'relations':[],'reason':'SIMULATED TEST ONLY'}
    return m,a


def test_duplicate_old_members_reorganized_once_preserve_input():
    m,a=synthetic();old=deepcopy(m);out=f.postimage(m,a)
    assert len(out['links'])==1 and out['links'][0]['claim_id']=='C'
    assert out['retiring_keys']==[('viewpoint_claim_links','LA'),('viewpoint_claim_links','LB')]
    assert len(out['old_heads'])==2 and all(h['identity_status']=='merged' for h in out['old_heads'])
    assert m==old and out['viewpoint_id'] not in ['A','B']


@pytest.mark.parametrize('fault',['missing','duplicate','foreign','unresolved','text','offset','overlap','empty',
    'foreign-evidence','duplicate-evidence','context-only','withheld','foreign-source','foreign-citation','duplicate-role','no-equivalent'])
def test_member_denominator_source_eligibility_roles_and_exact_locator_refuse(fault):
    m,a=synthetic();x=a['members'][0];l=x['links'][0]
    if fault=='missing':a['members']=[]
    elif fault=='duplicate':a['members'].append(deepcopy(x))
    elif fault=='foreign':x['claim_id']='other'
    elif fault=='unresolved':x['disposition']='unresolved'
    elif fault=='text':l['spans'][0]['exact_text']='不存在字句'
    elif fault=='offset':l['spans'][0]['start_char']=1
    elif fault=='overlap':l['spans'].append(deepcopy(l['spans'][0]))
    elif fault=='empty':l['spans']=[]
    elif fault=='foreign-evidence':l['evidence_bindings'][0]['evidence_step_id']='foreign'
    elif fault=='duplicate-evidence':l['evidence_bindings']*=2
    elif fault=='context-only':m['records'][1]['payload']['support_eligibility']='context_only'
    elif fault=='withheld':m['records'][1]['payload']['valid_for_identity_review']=False
    elif fault=='foreign-source':m['records'][2]['payload']['source_id']='other'
    elif fault=='foreign-citation':x['citations'][0]['source_id']='other'
    elif fault=='duplicate-role':x['links'].append(deepcopy(l))
    elif fault=='no-equivalent':l['link_type']='supports'
    with pytest.raises((ValueError,KeyError)):f.postimage(m,a)


@pytest.mark.parametrize('fault',['missing','duplicate','foreign','reject','problems'])
def test_full_independent_review_not_only_the_core(fault):
    m,a=synthetic();ids=f.required_verdict_ids(m)
    v=[{'claim_id':i,'approved':True,'problems':[],'reason':'SIMULATED TEST ONLY'} for i in ids]
    if fault=='missing':v.pop()
    elif fault=='duplicate':v.append(deepcopy(v[0]))
    elif fault=='foreign':v[0]['claim_id']='foreign'
    elif fault=='reject':v[0]['approved']=False
    elif fault=='problems':v[0]['problems']=['not complete']
    with pytest.raises(ValueError):f.approved(m,{'response':{'verdicts':v}})


def test_initial_approved_cannot_repeat_budget(tmp_path,monkeypatch):
    m,a=synthetic();p={'response':a,'artifact_sha256':'P'}
    r={'response':{'verdicts':[{'claim_id':i,'approved':True,'problems':[],
        'reason':'SIMULATED TEST ONLY'} for i in f.required_verdict_ids(m)]}}
    calls=[]
    def cached(root,stage,packet,**kw):calls.append(stage);return p if stage=='proposal' else r
    monkeypatch.setattr(f,'saved_raw',cached)
    (tmp_path/'correction.call-intent.json').write_text('UNKNOWN TEST ONLY')
    with pytest.raises(ValueError,match='another review budget'):f.effective_saved(tmp_path,m)
    assert calls==['proposal','initial_review']


@pytest.mark.parametrize('refuse',[False,True])
def test_original_only_correction_and_final_preserve_refusal(tmp_path,monkeypatch,refuse):
    m,a=synthetic();p={'response':a,'artifact_sha256':'P'}
    r={'response':{'verdicts':[{'claim_id':i,'approved':False,'problems':['ORIGINAL'],
        'reason':'SIMULATED TEST ONLY'} for i in f.required_verdict_ids(m)]},'artifact_sha256':'R'}
    final=deepcopy(r)
    if not refuse:
        for v in final['response']['verdicts']:v.update(approved=True,problems=[])
    original=deepcopy(r);calls=[]
    def cached(root,stage,packet,**kw):
        calls.append(stage)
        if stage=='correction':assert packet['initial_review']==original['response']
        return {'proposal':p,'initial_review':r,'correction':p,'final_review':final}[stage]
    monkeypatch.setattr(f,'saved_raw',cached)
    if refuse:
        with pytest.raises(Stop):f.effective_saved(tmp_path,m)
    else:assert f.effective_saved(tmp_path,m)==(p,final)
    assert calls==['proposal','initial_review','correction','final_review'] and r==original


def test_merge_fixed_models_profile_and_schema_no_recovery_alias():
    for stage in ['proposal','correction']:
        a=f.adapter(stage);assert(a.model_id,a.backend)==('gpt-6.1-sol','codex_subscription')
    for stage in ['initial_review','final_review']:
        a=f.adapter(stage);assert(a.model_id,a.backend)==('claude-opus-5-5','claude_subscription')
    with pytest.raises(ValueError):f.adapter('second_correction')


@pytest.mark.parametrize('fault',['missing','foreign','duplicate','unresolved','wrong_internal','mixed_endpoint','mixed_type','duplicate_projection'])
def test_relations_require_complete_explicit_independent_dispositions(fault):
    m,a=synthetic()
    for vid in ['X','Y']:
        m['records'].extend([{'collection':'canonical_viewpoints','object_id':vid,
            'payload':{'viewpoint_id':vid,'current_revision_id':'R'+vid}},
            {'collection':'viewpoint_revisions','object_id':'R'+vid,'payload':{'viewpoint_id':vid}}])
    rel={'viewpoint_relation_id':'REL','source_viewpoint_id':'A','target_viewpoint_id':'X',
        'relation_type':'entails','revision':1,'supporting_claim_ids':['C'],'supporting_claim_relation_ids':[]}
    m['relations']=[rel];a['relations']=[{'old_relation_ids':['REL'],'disposition':'reconnect','reason':'TEST'}]
    x=a['relations'][0]
    if fault=='missing':a['relations']=[]
    elif fault=='foreign':x['old_relation_ids']=['FOREIGN']
    elif fault=='duplicate':x['old_relation_ids']*=2
    elif fault=='unresolved':x['disposition']='unresolved'
    elif fault=='wrong_internal':x['disposition']='internalize'
    else:
        other=deepcopy(rel);other['viewpoint_relation_id']='REL2';m['relations'].append(other)
        if fault=='mixed_endpoint':other['target_viewpoint_id']='Y'
        if fault=='mixed_type':other['relation_type']='extends'
        if fault=='duplicate_projection':a['relations'].append({'old_relation_ids':['REL2'],'disposition':'reconnect','reason':'TEST'})
        else:x['old_relation_ids'].append('REL2')
    with pytest.raises(ValueError):f.postimage(m,a)


@pytest.mark.parametrize('col',['claims','viewpoint_claim_links','source_fragments','viewpoint_revisions'])
def test_content_projection_cannot_hide_a_semantic_write_dependency(col):
    m,a=synthetic();m['semantic_record_keys']=[r['collection']+'/'+r['object_id'] for r in m['records'] if r['collection']!=col]
    with pytest.raises(ValueError,match='denominator'):f.proposal_packet(m)


def test_merge_once_identity_refuses_new_finder_and_root(postgres_store,tmp_path):
    s=postgres_store;args={'identity':'merge-test-generation','parent_run_id':'original',
        'root':tmp_path/'original','handoff_id':'fixed'}
    s.reserve_cvp_membership_identity(**args);s.reserve_cvp_membership_identity(**args)
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    for bad in [{**args,'parent_run_id':'another'},{**args,'root':tmp_path/'another'}, {**args,'handoff_id':'new'}]:
        with pytest.raises(ChangeSetConflict):s.reserve_cvp_membership_identity(**bad)


def real_material(tmp_path):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            c=super().connect();c.execute('SET TRANSACTION READ ONLY');return c
    load_dotenv('.env');s=ReadOnly()
    d=f.owner_direction(s,RUN,IDS,authorization_quote='ISOLATED TEST ONLY of authorized merge',
        requested_proposition='人子不是強調耶穌的人性，而是強調耶穌的神性。',
        original_artifacts=[binding(OLD/'raw-final-review.json'),binding(OLD/'raw-reconsideration.json')])
    path=tmp_path/'direction.json';path.write_text(json.dumps(d,ensure_ascii=False))
    return s,f.prepare_material(s,RUN,binding(path)),d


def simulated_proposal(m):
    states={(r['collection'],r['object_id']):r['payload'] for r in m['records']}
    a=synthetic()[1];a['members']=[]
    for cid in m['claim_ids']:
        cl=states['claims',cid];text=cl['statement']
        bindings=sorted({(b['evidence_step_id'],b['source_fragment_id']) for l in m['before_links']
            if l['claim_id']==cid for b in l['evidence_bindings']
            if states['evidence_steps',b['evidence_step_id']].get('support_eligibility') in {'eligible','eligible_with_label','eligible_candidate'}})
        own=f.own_source_ids({c+'/'+i:p for (c,i),p in states.items()},cl);src=next(s for s in m['sources'] if s['source_id'] in own)
        quote=src['paragraphs'][0]
        a['members'].append({'claim_id':cid,'disposition':'linked','reason':'DISPOSABLE SQL DOMAIN FIXTURE NOT MODEL APPROVAL',
            'citations':[{'source_id':src['source_id'],'paragraph_index':quote['index'],'exact_quote':quote['text']}],
            'links':[{'link_type':'equivalent_full','spans':[{'start_char':0,'end_char':len(text),'exact_text':text}],
                'evidence_bindings':[{'evidence_step_id':e,'source_fragment_id':i} for e,i in bindings],
                'reason':'DISPOSABLE SQL DOMAIN FIXTURE NOT SEMANTIC APPROVAL'}]})
    groups={}
    for r in m['relations']:
        key=(r['relation_type'],r['source_viewpoint_id'] if r['source_viewpoint_id'] not in IDS else 'MERGE',
            r['target_viewpoint_id'] if r['target_viewpoint_id'] not in IDS else 'MERGE')
        groups.setdefault(key,[]).append(r['viewpoint_relation_id'])
    a['relations']=[{'old_relation_ids':sorted(ids),'disposition':'reconnect','reason':'DISPOSABLE TEST ONLY'} for ids in groups.values()]
    return a


@pytest.mark.skipif(os.getenv('WKP357_MERGE_SAVED')!='1',reason='explicit readonly actual full graph')
def test_actual_complete_prepare_models_original_approval_sources_and_no_writes(tmp_path):
    s,m,d=real_material(tmp_path);before=s.get_cvp_group(RUN)
    assert len(m['before_links'])==10 and len(m['claim_ids'])==6 and len(m['relations'])==3
    assert len(m['original_generation_context'])==10 and m['sources']
    for field in ['viewpoint_pins','campaign_sha256','run_id','human_content_approval','policy']:
        bad=deepcopy(d);bad[field]='foreign';bad['artifact_sha256']=sha256_json({k:v for k,v in bad.items() if k!='artifact_sha256'})
        with pytest.raises((ValueError,TypeError,KeyError)):f.validate_direction(bad,m)
    assert s.get_cvp_group(RUN)==before


@pytest.mark.skipif(os.getenv('WKP357_MERGE_SAVED')!='1',reason='actual saved graph disposableSQL merge lifecycle')
def test_actual_disposable_sql_full_merge_commit_rollback_cache_completion(postgres_store,tmp_path,monkeypatch):
    from backend.api.canonical_repository.transaction_validation import TransactionValidation
    from backend.pipeline.cvp_sql_flow import decoded_pins,pin_rows
    production,m,d=real_material(tmp_path);s=postgres_store
    # Clone exact relevant current graph and its actual indexed history into
    # socket-only disposable DB. This is not a synthetic provider approval.
    rows=production.read_affected_context([('canonical_viewpoints',i) for i in IDS])
    rows.update(production.read_cvp_context(m['original_claim_ids'],viewpoint_ids=m['prior_viewpoint_ids']))
    dependency_run='CVPG-5baf4616c077c3c118f2f36dc6254f84351ecca3d2c98d7114bf16e13e7def8f'
    dependency=production.get_cvp_group(dependency_run)
    from backend.pipeline.claim_dependency_handoff import context as dependency_context,subject
    _,dependency_cid,source_material=subject(dependency)
    for p in source_material['sql_scope']['read_pins']:
        key=(p['collection'],p['object_id']);rows[key]=production.get_record_state(*key)
    for op in dependency['reviewed']['plan']['operations']:
        key=(op['collection'],op['object_id']);rows[key]=production.get_record_state(*key)
    source_runs={}
    with production.connect() as conn:
        for run,meta in conn.execute("SELECT run_id,metadata FROM wang_knowledge.pipeline_runs WHERE stage='cvp_group' AND (metadata->>'sequence')::int IN (120,143) AND metadata->>'campaign_sha256'=%s",(m['campaign_sha256'],)).fetchall():
            source_runs[run]=meta
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    for meta in source_runs.values():
        rows.update(production.read_cvp_context(meta['claim_ids'],viewpoint_ids=meta['reviewed']['evidence']['viewpoint_ids']))
        old=read_artifact(meta['completion']['retained_impacts_resolution']['path'])
        for p in old['read_pins']:
            key=(p['collection'],p['object_id']);rows[key]=production.get_record_state(*key)
    assert all(rows.values())
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    ids=sorted({i for col,i in rows})
    with production.connect() as src,s.connect() as c:
        histories=sorted({r[0] for r in src.execute('SELECT DISTINCT change_set_id FROM wang_knowledge.object_versions WHERE object_id=ANY(%s)',(ids,)).fetchall()}
            |set(dependency['change_set_ids'])|{i for meta in source_runs.values() for i in meta['change_set_ids']})
        for table,where,args in [
            ('change_sets','change_set_id=ANY(%s)',(histories,)),('objects','object_id=ANY(%s)',(ids,)),
            ('object_versions','object_id=ANY(%s)',(ids,)),('change_operations','change_set_id=ANY(%s)',(histories,)),
            ('edges','edge_id=ANY(%s)',(ids,)),('review_events','object_id=ANY(%s)',(ids,)),
            ('pipeline_runs',"run_id=ANY(%s) OR metadata->>'parent_run_id'=ANY(%s) OR metadata->>'owner_run_id'=ANY(%s)",
                ([dependency_run,*source_runs],[dependency_run,*source_runs],[dependency_run,*source_runs]))]:
            data=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),args)
            columns=[d.name for d in data.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),
                SQL(',').join(map(Identifier,columns)),SQL(',').join(Placeholder()*len(columns)))
            for row in data.fetchall():c.execute(insert,tuple(Jsonb(v) if isinstance(v,(dict,list))
                and name not in {'source_ids','output_paths'} else v for name,v in zip(columns,row)))
        meta={k:v for k,v in production.get_cvp_group(RUN).items() if k not in {'status','error'}}
        c.execute("INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata) VALUES(%s,'batch','disposable','cvp_group','cli','running',%s::jsonb)",(RUN,canonical_json(meta)))
    original_dependency=dependency_context(s.get_cvp_group(dependency_run),s)
    a=simulated_proposal(m);p={'response':a,'artifact_sha256':'simulated-proposal'}
    r={'response':{'verdicts':[{'claim_id':i,'approved':True,'problems':[],
        'reason':'DISPOSABLE SQL DOMAIN FIXTURE NOT PROVIDER APPROVAL'} for i in f.required_verdict_ids(m)]},
        'artifact_sha256':'simulated-review','completed_at':'2026-10-12T00:00:00Z'}
    write(tmp_path/'materials.json',m);monkeypatch.setattr(f,'effective_saved',lambda *args:(p,r))
    package=f.compile_package(m,p,r,tmp_path);f.validate_package(package)
    plan=s.plan_package(package,source_kind=f.SCHEMA,transactional=True)
    assert not any(o.collection in {'claims','source_documents','source_fragments','evidence_steps'} for o in plan.operations)
    pins=decoded_pins(m['read_pins'])
    for op in plan.operations:pins.setdefault((op.collection,op.object_id),None)
    new=f.postimage(m,a)['viewpoint_id'];views=sorted((set(m['prior_viewpoint_ids'])-set(IDS))|{new})
    action=s.prepare_cvp_action(RUN,action_id=f.action_id(d),kind='cvp_identity_merge',authorization=m['authorization'],materials=m)
    def review():
        return s.review_cvp_group(RUN,plan=plan,evidence={'read_pins':pin_rows(pins),'viewpoint_ids':views,
            'package':{'sha256':sha256_json(package),'payload':package},'authorization':m['authorization'],
            'backup':{'disposable_only':True},'sources':m['sources'],'approval':{'disposable_only':True},'action':action})
    old={k:s.get_record_state(*k) for k in rows};seal=review();guard=s._assert_transaction_edges_and_reviews
    metadata={'reason':'disposable merge','actor':'pytest','approval':{'disposable_only':True},
        'authorization':m['authorization'],'run_id':RUN,'reviewed_sha256':seal}
    monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',lambda *a:(_ for _ in ()).throw(ValueError('injected final rollback')))
    with pytest.raises(ValueError,match='injected final rollback'):
        s.apply_plan(plan,transaction_context=TransactionValidation(RUN,seal,pins),metadata=metadata)
    assert all(s.get_record_state(*k)==v for k,v in old.items())
    monkeypatch.setattr(s,'_assert_transaction_edges_and_reviews',guard);seal=review()
    result=s.apply_plan(plan,transaction_context=TransactionValidation(RUN,seal,pins),metadata=metadata)
    assert s.get_cvp_group(RUN)['checkpoint']=='applied'
    transitioned_dependency=dependency_context(s.get_cvp_group(dependency_run),s)
    assert transitioned_dependency['claim_id']==original_dependency['claim_id']==dependency_cid
    assert transitioned_dependency['original_group_claim_ids']==original_dependency['original_group_claim_ids']
    assert len(transitioned_dependency['subsequent_merge_transitions'])==1
    from backend.pipeline.cvp_source_impact_resolution import prepare as prepare_impacts
    for run,meta in source_runs.items():
        facts=prepare_impacts(s,s.get_cvp_group(run))
        assert facts['pending_record_keys']==meta['reviewed']['evidence']['pending_cvp_management_record_keys']
        assert facts['model_verdicts_changed'] is False and facts['prior_completed_resolution']
    with s.connect() as c:
        required=s._existing_cvp_context(c,{'claims':{i:{'claim_id':i} for i in m['original_claim_ids']},
            'canonical_viewpoints':{i:{'viewpoint_id':i} for i in views}},historical_pins_only=True)
    from backend.pipeline.cvp_sql_flow import pins as current_pins
    audit={'fixture':'SYNTHETIC SQL TRANSACTION DOMAIN NOT PROVIDER APPROVAL',
        'meta':{'model':'gemini-3.8-flash','registry_read_scope':{'kind':'sql_transaction_group',
            'complete_target_members':True,'claim_ids':m['original_claim_ids'],'viewpoint_ids':views,
            'read_pins':pin_rows(current_pins(required))}},'layers':{
                '1':{'counts':{'pass':1}},'2':{},
                '3':{'complete':True,'judged':len(m['original_claim_ids']),
                    'results':[{'claim_id':i,'verdict':'supported'} for i in m['original_claim_ids']]},
                '4':{'complete':True,'judged':len(views),'results':[{'viewpoint_id':i,'verdict':'same'} for i in views]}}}
    path=tmp_path/'disposable-audit.json';path.write_text(canonical_json(audit))
    kwargs={'audit':audit,'audit_binding':binding(path),'read_pins':current_pins(required)}
    original=s.get_cvp_group(RUN);late=s._assert_completion_source_generations
    for fault in ['missing_claim','missing_link','old_head_scope','rejected','stale']:
        bad=deepcopy(audit);scope=bad['meta']['registry_read_scope']
        if fault.startswith('missing_'):
            col='claims' if fault=='missing_claim' else 'viewpoint_claim_links'
            scope['read_pins']=[r for r in scope['read_pins'] if r['collection']!=col]
        elif fault=='old_head_scope':scope['viewpoint_ids']=m['prior_viewpoint_ids']
        elif fault=='rejected':bad['layers']['4']['results'][0]['verdict']='disputed'
        elif fault=='stale':scope['read_pins'][0]['pin'][0]+=1
        bp=tmp_path/(fault+'.json');bp.write_text(canonical_json(bad))
        from backend.api.canonical_repository.postgres_store import ChangeSetConflict
        with pytest.raises((ValueError,ChangeSetConflict)):s.complete_cvp_group(RUN,audit=bad,audit_binding=binding(bp),read_pins=current_pins(required))
        assert s.get_cvp_group(RUN)==original
    monkeypatch.setattr(s,'_assert_completion_source_generations',lambda *a:(_ for _ in ()).throw(RuntimeError('late completion rollback')))
    with pytest.raises(RuntimeError,match='late completion rollback'):s.complete_cvp_group(RUN,**kwargs)
    assert s.get_cvp_group(RUN)==original
    monkeypatch.setattr(s,'_assert_completion_source_generations',late)
    s.complete_cvp_group(RUN,**kwargs);completed=s.get_cvp_group(RUN)
    assert completed['checkpoint']=='audited' and completed['claim_ids']==m['original_claim_ids']
    s.complete_cvp_group(RUN,**kwargs);assert s.get_cvp_group(RUN)==completed
    assert s.apply_plan(plan,transaction_context=TransactionValidation(RUN,seal,pins),metadata=metadata)['status']=='already_applied'
    for k,v in old.items():
        if k not in {(op.collection,op.object_id) for op in plan.operations}:assert s.get_record_state(*k)==v
    with s.connect() as c,c.cursor() as q:
        assert f.current_viewpoint_ids(q,IDS)==[new]
    history=s.get_change_history(result['change_set_id'])
    assert len(history['operations'])==len(plan.operations)
    assert s.get_cvp_group(RUN)==completed
    # Old declarations resolve to the new full member population, without
    # rewriting old group metadata or recursively inheriting approvals.
    transitioned=s.read_cvp_context([],viewpoint_ids=IDS)
    assert ('canonical_viewpoints',new) in transitioned
    assert all(('claims',cid) in transitioned for cid in m['claim_ids'])


@pytest.mark.skipif(os.getenv('WKP357_MERGE_SAVED')!='1',reason='readonly actual complete wire preflight')
def test_actual_full_merge_stage_wire_roundtrip_and_budget(tmp_path):
    from backend.pipeline.source_reconciliation_runner import stage_adapter
    s,m,d=real_material(tmp_path);p={'response':simulated_proposal(m),'artifact_sha256':'DISPOSABLE-PROPOSAL'}
    r={'response':{'verdicts':[{'claim_id':i,'approved':False,'problems':['SIMULATED'],
        'reason':'SYNTHETIC CAPACITY FIXTURE NOT PROVIDER RESPONSE'} for i in f.required_verdict_ids(m)]},'artifact_sha256':'DISPOSABLE-REVIEW'}
    root=ROOT/'execution'/RUN/'actions'/'cvp-merge'/f.action_id(d)
    projected=f.proposal_packet(m)
    semantic=set(m['semantic_record_keys'])
    assert projected['records']==[r for r in m['records'] if r['collection']+'/'+r['object_id'] in semantic]
    assert projected['sources']==m['sources']
    assert projected['original_generation_context']==m['original_generation_context']
    for stage,packet in [('proposal',projected),('initial_review',f.review_packet(m,p)),
                         ('correction',f.correction_packet(m,p,r)),('final_review',f.review_packet(m,p))]:
        adapter=stage_adapter(f.adapter(stage),packet,root/(stage+'.raw.json'))
        proof=adapter.request_encoding_proof(packet);size=adapter.request_bytes(packet)
        assert proof['exact_roundtrip_verified'] and proof['restored_payload_sha256']==sha256_json(packet)
        print(stage,size,len(m['sources']),len(m['records']))
        assert size<=600000
