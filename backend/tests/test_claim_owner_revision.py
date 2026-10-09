from copy import deepcopy
from types import SimpleNamespace as NS
import json,os
from pathlib import Path
import pytest

from backend.pipeline import claim_owner_revision as m,source_reconciliation as c
from backend.api.canonical_repository.postgres_store import sha256_json,record_content_sha
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_source_reconciliation import scenario
from backend.tests.test_claim_issue_management import issue_setup, issue_for
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


@pytest.fixture
def direction_case(tmp_path):
    def save(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return binding(p)
    claim={'claim_id':'C','statement':'原摘要','revision':3}
    state={'payload':claim,'revision':3,'content_sha256':record_content_sha(claim),'retired':False}
    value={'run_id':'G','campaign_sha256':'A','claim_ids':['original'],
           'reviewed':{'evidence':{'read_pins':[{'collection':'claims','object_id':'C','pin':[3,state['content_sha256'],False]}]}}}
    policy={'schema_version':'wang_claim_issue_management_v1','authorization_id':'owner','authorization_quote':'补全原文',
            'human_content_approval':False,'claim_fields':['statement'],'evidence_fields':[],
            'link_fields':['pinned_claim_revision','component_locator','evidence_bindings'],'cvp_permissions':[]}
    d={'schema_version':m.VERSION,'run_id':'G','campaign_sha256':'A','claim_id':'C',
       'before_statement':'原摘要','before_claim_pin':[3,state['content_sha256'],False],
       'decision':'propose_source_supported_claim_completion','authorization_quote':'摘要漏了，加上',
       'instruction':'按自身全文补全原摘要','human_content_approval':False,'maximum_corrections':1,
       'source_changes':0,'cvp_core_changes':0,'existing_dependency_only':True,'campaign_membership_changes':0,
       'source':save('source.json',{}),'original_handoff':save('handoff.json',{}),'policy':save('policy.json',policy)}
    return d,value,state


def seal(d):d['artifact_sha256']=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})


def test_sql_dependency_intake_exact_owner_once_without_changing_membership(postgres_store,issue_setup,tmp_path):
    from backend.api.canonical_repository.postgres_store import canonical_json
    manifest,states,report,_=issue_setup
    run=source_run(postgres_store,(manifest,states,{}))
    state=postgres_store.get_record_state('claims','CL-test')
    value=postgres_store.get_cvp_group(run)
    # Synthetic original group excludes this existing current dependency.
    metadata={k:v for k,v in value.items() if k!='run_id'}
    metadata['claim_ids']=['original']
    metadata['reviewed']={'evidence':{'read_pins':[{'collection':'claims','object_id':'CL-test',
        'pin':[state['revision'],state['content_sha256'],False]}]}}
    with postgres_store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
                     (canonical_json(metadata),run))
    policy={'schema_version':'wang_claim_issue_management_v1','authorization_id':'owner','authorization_quote':'补全',
            'human_content_approval':False,'claim_fields':['statement'],'evidence_fields':[],
            'link_fields':['pinned_claim_revision','component_locator','evidence_bindings'],'cvp_permissions':[]}
    p=tmp_path/'policy.json';p.write_text(json.dumps(policy))
    d={'schema_version':m.VERSION,'run_id':run,'campaign_sha256':value['campaign_sha256'],'claim_id':'CL-test',
       'before_statement':state['payload']['statement'],'before_claim_pin':[state['revision'],state['content_sha256'],False],
       'decision':'propose_source_supported_claim_completion','authorization_quote':'摘要不完整，补全','instruction':'按原文补全',
       'human_content_approval':False,'maximum_corrections':1,'source_changes':0,'cvp_core_changes':0,
       'existing_dependency_only':True,'campaign_membership_changes':0,'source':binding(manifest.before_source.path),
       'original_handoff':report,'policy':binding(p)}
    seal(d);path=tmp_path/'direction.json';path.write_text(json.dumps(d))
    issue=issue_for(states,binding(path)).model_copy(update={'origin':'owner'})
    before=postgres_store.get_record_state('claims','CL-test')
    row=postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    postgres_store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id='same-original-action')
    replay=postgres_store.register_claim_issue(run,issue.model_copy(update={'correction_used':True}).model_dump(mode='json'))
    assert row['issue_id']==replay['issue_id'] and replay['correction_used']
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    with pytest.raises(ChangeSetConflict):
        postgres_store.bind_claim_issue_action([issue.issue_id],run_id=run,action_id='reset-action')
    with pytest.raises(ValueError,match='foreign finder'):
        postgres_store.register_claim_issue(run,issue.model_copy(update={'origin':'gemini_audit'}).model_dump(mode='json'))
    assert postgres_store.get_record_state('claims','CL-test')==before
    assert postgres_store.get_cvp_group(run)['claim_ids']==['original']
    assert postgres_store.get_cvp_group(run)['change_set_ids']==[]


