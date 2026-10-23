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


@pytest.mark.parametrize('outcome',['supported','unknown','semantic'])
def test_dependency_audit_reuses_existing_safety_fallback_without_gemini_retry(tmp_path,monkeypatch,outcome):
    from backend.pipeline import claim_dependency_handoff as handoff,claim_first_management as first
    from backend.pipeline import viewpoint_content_blocked_fallback as fallback
    from backend.pipeline import cvp_sql_commit as flow
    root=tmp_path/'dependency-claim-audit';root.mkdir()
    state={'revision':3,'content_sha256':'a'*64,'retired':False}
    proof={'path':'existing-approved-material','sha256':'b'*64}
    value={'run_id':'G','claim_ids':['original'],'reviewed':{'plan':{'change_set_id':'KCS'},
        'evidence':{'package':{'payload':{'source_repair_application':{'run_root':str(tmp_path)}}}}}}
    intent={'schema_version':m.VERSION,'run_id':'G','claim_ids':['C'],'original_group_claim_ids':['original'],
        'direction':proof,'pin':[3,'a'*64,False],'change_set_id':'KCS','model':'gemini-3.8-flash',
        'workers':2,'layers':['1','2','3'],'automatic_retry':False}
    m.write_immutable(root/'audit-intent.json',intent)
    m.write_immutable(root/'audit.json',{'original':'blocked'})
    assessment=root/'assessment.json'
    m.write_immutable(assessment,{'meta':{fallback.KEY:proof},'result':outcome})
    monkeypatch.setattr(handoff,'subject',lambda v:None)
    monkeypatch.setattr(m,'dependency_direction',lambda v:(proof,'C'))
    monkeypatch.setattr(flow,'check_binding',lambda p:None)
    monkeypatch.setattr('subprocess.run',lambda *a,**kw:pytest.fail('must not rerun Gemini'))
    calls=[]
    def apply(path,output):
        calls.append((path,output))
        if outcome=='unknown':raise ValueError('fallback already attempted; no automatic retry')
        return assessment
    monkeypatch.setattr(fallback,'apply',apply)
    monkeypatch.setattr(fallback,'verify_saved_full_audit',lambda p,**kw:[proof])
    def validate(audit,ids):
        assert ids==['C']
        if outcome=='semantic':raise ValueError('actual unresolved')
    monkeypatch.setattr(first,'validate_claim_audit_report',validate)
    store=NS(get_cvp_group=lambda run:value,get_record_state=lambda *args:state)
    if outcome!='supported':
        with pytest.raises(ValueError):m.finish_dependency_audit(store,'G')
        assert not (root/'completion.json').exists()
    else:
        m.finish_dependency_audit(store,'G')
        receipt=m.read_artifact(root/'completion.json')
        assert receipt['revisit_required'] is True and receipt['independent_complete'] is False
        assert receipt['campaign_membership_changes']==0
        m.validate_dependency_completion(value)
        receipt.pop('independent_complete');monkeypatch.setattr(m,'read_artifact',lambda p: receipt if Path(p).name=='completion.json' else __import__('backend.pipeline.viewpoint_resolution_runtime',fromlist=['read_artifact']).read_artifact(p))
        with pytest.raises(ValueError,match='nonindependent revisit'):m.validate_dependency_completion(value)
    assert calls==[(root/'audit.json',root/'content-blocked-fallback')]
    assert m.read_artifact(root/'audit.json')=={'original':'blocked'}


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


