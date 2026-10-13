"""Actual unchanged input reuse, NOT selecting favorable model verdicts."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from backend.pipeline import viewpoint_relation_profile_reuse as m
from backend.pipeline import viewpoint_content_blocked_fallback as f
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
from backend.pipeline.viewpoint_audit_owner_resolution import binding
from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH,COMPONENT_PATH,RELATION_PATH,relation_rules
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def fixture(tmp_path,monkeypatch,*,old_verdict='disputed',new_verdict='same'):
    source=tmp_path/'source';source.write_text('complete own original')
    cache_root=tmp_path/'data/wang-knowledge-platform/staging/reports/library-audit/.judgements'
    monkeypatch.setattr('dotenv.dotenv_values',lambda p:{'DATA_BASE_DIR':str(tmp_path/'data')})
    pin={'collection':'viewpoint_claim_links','object_id':'L','pin':[1,'link-sha',False]}
    meta={'model':'gemini-3.8-flash','semantic_contract_version':m.COMPONENT_ROLES,
        'semantic_contract_file_sha256':hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
        'component_role_contract_file_sha256':hashlib.sha256(COMPONENT_PATH.read_bytes()).hexdigest(),
        'registry_read_scope':{'claim_ids':['C'],'read_pins':[pin]}}
    old_row={'viewpoint_id':'V','revision_id':'R','core_proposition':'proposition','linked_claims':1,
        'linked_connections':1,'member_claims':1,'verdict':old_verdict,'issue':'different_proposition','reason':'old immutable concern'}
    new_row={**old_row,'verdict':new_verdict,'issue':'scope_mismatch','reason':'new immutable concern'}
    prior={'meta':meta,'layers':{'3':{'results':[]},'4':{'complete':True,'judged':1,
        'disputed':int(old_verdict=='disputed'),'same':int(old_verdict=='same'),'results':[old_row]}}}
    current=deepcopy(prior);current['meta']['semantic_contract_version']=m.RELATION_ROLES
    current['meta']['relation_role_contract_file_sha256']=hashlib.sha256(RELATION_PATH.read_bytes()).hexdigest()
    current['layers']['4'].update(results=[new_row],disputed=int(new_verdict=='disputed'),same=int(new_verdict=='same'))
    old_path=tmp_path/'prior.json';new_path=tmp_path/'current.json'
    write_immutable(old_path,prior);write_immutable(new_path,current)
    prompt='original complete rules\ncomplete own original'
    full=prompt+'\n\n'+relation_rules()
    for text,row in ((prompt,old_row),(full,new_row)):
        write_immutable(cache_root/(hashlib.sha256(f'gemini-3.8-flash\n{text}'.encode()).hexdigest()+'.json'),
            {k:row[k] for k in ('verdict','issue','reason')})
    state={'pins':[deepcopy(pin)],'links':[{'link_type':'equivalent_component','review_status':'system_approved'}]}
    def capture(layer,identity,*,semantic_contract_version,sql_scope,with_pins=False):
        assert layer=='4' and identity=='V' and sql_scope['claim_ids']==[] and sql_scope['viewpoint_ids']==['V']
        values=(prompt if semantic_contract_version==m.COMPONENT_ROLES else full,{'schema':'unchanged'},[binding(source)])
        return (*values,deepcopy(state['pins']),deepcopy(state['links'])) if with_pins else values
    monkeypatch.setattr(f,'capture',capture)
    saved={'run_id':'run','campaign_sha256':'campaign','completion':{'audit':binding(old_path)}}
    store=SimpleNamespace(get_cvp_group=lambda r:deepcopy(saved))
    return store,saved,new_path,prior,current,state,source


@pytest.mark.parametrize('old_verdict,new_verdict',[('disputed','same'),('same','disputed'),('disputed','disputed')])
def test_selection_is_version_and_full_input_not_favorable_verdict(old_verdict,new_verdict,tmp_path,monkeypatch):
    s,row,path,prior,current,state,source=fixture(tmp_path,monkeypatch,old_verdict=old_verdict,new_verdict=new_verdict)
    before=path.read_bytes();output=m.prepare(s,'run',path,tmp_path/'assessment');derived=read_artifact(output)
    assert derived['layers']['4']['results']==prior['layers']['4']['results']
    assert path.read_bytes()==before and current['layers']['4']['results'][0]['verdict']==new_verdict
    assert m.profile(derived,'V','default')==m.COMPONENT_ROLES
    assert m.profile(derived,'other','default')=='default'
    assert m.validate(derived)
    m.validate_sql_parent(derived,row,binding(output))
    m.validate_sql_parent(derived,{**row,'completion':{'audit':binding(output)}},binding(output))
    for fault in ('run','campaign','completion'):
        bad=deepcopy(row)
        if fault=='completion':bad['completion']={'audit':{'path':'foreign'}}
        else:bad['run_id' if fault=='run' else 'campaign_sha256']='foreign'
        with pytest.raises(ValueError):m.validate_sql_parent(derived,bad,binding(output))


@pytest.mark.parametrize('fault',['new_pin','changed_pin','held','extra_role','old_cvr','old_cache','new_cache',
    'contract','claim_population','semantic_delta','selective','source','raw_body'])
def test_no_generation_source_permission_or_selective_result_waiver(fault,tmp_path,monkeypatch):
    s,row,path,prior,current,state,source=fixture(tmp_path,monkeypatch)
    if fault in {'selective','source','raw_body'}:
        output=m.prepare(s,'run',path,tmp_path/'assessment');derived=read_artifact(output)
        if fault=='selective':derived['layers']['4']['results'][0]['verdict']='same'
        elif fault=='source':source.write_text('changed physical source')
        else:path.write_text('{}')
        with pytest.raises((ValueError,KeyError)):m.validate(derived)
        return
    if fault=='new_pin':state['pins'].append({'collection':'claims','object_id':'new','pin':[1,'x',False]})
    if fault=='changed_pin':state['pins'][0]['pin'][0]=2
    if fault=='held':state['links'][0]['review_status']='human_hold'
    if fault=='extra_role':state['links'][0]['link_type']='qualifies'
    if fault=='old_cvr':prior['layers']['4']['results'][0]['revision_id']='old'
    if fault in {'old_cache','new_cache'}:
        for p in (tmp_path/'data').rglob('*.json'):
            value=read_artifact(p)
            if value['reason'].startswith('old' if fault=='old_cache' else 'new'):p.write_text('{"reason":"drift"}')
    if fault=='contract':current['meta']['component_role_contract_file_sha256']='changed'
    if fault=='claim_population':current['meta']['registry_read_scope']['claim_ids']=['foreign']
    if fault=='semantic_delta':
        original=f.capture
        def changed(*a,**k):
            v=original(*a,**k)
            return ('changed semantic body',*v[1:]) if k['semantic_contract_version']==m.RELATION_ROLES else v
        monkeypatch.setattr(f,'capture',changed)
    if fault in {'new_cache','contract','claim_population','semantic_delta'}:
        with pytest.raises(ValueError):m._entries(current,prior)
    else:assert m._entries(current,prior)==[]


def test_scoped_future_profile_keeps_original_equivalent_support_prompt(monkeypatch):
    from backend.tests.test_viewpoint_component_roles import audit as reader,graph
    links,claims,steps,fragments=graph()
    collections={'canonical_viewpoints':{'V':{'current_revision_id':'R'}},
        'viewpoint_revisions':{'R':{'core_proposition':'same proposition'}},'claims':claims,
        'source_fragments':fragments,'viewpoint_claim_links':{l['viewpoint_claim_link_id']:l for l in links}}
    store=SimpleNamespace(collection=lambda col,**kw:[{'object_id':k,'payload':v} for k,v in collections[col].items()])
    monkeypatch.setattr(reader,'_whole_sources_for_claim',lambda *a:(['complete own original'],False))
    class Model:
        preserve_full_context=True
        def judge(self,prompt,schema):self.prompt=prompt;return {'verdict':'same','issue':'none','reason':'SYNTHETIC transport'}
    def request(version):
        model=Model();reader.audit_viewpoints(store,object(),model,None,random.Random(0),workers=1,
            semantic_contract_version=version,only={'V'});return model.prompt
    assert request(reader.SCOPED_RELATION_SEMANTICS)==request(reader.COMPONENT_SEMANTICS)
    links[1]['link_type']='qualifies'
    assert request(reader.SCOPED_RELATION_SEMANTICS)==request(reader.RELATION_SEMANTICS)
    assert relation_rules() in request(reader.SCOPED_RELATION_SEMANTICS)


@pytest.mark.skipif(os.getenv('WKP357_RELATION_PROFILE_SAVED')!='1',reason='actual full current saved SQL/source/provider cache readonly')
def test_actual_completed_generation_capture_and_full_resolution_no_model_or_registry_write(tmp_path):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_reused_source_resolution import compose,verify_composed
    from backend.pipeline.viewpoint_adjudication_history import target_identity
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    load_dotenv('.env');s=PostgresKnowledgeStore()
    run='CVPG-5baf4616c077c3c118f2f36dc6254f84351ecca3d2c98d7114bf16e13e7def8f'
    base=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    current=base/'execution'/run/'audit-a848fc076654f2736000/audit.json'
    saved=s.get_cvp_group(run);before=current.read_bytes()
    output=m.prepare(s,run,current,tmp_path/'assessment');audit=read_artifact(output)
    assert len(read_artifact(tmp_path/'assessment/manifest.json')['entries'])==8
    assert audit['layers']['3']['judged']==5 and audit['layers']['4']['judged']==9
    assert audit['layers']['4']['disputed']==1
    m.validate_sql_parent(audit,saved,binding(output))
    assert f.verify_saved_full_audit(output)
    packet=build_packet(output,s,True)
    prior=s.adjudication_roots([target_identity(i) for i in packet['items']])
    result=compose(output,prior,tmp_path/'resolution/result.json',s)
    assert verify_composed(audit,result)=={'3':set(),'4':{'CV-4d2ad88ccb38bc256d20'}}
    assert current.read_bytes()==before and s.get_cvp_group(run)==saved


@pytest.mark.skipif(os.getenv('WKP357_RELATION_PROFILE_SQL')!='1',reason='actual saved scoped disposableSQL completion/late rollback/cache')
def test_actual_scoped_disposable_sql_full_completion_rollback_and_cache(postgres_store,tmp_path,monkeypatch):
    from dotenv import load_dotenv
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import decoded_pins
    from backend.pipeline.claim_dependency_handoff import subject
    from backend.pipeline.viewpoint_reused_source_resolution import compose
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    from backend.pipeline.viewpoint_adjudication_history import target_identity
    load_dotenv('.env');production=PostgresKnowledgeStore();target=postgres_store
    run='CVPG-5baf4616c077c3c118f2f36dc6254f84351ecca3d2c98d7114bf16e13e7def8f'
    before=production.get_cvp_group(run)
    parent=next(r['run_id'] for r in production.cvp_campaign_progress(before['campaign_sha256']) if r['sequence']==122)
    base=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    current=base/'execution'/run/'audit-a848fc076654f2736000/audit.json'
    audit=read_artifact(current)
    source=subject(before)[2]
    keys={(p['collection'],p['object_id']) for p in [*audit['meta']['registry_read_scope']['read_pins'],
        *before['reviewed']['evidence']['read_pins'],*source['sql_scope']['read_pins']]}
    ids=sorted({i for c,i in keys});runs=[run,parent]
    with production.connect() as src,target.connect() as dst:
        from backend.pipeline.cvp_merge_management import SCHEMA as MERGE
        immediate_merges=[r[0] for r in src.execute('''SELECT DISTINCT c.change_set_id FROM wang_knowledge.objects o
            JOIN wang_knowledge.object_versions v ON (v.collection,v.object_id,v.revision)=(o.collection,o.object_id,o.revision)
            JOIN wang_knowledge.change_sets c ON c.change_set_id=v.change_set_id
            WHERE o.object_id=ANY(%s) AND c.source_kind=%s''',(ids,MERGE)).fetchall()]
        # Its validator checks the COMPLETE actual merge write set, not just
        # source/visible member pins. Include unchanged retired old heads too.
        ids=sorted(set(ids)|{r[0] for r in src.execute('SELECT object_id FROM wang_knowledge.change_operations WHERE change_set_id=ANY(%s)',(immediate_merges,)).fetchall()})
        changes=sorted({r[0] for r in src.execute('SELECT DISTINCT change_set_id FROM wang_knowledge.object_versions WHERE object_id=ANY(%s)',(ids,)).fetchall()})
        # Current owned links include an immediately committed merge generation.
        # Copy its actual parent/review archive, never waive that authority.
        runs=sorted(set(runs)|{r[0] for r in src.execute("SELECT run_id FROM wang_knowledge.pipeline_runs WHERE stage='cvp_group' AND metadata->'change_set_ids' ?| %s",(changes,)).fetchall()})
        selections=[('change_sets','change_set_id=ANY(%s)',(changes,)),
            ('objects','object_id=ANY(%s)',(ids,)),('object_versions','object_id=ANY(%s)',(ids,)),
            ('change_operations','change_set_id=ANY(%s)',(changes,)),
            ('review_events','object_id=ANY(%s)',(ids,)),('edges','edge_id=ANY(%s)',(ids,)),
            ('pipeline_runs',"run_id=ANY(%s) OR metadata->>'parent_run_id'=ANY(%s) OR metadata->>'owner_run_id'=ANY(%s) OR (stage='cvp_campaign' AND metadata->>'campaign_sha256'=%s)",
                (runs,runs,runs,before['campaign_sha256']))]
        for table,where,params in selections:
            rows=src.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),params)
            columns=[c.name for c in rows.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(Identifier(table),
                SQL(',').join(map(Identifier,columns)),SQL(',').join(Placeholder()*len(columns)))
            for values in rows.fetchall():
                dst.execute(insert,tuple(Jsonb(v) if isinstance(v,(dict,list)) and name not in
                    {'source_ids','output_paths'} else v for name,v in zip(columns,values)))
    output=m.prepare(target,run,current,tmp_path/'assessment');derived=read_artifact(output)
    packet=build_packet(output,target,True)
    prior=target.adjudication_roots([target_identity(i) for i in packet['items']])
    resolution=tmp_path/'resolution/result.json';compose(output,prior,resolution,target)
    original=target.get_cvp_group(run)
    kwargs={'audit':derived,'audit_binding':binding(output),
        'read_pins':decoded_pins(derived['meta']['registry_read_scope']['read_pins']),
        'owner_resolution_path':resolution}
    # Reused disputed remains disputed: the generation receipt is not approval.
    with pytest.raises(ValueError):target.complete_cvp_group(run,**{**kwargs,'owner_resolution_path':None})
    assert target.get_cvp_group(run)==original
    late=target._assert_completion_source_generations
    monkeypatch.setattr(target,'_assert_completion_source_generations',lambda *a:(_ for _ in ()).throw(RuntimeError('late completion rollback')))
    with pytest.raises(RuntimeError,match='late completion rollback'):target.complete_cvp_group(run,**kwargs)
    assert target.get_cvp_group(run)==original
    monkeypatch.setattr(target,'_assert_completion_source_generations',late)
    target.complete_cvp_group(run,**kwargs);accepted=target.get_cvp_group(run)
    assert accepted['checkpoint']=='audited' and accepted['claim_ids']==before['claim_ids']
    target.complete_cvp_group(run,**kwargs);assert target.get_cvp_group(run)==accepted
    assert production.get_cvp_group(run)==before