def test_external_dependency_not_added_to_campaign(direction_case):
    d,v,s=direction_case;before=deepcopy(v);seal(d);m.validate_direction(d,v,s);assert v==before


@pytest.mark.parametrize('fault',['campaign','group','claim','pin','statement','retired','source','approved','budget','membership','not-dependency','foreign-pin','instruction'])
def test_exact_owner_correction_does_not_waive_current_proof_or_membership(direction_case,fault):
    d,v,s=direction_case
    if fault=='campaign':d['campaign_sha256']='foreign'
    elif fault=='group':d['run_id']='foreign'
    elif fault=='claim':d['claim_id']='foreign'
    elif fault=='pin':d['before_claim_pin'][0]=2
    elif fault=='statement':d['before_statement']='another'
    elif fault=='retired':s['retired']=True
    elif fault=='source':d['source']['file_sha256']='0'*64
    elif fault=='approved':d['human_content_approval']=True
    elif fault=='budget':d['maximum_corrections']=2
    elif fault=='membership':d['campaign_membership_changes']=1
    elif fault=='not-dependency':d['existing_dependency_only']=False
    elif fault=='foreign-pin':v['reviewed']['evidence']['read_pins']=[]
    else:d['instruction']=''
    seal(d)
    with pytest.raises(ValueError):m.validate_direction(d,v,s)


def test_joint_claim_revision_requires_all_active_links_and_no_type_or_target_change(tmp_path):
    d={'claim_id':'C'};p=tmp_path/'direction.json';p.write_text(json.dumps(d))
    claim={'claim_id':'C','statement':'不完整','revision':3}
    link={'claim_id':'C','viewpoint_id':'V','link_type':'equivalent_component','effective_state':'active',
          'pinned_claim_revision':3,'revision':2,'viewpoint_claim_link_id':'L'}
    rows=c.rows({'claims/C':claim,'viewpoint_claim_links/L':link})
    f={'owner_claim_completion':binding(p),'before_records':rows}
    after={'claims/C':{**claim,'statement':'完整','revision':4},
           'viewpoint_claim_links/L':{**link,'pinned_claim_revision':4,'revision':3}}
    def proposal():return NS(records=c.rows(after),claim_assessments=[NS(cause='claim_statement')])
    assert m.validate_joint(f,proposal(),{})
    for fault in ('omit-link','type','target','revive','claim-retain','revision','cause','extra'):
        bad=deepcopy(after);pr=proposal()
        if fault=='omit-link':bad.pop('viewpoint_claim_links/L')
        elif fault=='type':bad['viewpoint_claim_links/L']['link_type']='supports'
        elif fault=='target':bad['viewpoint_claim_links/L']['viewpoint_id']='other'
        elif fault=='revive':bad['viewpoint_claim_links/L']['effective_state']='retired'
        elif fault=='claim-retain':bad['claims/C']['statement']='不完整'
        elif fault=='revision':bad['viewpoint_claim_links/L']['pinned_claim_revision']=3
        elif fault=='cause':pr.claim_assessments=[NS(cause='cvp_link')]
        else:bad['evidence_steps/E']={'evidence_id':'E'}
        pr.records=c.rows(bad)
        with pytest.raises(ValueError):m.validate_joint(f,pr,{})