def test_partial_review_never_approves_incomplete_connections(direction_case,tmp_path):
    d,v,s=direction_case;seal(d)
    path=tmp_path/'direction.json';path.write_text(json.dumps(d))
    link={'claim_id':'C','viewpoint_id':'V','viewpoint_claim_link_id':'L','revision':2}
    frozen={'owner_claim_completion':binding(path),'before_records':c.rows({'claims/C':s['payload'],'viewpoint_claim_links/L':link}),
        'sql_scope':{'read_pins':v['reviewed']['evidence']['read_pins']+[{'collection':'viewpoint_claim_links',
            'object_id':'L','pin':[2,record_content_sha(link),False]}]},
        'manifest':{'permissions':[{'collection':'claims','object_id':'C','fields':['statement']},
            {'collection':'viewpoint_claim_links','object_id':'L','fields':['pinned_claim_revision','component_locator','evidence_bindings']}]}}
    proposal=NS(records=c.rows({'claims/C':{**s['payload'],'revision':4,'statement':'补全'}}),
        dispositions=[NS(record_key='viewpoint_claim_links/L',action='unresolved')])
    result=m.partial_review(frozen,proposal)
    assert result['write_authority'] is False and result['pending_link_keys']==['viewpoint_claim_links/L']
    from backend.pipeline.source_reconciliation import validate_review
    packet={'unresolved_proposal_review':result,'proposal_sha256':'a'*64,
        'proposal':{'dispositions':[{'record_key':'viewpoint_claim_links/L','action':'unresolved'}]}}
    response={'proposal_sha256':'a'*64,'verdicts':[{'record_key':'viewpoint_claim_links/L',
        'approved':True,'reason':'not an actual final approval','problems':[]}]}
    assert validate_review(packet,response) is False
    for fault in ('foreign','pin','changed-pending','historical-missing'):
        bad=deepcopy(frozen);p=deepcopy(proposal)
        if fault=='foreign':p.dispositions[0].record_key='viewpoint_claim_links/foreign'
        elif fault=='pin':bad['sql_scope']['read_pins'][1]['pin'][0]=1
        elif fault=='changed-pending':p.records+=c.rows({'viewpoint_claim_links/L':{**link,'revision':3}})
        else:p.dispositions+=[NS(record_key='canonical_viewpoints/V',action='unresolved')]
        with pytest.raises(ValueError):m.partial_review(bad,p)


@pytest.mark.parametrize('fault',[None,'records','missing-claim','foreign-claim','pin','retired','hold','cause','issue','owner'])
def test_empty_owner_draft_is_diagnostic_not_a_postimage(direction_case,tmp_path,fault):
    d,v,s=direction_case;seal(d)
    path=tmp_path/'direction.json';path.write_text(json.dumps(d))
    link={'viewpoint_claim_link_id':'L','claim_id':'C','revision':2}
    from backend.pipeline.claim_issue_management import Issue
    issue=Issue(claim_id='C',claim_revision=3,claim_sha256='a'*64,source_id='S',source_file_sha256='b'*64,
        evidence_sha256='c'*64,spans=[(0,1,'原')],origin='owner',report=d['source'],reason='complete')
    f={'owner_claim_completion':binding(path),'before_records':c.rows({'claims/C':s['payload'],'viewpoint_claim_links/L':link}),
       'sql_scope':{'read_pins':deepcopy(v['reviewed']['evidence']['read_pins'])+[
           {'collection':'viewpoint_claim_links','object_id':'L','pin':[2,record_content_sha(link),False]}]},
       'claim_issue_context':{'issues':[issue.model_dump(mode='json')]}}
    p=NS(records=[],claim_assessments=[NS(issue_id=issue.issue_id,cause='claim_statement')],
        dispositions=[NS(record_key='claims/C',action='unresolved'),NS(record_key='viewpoint_claim_links/L',action='unresolved')])
    if fault=='records':p.records=c.rows({'claims/C':{**s['payload'],'revision':4,'statement':'new'}})
    elif fault=='missing-claim':p.dispositions=p.dispositions[1:]
    elif fault=='foreign-claim':p.dispositions.append(NS(record_key='claims/foreign',action='unresolved'))
    elif fault=='pin':f['sql_scope']['read_pins'][1]['pin'][0]=1
    elif fault=='retired':f['sql_scope']['read_pins'][1]['pin'][2]=True
    elif fault=='hold':f['before_records'][1]['payload']['review_status']='human_review_required'
    elif fault=='cause':p.claim_assessments[0].cause='source_unresolved'
    elif fault=='issue':p.claim_assessments[0].issue_id='foreign'
    elif fault=='owner':f['claim_issue_context']['issues'][0]['origin']='gemini_audit'
    original=deepcopy((f,p))
    if fault:
        with pytest.raises(ValueError):m.empty_initial_draft(f,p)
    else:
        receipt=m.empty_initial_draft(f,p)
        assert receipt['write_authority'] is False and receipt['mandatory_original_unique_correction']
        assert (f,p)==original and p.records==[]
        packet={'proposal_sha256':'a'*64,'proposal':{'dispositions':[{'record_key':x.record_key} for x in p.dispositions]},
                'unresolved_proposal_review':receipt}
        response={'proposal_sha256':'a'*64,'verdicts':[{'record_key':x.record_key,'approved':True,'problems':[],
                    'reason':'diagnostic only'} for x in p.dispositions]}
        assert c.validate_review(packet,response) is False


