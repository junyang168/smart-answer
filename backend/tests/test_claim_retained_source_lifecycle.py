"""Retained Claim diagnosis through review and SQL; fixtures are NOT content approval."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pytest
from backend.pipeline import source_reconciliation as c, source_reconciliation_runner as r
from backend.pipeline import claim_issue_management as m
from backend.pipeline.cvp_sql_actions import execute_source_action
from backend.api.canonical_repository.postgres_store import ChangeSetConflict
from backend.tests.test_claim_issue_management import issue_setup, issue_for, materials, retain_proposal, IssuePorts
from backend.tests.test_source_reconciliation import scenario, put
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


@pytest.mark.parametrize('cause',['already_sufficient','cvp_link','claim_statement'])
@pytest.mark.parametrize('fault',[None,'pin','hold','issue','permission','duplicate','writable','proposed','source'])
def test_diagnostic_mode_matrix(issue_setup,tmp_path,cause,fault):
    _,frozen,issue=materials(issue_setup,tmp_path)
    frozen['sql_scope']={'read_pins':[{'collection':col,'object_id':oid,
        'pin':[p.get('revision',1),c.sha256_json({f:v for f,v in p.items() if f!='revision'}),False]}
        for (col,oid),p in ((c.split_key(k),p) for k,p in c.index(frozen['before_records']).items())]}
    proposal=retain_proposal(frozen,issue,cause)
    key=c.key('claims',issue.claim_id)
    if cause=='claim_statement':next(d for d in proposal.dispositions if d.record_key==key).action='unresolved'
    q=next(d for d in proposal.dispositions if d.record_key.startswith('questions/'));q.action='unresolved'
    if fault=='pin':next(p['pin'] for p in frozen['sql_scope']['read_pins'] if p['object_id']==issue.claim_id)[0]+=1
    if fault=='hold':next(row['payload'] for row in frozen['before_records'] if c.key(row['collection'],row['object_id'])==key)['review_status']='human_hold'
    if fault=='issue':proposal.claim_assessments[0].issue_id='foreign'
    if fault=='permission':next(p for p in frozen['manifest']['permissions'] if p['collection']=='claims')['fields']=[]
    if fault=='duplicate':proposal.claim_assessments.append(deepcopy(proposal.claim_assessments[0]))
    if fault=='writable':frozen['manifest']['permissions'].append({'collection':'questions','object_id':q.record_key.split('/',1)[1],'fields':['text'],'create':False})
    if fault=='proposed':proposal.records.append(c.Row.model_validate(next(row for row in frozen['before_records'] if c.key(row['collection'],row['object_id'])==q.record_key)))
    if fault=='source':proposal.claim_assessments[0].cause='source_unresolved'
    if fault:
        with pytest.raises(ValueError):r.initial_review_base(frozen,proposal)
        return
    original=deepcopy((frozen,proposal.model_dump()))
    packet=r.initial_review_base(frozen,proposal)
    assert packet['unresolved_proposal_review']['content_write_authority'] is False
    assert packet['unresolved_proposal_review']['mandatory_original_unique_correction'] is True
    response={'proposal_sha256':packet['proposal_sha256'],'verdicts':[
        {'record_key':d.record_key,'approved':True,'problems':[],'reason':'synthetic diagnostic'} for d in proposal.dispositions]}
    assert c.validate_review(packet,response) is False
    with pytest.raises(ValueError):c.compile_package(frozen,proposal,{'response':response})
    assert (frozen,proposal.model_dump())==original


class PendingPorts(IssuePorts):
    def __init__(self,issue,resolve):super().__init__(issue);self.resolve=resolve
    def claim_issue_adapter(self,stage):
        adapter=super().claim_issue_adapter(stage);generate=adapter.generate
        def response(packet):
            answer=generate(packet)
            if stage=='proposal' or (stage=='correction' and not self.resolve):
                next(d for d in answer['dispositions'] if d['record_key'].startswith('questions/'))['action']='unresolved'
            elif stage=='correction':
                next(d for d in answer['dispositions'] if d['record_key'].startswith('questions/'))['action']='retain'
            return answer
        adapter.generate=response
        return adapter


@pytest.mark.parametrize('resolve',[False,True])
def test_review_correction_strict_final_and_shared_sql(postgres_store,issue_setup,tmp_path,monkeypatch,resolve):
    manifest,states,report,policy=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scoped')
    ports=PendingPorts(issue,resolve)
    monkeypatch.setattr('backend.pipeline.cvp_sql_flow.verified_group_backup',lambda *a:{'dump':report,'restore_catalog_nonempty':True})
    before={k:postgres_store.get_record_state(*c.split_key(k)) for k in states}
    for _ in range(2):
        if not resolve:
            with pytest.raises(ValueError,match='unresolved'):execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
            assert postgres_store.get_cvp_group(run)['checkpoint']=='prepared'
        else:
            result=execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
            assert result['status'] in {'no_content_write','already_applied'}
            assert postgres_store.get_cvp_group(run)['checkpoint']=='applied'
        assert not postgres_store.get_cvp_group(run)['change_set_ids']
        assert before=={k:postgres_store.get_record_state(*c.split_key(k)) for k in states}
    assert ports.calls==['proposal','initial_review','correction']+(['final_review'] if resolve else [])
    if resolve:
        audit={'fixture':'NOT PROVIDER APPROVAL'}
        audit_binding=put(tmp_path/'audit.json',audit)
        with pytest.raises(ChangeSetConflict,match='remains pending'):
            postgres_store.complete_cvp_group(run,audit=audit,audit_binding=audit_binding,read_pins={})


def test_no_write_transaction_late_failure_and_cached_resume(postgres_store,issue_setup,tmp_path,monkeypatch):
    from backend.api.canonical_repository.transaction_validation import WriteTrackingCursor
    manifest,states,report,policy=issue_setup
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scoped')
    ports=PendingPorts(issue,True)
    monkeypatch.setattr('backend.pipeline.cvp_sql_flow.verified_group_backup',lambda *a:{'dump':report,'restore_catalog_nonempty':True})
    verify=WriteTrackingCursor.verify_no_content_write
    def fail(*a):raise ValueError('late zero-write verification failure')
    monkeypatch.setattr(WriteTrackingCursor,'verify_no_content_write',fail)
    before={k:postgres_store.get_record_state(*c.split_key(k)) for k in states}
    with pytest.raises(ValueError,match='late zero-write'):
        execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    saved=postgres_store.get_cvp_group(run)
    assert saved['checkpoint']=='reviewed' and not saved.get('no_content_write_reviewed_sha256')
    assert before=={k:postgres_store.get_record_state(*c.split_key(k)) for k in states}
    monkeypatch.setattr(WriteTrackingCursor,'verify_no_content_write',verify)
    execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert ports.calls==['proposal','initial_review','correction','final_review']
    assert postgres_store.get_cvp_group(run)['checkpoint']=='applied'
    assert before=={k:postgres_store.get_record_state(*c.split_key(k)) for k in states}


@pytest.mark.skipif(not os.getenv('WKP_RETAINED_SOURCE_ACTUAL_ROOT'),reason='explicit current saved scope; SELECT only')
@pytest.mark.parametrize('fault',[None,'pin','hold','writable','strict'])
def test_actual_saved_full_input_immutable(fault):
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline.cvp_sql_flow import decoded_pins
    root=Path(os.environ['WKP_RETAINED_SOURCE_ACTUAL_ROOT'])
    files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
    f=r.body(r.read(root/'scoped-materials.json'));c.guard_files(f)
    load_dotenv('.env');store=ReadOnly();run=f['sql_scope']['run_id'];saved=store.get_cvp_group(run)
    with store.connect() as conn,conn.cursor() as cur:store._assert_transaction_read_pins(cur,decoded_pins(f['sql_scope']['read_pins']))
    raw=r.validate_raw(root,'proposal',r.body(r.read(root/'proposal.packet.json')))
    proposal=r.effective_proposal(f,raw['response']);original=deepcopy(f)
    cid=f['claim_issue_context']['issues'][0]['claim_id'];key=c.key('claims',cid)
    pending=next(d.record_key for d in proposal.dispositions if d.action=='unresolved')
    if fault=='pin':next(p['pin'] for p in f['sql_scope']['read_pins'] if p['object_id']==cid)[0]+=1
    if fault=='hold':next(row['payload'] for row in f['before_records'] if c.key(row['collection'],row['object_id'])==key)['review_status']='human_hold'
    if fault=='writable':
        col,oid=c.split_key(pending);f['manifest']['permissions'].append({'collection':col,'object_id':oid,'fields':['reason'],'create':False})
    if fault:
        with pytest.raises(ValueError):
            if fault=='strict':c.review_packet(f,proposal)
            else:r.initial_review_base(f,proposal)
    else:
        packet=r.initial_review_base(f,proposal)
        assert packet['unresolved_proposal_review']['pending_record_keys']==[pending]
        assert packet['unresolved_proposal_review']['claim_draft_diagnostic']['schema_version']=='claim_retained_source_initial_diagnostic_v1'
        assert f==original
    assert store.get_cvp_group(run)==saved
    assert files=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
