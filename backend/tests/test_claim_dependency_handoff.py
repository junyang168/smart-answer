from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pytest
from backend.pipeline.claim_dependency_handoff import joint_links, context, subject


@pytest.mark.parametrize('fault', [None,'missing','extra','duplicate','hold','claim_pin','type','target','state','cvr','occurrence','relations','claims'])
def test_joint_claim_impact_requires_all_active_links(fault):
    old={'viewpoint_claim_link_id':'L','claim_id':'C','viewpoint_id':'V','effective_state':'active',
         'link_type':'equivalent_component','validated_against_viewpoint_revision_id':'R',
         'occurrence_refs':['O'],'supporting_relation_ids':[]}
    new={**old,'pinned_claim_revision':3,'review_status':'system_approved'}
    package={'claims':[{'claim_id':'C','revision':3}], 'viewpoint_claim_links':[new]}
    material={'before_records':[{'collection':'viewpoint_claim_links','object_id':'L','payload':old}]}
    if fault=='missing':package['viewpoint_claim_links']=[]
    if fault=='extra':package['viewpoint_claim_links'].append({**new,'viewpoint_claim_link_id':'foreign'})
    if fault=='duplicate':package['viewpoint_claim_links'].append(deepcopy(new))
    fields={'hold':('review_status','human_hold'),'claim_pin':('pinned_claim_revision',2),
            'type':('link_type','supports'),'target':('viewpoint_id','foreign'),'state':('effective_state','retired'),
            'cvr':('validated_against_viewpoint_revision_id','old'),'occurrence':('occurrence_refs',[]),
            'relations':('supporting_relation_ids',['foreign'])}
    if fault in fields:new[fields[fault][0]]=fields[fault][1]
    if fault=='claims':package['claims'].append({'claim_id':'foreign','revision':3})
    if fault is None:assert joint_links(material,package,'C')=={'L':new}
    else:
        with pytest.raises(ValueError):joint_links(material,package,'C')


@pytest.mark.skipif(os.getenv('WKP357_CLAIM_IMPACT_SAVED')!='1',reason='explicit read-only saved production scope')
@pytest.mark.parametrize('fault',[None,'group_membership_not_authority','pending','action','material_sha','run','plan','commit','link_type','view_population',
                                 'sql_issue','sql_approval','sql_history','sql_write_set','sql_active_population'])
def test_actual_saved_claim_impact_no_calls_writes_or_file_drift(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    load_dotenv('.env');store=PostgresKnowledgeStore()
    value=store.get_cvp_group('CVPG-5baf4616c077c3c118f2f36dc6254f84351ecca3d2c98d7114bf16e13e7def8f')
    evidence=value['reviewed']['evidence'];app=evidence['package']['payload']['source_repair_application']
    root=Path(app['run_root']);files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    original=deepcopy(value);cid='DK-78f4b0b8eb95-P03-CL006'
    if fault=='group_membership_not_authority':value['claim_ids'].append(cid)
    if fault=='pending':evidence['pending_cvp_claim_ids'].append('foreign')
    if fault=='action':evidence['action']['action_id']='foreign'
    if fault=='material_sha':evidence['action']['materials_sha256']='f'*64
    if fault=='run':value['run_id']='foreign'
    if fault=='plan':value['reviewed']['plan']['operations'].pop()
    if fault=='commit':value['reviewed']['plan']['change_set_id']='foreign'
    if fault=='link_type':evidence['package']['payload']['viewpoint_claim_links'][0]['link_type']='supports'
    if fault=='view_population':evidence['viewpoint_ids']=[]
    with store.connect() as conn,conn.cursor() as cursor:
        conn.execute('SET TRANSACTION READ ONLY')
        class ReadOnly:
            def execute(self,q,args=None):
                cursor.execute(q.removesuffix(' FOR UPDATE'),args)
                one=fault in {'sql_issue','sql_approval','sql_history'} and (
                    (fault=='sql_issue' and args==('CVPI-' + __import__('backend.pipeline.claim_issue_management',fromlist=['Issue']).Issue.model_validate(evidence['action']['materials']['claim_issue_context']['issues'][0]).issue_id,))
                    or (fault=='sql_approval' and q.startswith('SELECT status,fingerprint'))
                    or (fault=='sql_history' and q.startswith('SELECT payload,content_sha256')))
                many=(fault=='sql_write_set' and q.startswith('SELECT operation,collection')) or (
                    fault=='sql_active_population' and q.startswith('SELECT object_id FROM'))
                if one or many:
                    rows=cursor.fetchall()
                    if fault=='sql_issue':rows=[]
                    if fault=='sql_approval':
                        row=list(rows[0]);row[3]={**row[3],'approval':{}};rows=[tuple(row)]
                    if fault=='sql_history':
                        row=list(rows[0]);row[0]={**row[0],'unexpected':'changed'};rows=[tuple(row)]
                    if many:rows=[]
                    return type('Result',(),{'fetchone':lambda self: rows[0] if rows else None,
                                            'fetchall':lambda self: rows})()
                return cursor
            def __getattr__(self,name):return getattr(cursor,name)
        if fault=='pending':assert subject(value) is None
        elif fault=='group_membership_not_authority':
            # Pure subject detection is independent of group membership. SQL
            # completion still uses the frozen actual group membership.
            assert subject(value)[1]==cid
        elif fault is None:
            result=context(value,cursor=ReadOnly());assert result['claim_id']==cid
            assert len(result['link_ids'])==2 and result['campaign_membership_changes']==0
            assert not result['group_complete'] and not result['content_write_authority']
        else:
            with pytest.raises(ValueError):context(value,cursor=ReadOnly())
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    assert store.get_cvp_group(original['run_id'])==original