@pytest.mark.skipif(not os.environ.get('WKP_OWNER_EMPTY_DRAFT_ROOT'),reason='explicit actual saved readonly input')
def test_actual_empty_owner_draft_reuses_answer_and_only_unused_initial_review(monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import source_reconciliation_runner as r
    load_dotenv('.env');store=PostgresKnowledgeStore();root=Path(os.environ['WKP_OWNER_EMPTY_DRAFT_ROOT'])
    leaves=[p for p in root.iterdir() if p.is_file()];before={p:p.read_bytes() for p in leaves}
    monkeypatch.setattr(r,'write',lambda *a,**k:pytest.fail('readonly preflight wrote artifact'))
    f=r.body(r.read(root/'scoped-materials.json'));c.guard_files(f)
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,{(p['collection'],p['object_id']):tuple(p['pin']) for p in f['sql_scope']['read_pins']})
    raw=r.validate_raw(root,'proposal',r.body(r.read(root/'proposal.packet.json')))
    proposed=r.effective_proposal(f,raw['response']);assert proposed.records==[]
    packet=r.initial_review_base(f,proposed)
    selected=f['inventory']['affected_keys']+f['inventory']['context_keys'];states=c.index(f['before_records'])
    assert packet['proposal']==proposed.model_dump()
    assert packet['before_records']==c.rows({k:states[k] for k in selected})
    assert packet['unresolved_proposal_review']['owner_claim_draft_diagnostic']['write_authority'] is False
    with pytest.raises(ValueError):c.review_packet(f,proposed)
    with pytest.raises(ValueError):m.validate_joint(f,proposed,{})
    with pytest.raises(ValueError):r.initial_review_base({k:v for k,v in f.items() if k!='owner_claim_completion'},proposed)
    assert not (root/'initial_review.reservation.json').exists()
    assert all(p.read_bytes()==data for p,data in before.items()) and set(root.iterdir())==set(leaves)


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


@pytest.mark.parametrize('typed_rows',[False,True])
def test_joint_claim_revision_requires_all_active_links_and_no_type_or_target_change(tmp_path,typed_rows):
    d={'claim_id':'C'};p=tmp_path/'direction.json';p.write_text(json.dumps(d))
    claim={'claim_id':'C','statement':'不完整','revision':3}
    link={'claim_id':'C','viewpoint_id':'V','link_type':'equivalent_component','effective_state':'active',
          'pinned_claim_revision':3,'revision':2,'viewpoint_claim_link_id':'L'}
    rows=c.rows({'claims/C':claim,'viewpoint_claim_links/L':link})
    f={'owner_claim_completion':binding(p),'before_records':rows}
    after={'claims/C':{**claim,'statement':'完整','revision':4},
           'viewpoint_claim_links/L':{**link,'pinned_claim_revision':4,'revision':3}}
    def records(value):
        result=c.rows(value)
        return [c.Row.model_validate(r) for r in result] if typed_rows else result
    def proposal():return NS(records=records(after),claim_assessments=[NS(cause='claim_statement')])
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
        pr.records=records(bad)
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