@pytest.mark.parametrize('fault',[None,'gap','sha','before','foreign','create'])
def test_two_reviewed_generations_stitch_without_recursive_history_or_scope_reset(fault):
    old={'operation':'update','collection':'viewpoint_claim_links','object_id':'L','before_revision':1,'before_sha256':'old',
         'after_revision':2,'after_sha256':record_content_sha({'revision':2}),'after':{'revision':2}}
    op={'operation':'update','collection':'viewpoint_claim_links','object_id':'L','before_revision':2,
        'before_sha256':old['after_sha256'],'before':old['after'],'after_revision':3,
        'after_sha256':record_content_sha({'revision':3}),'after':{'revision':3}}
    if fault=='gap':op['before_revision']=1
    elif fault=='sha':op['after_sha256']='bad'
    elif fault=='before':op['before']={'revision':99}
    elif fault=='foreign':op['object_id']='foreign'
    elif fault=='create':op['operation']='create'
    if fault:
        with pytest.raises(ValueError):m.stitch({'viewpoint_claim_links/L':old},[op],{'viewpoint_claim_links/L'})
    else:
        result=m.stitch({'viewpoint_claim_links/L':old},[op],{'viewpoint_claim_links/L'})
        assert result['viewpoint_claim_links/L']['before_revision']==1
        assert result['viewpoint_claim_links/L']['after_revision']==3


@pytest.mark.skipif(os.environ.get('WKP_OWNER_CLAIM_REAL_PREFLIGHT')!='1',reason='explicit read-only actual saved acceptance')
def test_actual_current_dependency_complete_packet_no_calls_or_registry_write(tmp_path):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.claim_issue_management import intake,make_issue,bound_fragments,manifest_for_issues
    from backend.pipeline.source_reconciliation_runner import build_proposal_packet
    from backend.pipeline.cvp_sql_actions import source_action_id
    load_dotenv('.env');s=PostgresKnowledgeStore()
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    d=__import__('backend.pipeline.viewpoint_resolution_runtime',fromlist=['read_artifact']).read_artifact(root/'owner-g094-claim-statement-completion.json')
    v=s.get_cvp_group(d['run_id']);cid=d['claim_id'];rows=s.read_cvp_context([cid]);claim=rows['claims',cid]['payload']
    evidence=[rows['evidence_steps',eid]['payload'] for eid in sorted(claim['evidence_step_ids'])]
    fragments=bound_fragments(evidence,lambda fid:rows['source_fragments',fid]['payload'])
    sid=fragments[0]['source_id'];source=rows['source_documents',sid]['payload']
    issue=make_issue(claim,evidence,source,spans=[(0,len(claim['statement']),claim['statement'])],origin='owner',
                     report=binding(root/'owner-g094-claim-statement-completion.json'),reason=d['instruction'],fragments=fragments)
    manifest=manifest_for_issues(s,d['run_id'],[issue],d['policy'],tmp_path/'scope')
    current=s.read_affected_context([('claims',cid)]+[(p.collection,p.object_id) for p in manifest.permissions])
    states={c.key(col,oid):r['payload'] for (col,oid),r in current.items() if not r['retired']}
    frozen=c.freeze(manifest,states);m.attach_material(s,v,frozen,tmp_path/'scope')
    packet=build_proposal_packet(frozen)
    assert cid not in manifest.campaign_claim_ids and len(manifest.campaign_claim_ids)==973 and len(manifest.groups)==156
    assert packet['claim_issue_context']['claim_ids']==[cid] and len(frozen['after'][0]['paragraphs'])>=17
    assert all(p.collection in {'claims','viewpoint_claim_links'} for p in manifest.permissions)
    assert s.get_cvp_group(d['run_id'])['change_set_ids']==v['change_set_ids']
    assert s.get_claim_issue(issue.issue_id) is None
    print(json.dumps({'issue':issue.issue_id,'action':source_action_id(manifest),'records':len(states),
                      'paragraphs':len(frozen['after'][0]['paragraphs']),'packet_bytes':len(json.dumps(packet,ensure_ascii=False).encode())}))