@pytest.mark.skipif(os.environ.get('WKP_OWNER_CLAIM_SAVED_REVIEW')!='1',reason='actual saved response read-only preflight')
def test_actual_incomplete_proposal_enters_only_unused_initial_review(tmp_path):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import source_reconciliation_runner as r
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    from backend.pipeline.source_review_input_repair import add_receipt
    load_dotenv('.env');s=PostgresKnowledgeStore()
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83/claim-issues/357b4822e214cfa79e54bfb6655021faed02c890efcbd5adf90f2b3f4851a9bb/action/CVPA-3c9563a838ccccf56ac2b9dc3b6b2f84544aaea99f4703d73b77e518686b8334')
    frozen=r.body(r.read(root/'scoped-materials.json'));raw=r.read(root/'proposal.raw.json')
    packet=r.body(r.read(root/'proposal.packet.json'))
    assert r.validate_raw(root,'proposal',packet)==raw
    with s.connect() as conn,conn.cursor() as cursor:
        s._assert_transaction_read_pins(cursor,{(p['collection'],p['object_id']):tuple(p['pin']) for p in frozen['sql_scope']['read_pins']})
    proposed=r.effective_proposal(frozen,raw['response'])
    with pytest.raises(ValueError,match='unresolved'):
        c.review_packet(frozen,proposed)  # Strict commit/final gate unchanged.
    review=add_receipt(frozen,raw['response'],r.initial_review_base(frozen,proposed))
    assert review['unresolved_proposal_review']['write_authority'] is False
    assert len(review['unresolved_proposal_review']['pending_link_keys'])==4
    assert len(review['unresolved_proposal_review']['historical_pending_keys'])==2
    assert review['proposal']['records']==proposed.model_dump()['records']
    old=r.read(root/'proposal.reservation.json')
    assert raw['request_payload_sha256']==old['request_payload_sha256'] and old['request_bytes']==514081
    assert not (root/'initial_review.reservation.json').exists()
    from backend.pipeline.viewpoint_resolution_runtime import authorized_request_limit
    campaign_root=root.parents[5]
    with authorized_request_limit(campaign_root/'execution',
            r.read(Path(frozen['owner_claim_completion']['path']))['campaign_sha256'],
            campaign_root/'owner-request-limit-600k.json'):
        adapter=r.stage_adapter(model_adapter('initial_review',live=False,deferred=True,issue=True),
                                review,root/'initial_review.raw.json')
        limit,_=r.request_limit(adapter,root/'initial_review.raw.json')
        assert adapter.request_bytes(review)<limit==600000
    from backend.api.canonical_repository.postgres_store import sha256_json
    proof=adapter.request_encoding_proof(review)
    assert proof['exact_roundtrip_verified'] and proof['restored_payload_sha256']==sha256_json(review)
    # Redundant history assessments stay visible and impact-only. They cannot
    # stand in for a missing real CVP, authorize writes or invent identities.
    extras=[i.record_key for i in proposed.cvp_impacts if i.record_key.startswith('viewpoint_identity')]
    for fault in ('missing-cvp','duplicate','history-write','history-retain','history-needs-direction',
                  'foreign-history','no-owner','creation-not-retired','historical-cvr-drift','prior-proof-drift'):
        p=proposed.model_copy(deep=True);f=deepcopy(frozen)
        if fault=='missing-cvp':p.cvp_impacts=[i for i in p.cvp_impacts if not i.record_key.startswith('canonical_viewpoints/')]
        elif fault=='duplicate':p.cvp_impacts.append(p.cvp_impacts[-1])
        elif fault=='history-write':p.records+=c.rows({extras[0]:{**c.index(f['before_records'])[extras[0]],'revision':999}})
        elif fault=='history-retain':next(d for d in p.dispositions if d.record_key==extras[0]).action='retain'
        elif fault=='history-needs-direction':next(i for i in p.cvp_impacts if i.record_key==extras[0]).action='needs_direction'
        elif fault=='foreign-history':next(i for i in p.cvp_impacts if i.record_key==extras[0]).record_key='viewpoint_identity_candidates/foreign'
        elif fault=='creation-not-retired':
            states=c.index(f['before_records']);head=states['canonical_viewpoints/CV-031b1f6eec4157cd37fe']
            next(x for x in f['sql_scope']['read_pins'] if x['collection']=='viewpoint_identity_candidates'
                 and x['object_id']==head['created_from_candidate_id'])['pin'][2]=False
        elif fault=='historical-cvr-drift':
            next(x for x in f['before_records'] if x['collection']=='canonical_viewpoints'
                 and x['object_id']=='CV-031b1f6eec4157cd37fe')['payload']['current_revision_id']='foreign'
        elif fault=='prior-proof-drift':f['prior_cvp_management']['file_sha256']='0'*64
        else:f.pop('owner_claim_completion')
        with pytest.raises(ValueError):r.initial_review_base(f,p)
    print(json.dumps({'review_records':len(review['before_records']),'review_bytes':len(json.dumps(review,ensure_ascii=False).encode()),
        'actual_request_bytes':adapter.request_bytes(review),'roundtrip_exact':True,
        'pending_links':4,'historical_pending':2,'write_authority':False,'new_model_calls':0}))


@pytest.mark.skipif(not os.environ.get('WKP_OWNER_CLAIM_SAVED_FINAL_ROOT'),reason='explicit saved final read-only preflight')
def test_saved_owner_final_typed_graph_without_calls_or_writes():
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import source_reconciliation_runner as r
    load_dotenv('.env');store=PostgresKnowledgeStore()
    root=Path(os.environ['WKP_OWNER_CLAIM_SAVED_FINAL_ROOT'])
    frozen=r.body(r.read(root/'scoped-materials.json'))
    c.guard_files(frozen)
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,{(p['collection'],p['object_id']):tuple(p['pin']) for p in frozen['sql_scope']['read_pins']})
    raw={stage:r.validate_raw(root,stage,r.body(r.read(root/(stage+'.packet.json'))))
         for stage in ('proposal','initial_review','correction','final_review')}
    proposal=r.effective_proposal(frozen,raw['correction']['response'])
    assert all(isinstance(row,c.Row) for row in proposal.records)
    packet=r.body(r.read(root/'final_review.packet.json'))
    assert c.validate_review(packet,raw['final_review']['response'])
    package=c.compile_package(frozen,proposal,raw['final_review'])
    assert m.validate_joint(frozen,proposal,package)
    assert len(proposal.records)==5
    assert {row.collection for row in proposal.records}=={'claims','viewpoint_claim_links'}
    assert not (root/'apply-intent.json').exists()
    print(json.dumps({'authenticated_stages':4,'review_verdicts':len(raw['final_review']['response']['verdicts']),
        'review_approved':True,'typed_joint_rows':len(proposal.records),'new_model_calls':0,'registry_writes':0}))
@pytest.mark.parametrize('fault',[None,'origin','certificate','new_delta','missing'])
def test_imported_group_revalidation_has_no_owner_claim_revision_subject(fault):
    from backend.pipeline.claim_owner_revision import dependency_direction,validate_dependency_completion
    value={'materials':{'origin':'explicit_historical_import'},'change_set_ids':['KCS-old'],
           'import_certificate':{'status':'verified_historical_completion','change_set_ids':['KCS-old']}}
    if fault=='origin':value['materials']['origin']='source_revision'
    if fault=='certificate':value['import_certificate']['status']='unverified'
    if fault=='new_delta':value['change_set_ids'].append('KCS-new-source')
    if fault=='missing':value.pop('import_certificate')
    if fault:
        with pytest.raises(ValueError,match='missing reviewed'):dependency_direction(value)
    else:
        assert dependency_direction(value)==(None,None)
        validate_dependency_completion(value)


@pytest.mark.parametrize('fault',[None,'approval','claim-delta'])
def test_reviewed_link_only_package_has_no_dependency_claim_subject(monkeypatch,fault):
    from backend.pipeline.claim_owner_revision import dependency_direction
    from backend.pipeline import cvp_link_replacement as link
    package={'source_repair_application':{'schema_version':link.SCHEMA,'run_root':'/TEST_ONLY'},'claims':[]}
    calls=[]
    def validate(p):
        calls.append(p)
        if fault=='approval':raise ValueError('cached link approval missing')
    monkeypatch.setattr(link,'validate_package',validate)
    if fault=='claim-delta':package['claims']=[{'claim_id':'C'}]
    value={'reviewed':{'evidence':{'approval':{},'package':{'payload':package}}}}
    if fault:
        with pytest.raises(ValueError):dependency_direction(value)
    else:assert dependency_direction(value)==(None,None)
    assert calls==[package]
