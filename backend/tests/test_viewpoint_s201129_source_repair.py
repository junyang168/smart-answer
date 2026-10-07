from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.knowledge_models import ViewpointIdentityDecisionRecord
from backend.api.canonical_repository.reviewed_candidate_contract import validate_store_package_authorization, ConsensusApplicationError
from backend.pipeline import viewpoint_s201129_source_repair as repair
from backend.pipeline import viewpoint_s201129_source_epoch as epoch
from backend.pipeline import viewpoint_applied_source_epochs as composed


@pytest.mark.parametrize('drift',[None,'missing','duplicate','foreign','revision','sha','payload','create_before','unbound','retire'])
def test_generic_applied_preimages_read_only_exact_SQL_versions(tmp_path,monkeypatch,drift):
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    from backend.api.canonical_repository.postgres_store import record_content_sha
    old={'claim_id':'any-Claim','revision':3,'statement':'old','review_status':'human_review_required'}
    digest=record_content_sha(old)
    operations=[{'operation':'update','collection':'claims','object_id':'any-Claim',
        'before_revision':3,'before_sha256':digest},
        {'operation':'create','collection':'questions','object_id':'any-new-object',
         'before_revision':None,'before_sha256':None}]
    rows=[('claims','any-Claim',3,digest,deepcopy(old))];queries=[]
    package={'any_original':'package'};bound={'path':str(tmp_path/'effective-package.json')}
    monkeypatch.setattr(composed,'consume_applied_source_package',lambda b,s:package)
    monkeypatch.setattr(runtime,'read_artifact',lambda p:{'operations':operations})
    monkeypatch.setattr(repair,'saved_before',lambda:pytest.fail('whole predecessor fallback'))
    if drift=='missing':rows=[]
    elif drift=='duplicate':rows*=2
    elif drift=='foreign':rows[0]=('claims','foreign',3,digest,old)
    elif drift=='revision':rows[0]=('claims','any-Claim',2,digest,old)
    elif drift=='sha':rows[0]=('claims','any-Claim',3,'a'*64,old)
    elif drift=='payload':rows[0][4]['statement']='unreviewed'
    elif drift=='create_before':operations[1]['before_revision']=1
    elif drift=='unbound':operations[0]['before_sha256']=None
    elif drift=='retire':operations[0]['operation']='retire'
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,q,params=None):queries.append((q,params))
        def fetchall(self):return rows
    class Conn(Cursor):
        def cursor(self):return Cursor()
    class Store:
        def connect(self):return Conn()
    if drift:
        with pytest.raises(ValueError):composed.applied_source_preimages(bound,Store())
    else:
        actual,prior=composed.applied_source_preimages(bound,Store())
        assert actual==package and prior=={('claims','any-Claim'):old,('questions','any-new-object'):None}
        assert prior['claims','any-Claim']['review_status']=='human_review_required'
        assert len(queries)==2 and queries[0][0]=='SET TRANSACTION READ ONLY'
        assert queries[1][1]==('claims','any-Claim',3,digest)


@pytest.mark.parametrize('drift',[None,'package','missing_context','duplicate_context','context_change'])
def test_legacy_postimages_use_generic_SQL_and_original_unchanged_context(monkeypatch,drift):
    from backend.api.canonical_repository.postgres_store import _normalize_records
    old={'evidence_step_id':'E','revision':1,'statement':'same'}
    package={'evidence_steps':[{**old,'revision':2}]}
    logical={'before_evidence':[old]};plan={'operations':[]}
    monkeypatch.setattr(composed,'applied_source_preimages',lambda *a:({'wrong':'package'} if drift=='package' else package,{}))
    monkeypatch.setattr(epoch,'binding',lambda:{'path':'/arbitrary-package'})
    monkeypatch.setattr(repair,'PostgresKnowledgeStore',lambda:None)
    monkeypatch.setattr(repair,'reviewed_inputs',lambda:({},None,logical,None))
    monkeypatch.setattr(repair,'read_artifact',lambda p:plan)
    monkeypatch.setattr(repair,'saved_before',lambda:pytest.fail('whole predecessor parsed'))
    if drift=='missing_context':logical['before_evidence']=[]
    elif drift=='duplicate_context':logical['before_evidence']*=2
    elif drift=='context_change':old['statement']='unreviewed'
    if drift:
        with pytest.raises(ValueError):epoch.postimages(package)
    else:
        assert epoch.postimages(package)['evidence_steps']['E']==old
        assert package['evidence_steps'][0]['revision']==2


@pytest.mark.parametrize('frame',['snapshot','compact_parent'])
@pytest.mark.parametrize('drift',[None,'package','operation','plan','intent','receipt','readback','missing_pin','raw','SQL','SQL_summary','parent_file','parent_pin','parent_fp'])
def test_generic_completed_application_consumption_has_no_source_ID_or_writer_rebuild(tmp_path,monkeypatch,drift,frame):
    import json
    from backend.pipeline import source_reconciliation_runner as r
    from backend.pipeline import source_reconciliation as c
    from backend.api.canonical_repository.postgres_store import ChangeOperation,operation_fingerprint_rows,record_content_sha
    version='historical_application_format_not_a_sermon_ID'
    root=tmp_path/'arbitrary-approved-source';root.mkdir()
    def put(name,value):
        (root/name).write_text(json.dumps(value));return r.binding(root/name)
    parent=put('arbitrary-parent-package.json',{'unchanged':'parent package, not another writer invocation'})
    before_name='before.json' if frame=='snapshot' else 'before-proof.json'
    predecessor={'before_records':['MUST_NOT_PARSE']} if frame=='snapshot' else {
        'parent':parent,'pre_fingerprint_sha256':'b'*64,'Registry_writes':0}
    before=put(before_name,predecessor)
    raw=put('review.json',{'original':'reviewed source-specific data, not special code'})
    package={'package_id':'arbitrary-package','claims':[{'claim_id':'C','statement':'unchanged reviewed value','revision':2}],
        'source_repair_application':{'schema_version':version,'before_binding':before,'bindings':[raw]}}
    if frame=='compact_parent':
        del package['source_repair_application']['before_binding']
        package['source_repair_application']['parent_source_application']=parent
    bound=put('effective-package.json',package);sha=c.sha256_json(package)
    payload={'claim_id':'C','statement':'unchanged reviewed value'}
    operation={'operation':'update','collection':'claims','object_id':'C','before_sha256':'a'*64,
        'after_sha256':record_content_sha(payload),'before_revision':1,'after_revision':2,'payload':payload,'removed_fields':[]}
    fp=c.sha256_json({'planner_schema':'wang_postgres_changeset_v2','source_kind':version,'source_sha256':sha,
        'package_id':package['package_id'],'operations':operation_fingerprint_rows([ChangeOperation(**operation)]),'review_events':[]})
    plan={'change_set_id':'KCS-'+fp[:20],'fingerprint_sha256':fp,'source_kind':version,'source_sha256':sha,
        'package_id':package['package_id'],'operations':[operation],'review_events':[]}
    plan_pin=put('dry-run-plan.json',plan)
    receipt={'change_set_id':plan['change_set_id'],'status':'applied','summary':{'operations':1,'updated':1}}
    receipt_pin=put('apply-receipt.json',receipt)
    intent={'package_sha256':sha,'plan_sha256':c.sha256_json(plan),'schema_version':version,
        'single_apply_required':True,'pre_fingerprint_sha256':'b'*64}
    if frame=='compact_parent':del intent['pre_fingerprint_sha256']
    intent_pin=put('apply-intent.json',intent)
    pins=[before,raw,bound,plan_pin,receipt_pin,intent_pin,
        put('backup.json',{'original':'backup receipt'}),put('pg-restore-proof.json',{'original':'pg restore pass'})]
    verified={'status':'applied_readback_verified','change_set_id':plan['change_set_id'],
        'master_data_mutations':1,'pre_fingerprint_sha256':'b'*64,'bindings':pins}
    put('verified-result.json',verified)
    SQL=[version,sha,fp,'applied',deepcopy(receipt['summary'])];calls=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,query,params=None):calls.append((query,params))
        def fetchone(self):return tuple(SQL)
    class Connection(Cursor):
        def cursor(self):return Cursor()
    class Store:
        def connect(self):return Connection()
    def replace(name,value):
        # A coherent new physical pin still cannot hide a different SQL package
        # or exact operation fingerprint. Test-only mutation of saved fixtures.
        (root/name).write_text(json.dumps(value));pin=r.binding(root/name)
        for old in pins:
            if old['path']==pin['path']:old.update(pin)
        (root/'verified-result.json').write_text(json.dumps(verified))
    if drift=='package':package['claims'][0]['statement']='unreviewed';replace('effective-package.json',package);bound=r.binding(root/'effective-package.json')
    elif drift=='operation':operation['payload']['statement']='unreviewed';replace('dry-run-plan.json',plan)
    elif drift=='plan':plan['operations']=[];replace('dry-run-plan.json',plan)
    elif drift=='intent':intent['single_apply_required']=False;replace('apply-intent.json',intent)
    elif drift=='receipt':receipt['status']='planned';replace('apply-receipt.json',receipt)
    elif drift=='readback':verified['master_data_mutations']=2;replace('verified-result.json',verified)
    elif drift=='missing_pin':pins.remove(before);replace('verified-result.json',verified)
    elif drift=='raw':(root/'review.json').write_text('{}')
    elif drift=='SQL':SQL[3]='planned'
    elif drift=='SQL_summary':SQL[4]['operations']=2
    elif drift in {'parent_file','parent_pin','parent_fp'}:
        if frame!='compact_parent':pytest.skip('compact parent format only')
        if drift=='parent_file':(root/'arbitrary-parent-package.json').write_text('{}')
        elif drift=='parent_pin':predecessor['parent']={**parent,'file_sha256':'f'*64};replace(before_name,predecessor)
        else:predecessor['pre_fingerprint_sha256']='f'*64;replace(before_name,predecessor)
    from backend.pipeline import viewpoint_resolution_runtime as resolution
    original_read=resolution.read_artifact
    def read(path):
        if Path(path).name=='before.json':pytest.fail('completed consumer parsed whole old snapshot')
        return original_read(path)
    monkeypatch.setattr(resolution,'read_artifact',read)
    monkeypatch.setattr(repair,'validate_package',lambda *a:pytest.fail('old source writer reconstruction'))
    if drift:
        with pytest.raises(ValueError):composed.consume_applied_source_package(bound,Store())
    else:
        assert composed.has_applied_source_frame(root/'effective-package.json',package)
        assert composed.consume_applied_source_package(bound,Store())==package
        assert calls[0][0]=='SET TRANSACTION READ ONLY' and len(calls)==2


@pytest.fixture(autouse=True)
def isolate_synthetic_source_tests_from_live_child_receipts(monkeypatch, tmp_path):
    # These unit tests provide synthetic records. The later real child receipt
    # must not make their parent validator read production data or call SQL.
    from backend.pipeline import viewpoint_s201129_link_support_apply as child
    monkeypatch.setattr(child, 'ROOT', tmp_path / 'no-live-child-application')
    monkeypatch.setattr(repair, '_saved_before_cache', None)


@pytest.mark.parametrize('drift',[None,'missing_create','retired','payload','revision','sha','old_link','review_pre_fp'])
def test_completed_child_restores_exact_scoped_records_without_old_writer(tmp_path,monkeypatch,drift):
    from backend.pipeline import viewpoint_s201129_link_support_apply as child
    monkeypatch.setattr(child,'ROOT',tmp_path)
    monkeypatch.setattr(child,'PACKAGE',tmp_path/'effective-package.json')
    monkeypatch.setattr(child.review,'LINK','arbitrary-link')
    (tmp_path/'apply-receipt.json').write_text('{}')
    old={'viewpoint_claim_link_id':'arbitrary-link','revision':2,'link_type':'equivalent_component'}
    payloads=[('viewpoint_claim_links','arbitrary-link',{'viewpoint_claim_link_id':'arbitrary-link','link_type':'supports'},3,'update'),
        ('viewpoint_identity_candidates','any-candidate',{'identity_candidate_id':'any-candidate'},1,'create'),
        ('viewpoint_identity_decisions','any-decision',{'identity_decision_id':'any-decision'},1,'create')]
    operations=[{'collection':c,'object_id':oid,'payload':p,'after_revision':rev,'operation':kind}
                for c,oid,p,rev,kind in payloads]
    current={(c,oid):{'payload':{**p,'revision':rev},'revision':rev,
        'content_sha256':repair.semantic_record_sha(p),'retired':False}
        for c,oid,p,rev,kind in payloads}
    current['claims','unrelated']={'unchanged':'leave this row alone'}
    # Hash helper omits revision exactly as the production store does.
    for row in list(current.values())[:3]:row['content_sha256']=repair.semantic_record_sha(row['payload'])
    plan={'operations':operations,'change_set_id':'KCS-any','source_sha256':'a'*64}
    package={'original':'entire child package'}
    files={'dry-run-plan.json':plan,'effective-package.json':package,
        'before-proof.json':{'before_link':deepcopy(old),'pre_fingerprint_sha256':'c'*64},
        'apply-receipt.json':{'status':'applied','change_set_id':'KCS-any'},
        'verified-result.json':{'status':'applied_readback_verified','change_set_id':'KCS-any',
            'master_data_mutations':3,'bindings':[]}}
    calls=[]
    monkeypatch.setattr(child,'read_artifact',lambda path:deepcopy(files[Path(path).name]))
    monkeypatch.setattr(child,'reviewed_inputs',lambda:({'targets':[{'before_link':old}],
        'current_Registry_fingerprint_sha256':'c'*64},{}))
    monkeypatch.setattr(child,'validate_package',lambda *a:pytest.fail('old child writer'))
    monkeypatch.setattr(child,'parent_snapshot',lambda *a:pytest.fail('parent whole Registry replay'))
    monkeypatch.setattr(child,'validate_plan_package',lambda *a:None)
    monkeypatch.setattr(repair,'binding',lambda path:{'path':str(path),'file_sha256':'b'*64})
    monkeypatch.setattr(composed,'consume_applied_source_package',lambda *a:(calls.append(a[0]) or package))
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,*a):return self
        def fetchone(self):return child.VERSION,'a'*64,'applied'
    class Store:
        def connect(self):return Conn()
    monkeypatch.setattr(repair,'PostgresKnowledgeStore',Store)
    candidate=current['viewpoint_identity_candidates','any-candidate']
    if drift=='missing_create':del current['viewpoint_identity_candidates','any-candidate']
    elif drift=='retired':candidate['retired']=True
    elif drift=='payload':candidate['payload']['extra']='unreviewed'
    elif drift=='revision':candidate['revision']=2
    elif drift=='sha':candidate['content_sha256']='bad'
    elif drift=='old_link':files['before-proof.json']['before_link']['link_type']='unreviewed'
    elif drift=='review_pre_fp':files['before-proof.json']['pre_fingerprint_sha256']='f'*64
    original=deepcopy(current)
    if drift:
        with pytest.raises(ValueError):child.restore_parent_records(current)
    else:
        result=child.restore_parent_records(current)
        assert set(result)=={('claims','unrelated'),('viewpoint_claim_links','arbitrary-link')}
        assert result['viewpoint_claim_links','arbitrary-link']['payload']==old
        assert result['claims','unrelated']==current['claims','unrelated']
    assert current==original and len(calls)==1


def before_cache_fixture(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(repair, 'APPLICATION_ROOT', tmp_path)
    states = {('claims', 'C'): {'revision': 1, 'statement': 'original'}}
    fp = repair.fingerprint(states, ['claims'])
    scope = {'registry_collections': ['claims'], 'registry_fingerprint_sha256': fp}
    proof = {'collections': ['claims'], 'pre_fingerprint_sha256': fp,
             'before_records': [{'collection': 'claims', 'object_id': 'C', 'payload': states['claims', 'C']}]}
    (tmp_path/'before.json').write_text(json.dumps(proof))
    monkeypatch.setattr(repair, 'reviewed_inputs', lambda: (scope, None, None, None))
    return scope, proof


def test_before_cache_parses_and_recomputes_once_but_returns_isolated_data(monkeypatch, tmp_path):
    before_cache_fixture(monkeypatch, tmp_path)
    original = repair.read_artifact
    calls = []
    monkeypatch.setattr(repair, 'read_artifact', lambda p: (calls.append(p), original(p))[1])
    first, proof = repair.saved_before()
    first['claims', 'C']['statement'] = 'caller mutation'
    proof['collections'].append('foreign')
    second, second_proof = repair.saved_before()
    assert len(calls) == 1
    assert second['claims', 'C']['statement'] == 'original'
    assert second_proof['collections'] == ['claims']


@pytest.mark.parametrize('drift', ['content', 'expected_fingerprint', 'collections', 'deleted', 'during_read'])
def test_before_cache_never_hides_proof_or_expected_pin_drift(monkeypatch, tmp_path, drift):
    import json
    scope, proof = before_cache_fixture(monkeypatch, tmp_path)
    repair.saved_before()
    path = tmp_path/'before.json'
    if drift == 'content':
        proof['before_records'][0]['payload']['statement'] = 'changed'
        path.write_text(json.dumps(proof))
    elif drift == 'expected_fingerprint': scope['registry_fingerprint_sha256'] = '0'*64
    elif drift == 'collections': scope['registry_collections'] = ['claims', 'foreign']
    elif drift == 'deleted': path.unlink()
    else:
        repair._saved_before_cache = None
        original = repair.read_artifact
        def changing_read(p):
            result = original(p)
            path.write_text(json.dumps({**proof, 'changed': True}))
            return result
        monkeypatch.setattr(repair, 'read_artifact', changing_read)
    with pytest.raises((ValueError, FileNotFoundError)):
        repair.saved_before()


def decision():
    return dict(identity_decision_id='VID-test', identity_candidate_id='VIC-test',
        decision='match_existing', resolved_viewpoint_id='CV-test',
        reviewer_kind='system', reviewer_id='s201129_owner_scoped_opus_terminal_review',
        approval_basis='owner_scoped_independent_terminal_review', reason='exact review',
        input_sha256='a'*64, review_artifact_sha256='b'*64, policy_version=repair.VERSION,
        reviewer_model_ids=['claude-opus-5-5'], semantic_call_artifact_sha256s=['b'*64],
        created_at='2026-10-06T00:00:00Z', review_status='system_approved')


@pytest.mark.parametrize('mutation',[None,'wrong_policy','fake_human','fake_dual','wrong_model','missing_raw','missing_review','wrong_reviewer'])
def test_actual_single_opus_provenance_not_fake_dual_review(mutation):
    row=decision()
    changes={'wrong_policy':('policy_version','other'), 'fake_human':('reviewer_kind','human_editor'),
        'fake_dual':('approval_basis','dual_model_consensus'), 'wrong_model':('reviewer_model_ids',['gpt-6.1-sol']),
        'missing_raw':('semantic_call_artifact_sha256s',[]), 'missing_review':('review_artifact_sha256',None),
        'wrong_reviewer':('reviewer_id','arbitrary')}
    if mutation:
        k,v=changes[mutation];row[k]=v
        with pytest.raises(ValidationError):ViewpointIdentityDecisionRecord.model_validate(row)
    else:
        assert ViewpointIdentityDecisionRecord.model_validate(row).reviewer_model_ids==['claude-opus-5-5']


@pytest.mark.parametrize('package',[
    {'viewpoint_identity_decisions':[decision()]},
    {'claims':[{'review_status':'independent_terminal_reviewed'}]},
    {'source_repair_application':{'schema_version':repair.VERSION,'review_root':'/foreign'}}])
def test_scoped_review_cannot_bypass_exact_package_store_gate(package):
    with pytest.raises((ValueError,ConsensusApplicationError)):
        validate_store_package_authorization(package)


@pytest.mark.parametrize('mutation',[None,'missing','payload','revision','sha','retired'])
def test_all_postimages_not_only_changed_claims(monkeypatch,mutation):
    normal={'source_fragments':{'F':{'fragment_id':'F','revision':3,'quote':'exact'}}}
    monkeypatch.setattr(epoch,'postimages',lambda p:deepcopy(normal))
    p=normal['source_fragments']['F']
    records={('source_fragments','F'):{'payload':deepcopy(p),'revision':3,'content_sha256':repair.semantic_record_sha(p),'retired':False}}
    if mutation=='missing':records.clear()
    elif mutation=='payload':records['source_fragments','F']['payload']['quote']='drift'
    elif mutation=='revision':records['source_fragments','F']['revision']=4
    elif mutation=='sha':records['source_fragments','F']['content_sha256']='wrong'
    elif mutation=='retired':records['source_fragments','F']['retired']=True
    if mutation:
        with pytest.raises(ValueError):epoch.validate_postimage({},records)
    else:assert epoch.validate_postimage({},records)==normal


@pytest.mark.parametrize('mutation',[None,'duplicate','foreign','nested','unsorted','missing_hash'])
def test_multiple_epochs_keep_separate_exact_proofs(monkeypatch,mutation):
    old={'path':str(composed.historical.PACKAGE),'file_sha256':'a'*64}
    new={'path':str(repair.PACKAGE),'file_sha256':'b'*64}
    rows=sorted([old,new],key=lambda b:b['path'])
    proof={'schema_version':composed.VERSION,'bindings':rows}
    if mutation=='duplicate':proof['bindings']=[old,old]
    elif mutation=='foreign':proof['bindings']=[{'path':'/foreign','file_sha256':'c'*64}]
    elif mutation=='nested':proof['bindings']=[proof.copy()]
    elif mutation=='unsorted':proof['bindings']=list(reversed(rows))
    elif mutation=='missing_hash':proof['bindings']=[{'path':str(repair.PACKAGE)}]
    if mutation:
        with pytest.raises((ValueError,KeyError)):composed.members(proof)
    else:
        assert composed.members(proof)==rows
        assert composed.implementation(old) is composed.historical
        assert composed.implementation(new) is epoch


def test_pending_reviewed_claim_restores_only_membership_and_no_mutation(monkeypatch):
    claim={'claim_id':repair.PREFIX+'CL007','revision':3,'statement':'new','review_status':'independent_terminal_reviewed'}
    old={**claim,'revision':2,'statement':'old','review_status':'ai_consensus_reviewed'}
    records={('claims',claim['claim_id']):{'payload':deepcopy(claim),'revision':3,'content_sha256':repair.semantic_record_sha(claim),'retired':False}}
    package={'claims':[claim]};manifest={'claim_packets':[{'claim_id':claim['claim_id']}]}
    monkeypatch.setattr(repair,'validate_package',lambda p:None)
    monkeypatch.setattr(epoch,'postimages',lambda p:{'claims':{claim['claim_id']:claim}})
    monkeypatch.setattr(repair,'saved_before',lambda:({('claims',claim['claim_id']):old},{}))
    monkeypatch.setattr(repair,'reviewed_inputs',lambda:({'manifest_path':'/manifest'},None,None,None))
    monkeypatch.setattr(repair,'read_artifact',lambda p:manifest)
    original=deepcopy(records)
    checked,used=epoch.membership_snapshot(manifest,records,package,set(),{claim['claim_id']})
    assert checked['claims',claim['claim_id']]['payload']==old
    assert records==original and used==[claim['claim_id']]
    with pytest.raises(ValueError):epoch.selection_row('another-claim',package)


@pytest.mark.parametrize('wrong',[True,False])
def test_exact_legacy_flag_not_a_global_human_hold_waiver(monkeypatch,wrong):
    cid=repair.PREFIX+'CL004'
    claim={'claim_id':cid,'revision':3,'review_status':'human_review_required','review_note':'human_spot_check old'}
    before=deepcopy(claim)
    if wrong:claim['review_note']='real content hold'
    monkeypatch.setattr(repair,'stable_decided_at',lambda p:'2026-10-06T00:00:00Z')
    monkeypatch.setattr(repair,'binding',lambda p:{'path':str(p),'file_sha256':'a'*64})
    draft={'claims':[claim],'viewpoint_claim_links':[]}
    if wrong:
        with pytest.raises(ValueError):repair.package_for({('claims',cid):before},{},draft,{'owner_authorization':{}},{})
    else:
        result=repair.package_for({('claims',cid):before},{},draft,{'owner_authorization':{}},{})
        assert result['claims'][0]['review_status']=='independent_terminal_reviewed'
        assert 'Not fresh dual-review or human approval' in result['claims'][0]['review_note']
        assert draft['claims'][0]==claim


def test_epoch_cannot_double_transition_same_physical_source(monkeypatch):
    proof={'path':str(repair.PACKAGE),'file_sha256':'a'*64}
    monkeypatch.setattr(composed,'members',lambda p:[proof,proof])
    monkeypatch.setattr(epoch,'grouping_physical_transition',lambda m,p:{'/source':('old','new')})
    with pytest.raises(ValueError,match='conflicting'):
        composed.grouping_physical_transition({},proof)


@pytest.mark.parametrize('field',['claims','source_fragments','viewpoint_claim_links','viewpoint_identity_decisions','canonical_viewpoints','source_repair_application'])
def test_store_rebuilds_entire_reviewed_package_not_partial_pass(monkeypatch,field):
    expected={'claims':[{'exact':'claim'}],'source_fragments':[{'exact':'source'}],
        'viewpoint_claim_links':[{'exact':'link'}],'viewpoint_identity_decisions':[decision()],
        'source_repair_application':{'schema_version':repair.VERSION,'review_root':str(repair.REVIEW_ROOT)}}
    monkeypatch.setattr(repair,'reviewed_inputs',lambda:({},{},{},{}))
    monkeypatch.setattr(repair,'saved_before',lambda:({},{}))
    monkeypatch.setattr(repair,'package_for',lambda *args:deepcopy(expected))
    repair.validate_package(expected)
    altered=deepcopy(expected)
    if field=='source_repair_application':altered[field]['review_root']='/foreign'
    else:altered[field]=[{'not':'reviewed'}]
    with pytest.raises(ValueError):repair.validate_package(altered)


def test_no_application_or_audit_replay(monkeypatch,tmp_path):
    from backend.pipeline import viewpoint_s201129_source_continuation as worker
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    with pytest.raises(ValueError,match='already attempted'):worker.run()
    out=tmp_path/'exact-audit';out.mkdir()
    with pytest.raises(ValueError,match='already attempted'):worker.verified_audit('exact-audit',{'C'},{'V'})
    with pytest.raises(ValueError,match='outside exact'):worker.recompletion_seed(Path('/foreign'),[])


@pytest.mark.parametrize('sequence',[1,39,42,43,156])
def test_source_audit_cannot_complete_pending_or_unaffected_group(sequence,tmp_path):
    from backend.pipeline import viewpoint_s201129_source_continuation as worker
    with pytest.raises(ValueError,match='two already-completed'):
        worker.completion_from_saved_audit(sequence,tmp_path/'audit.json',tmp_path/'output')


@pytest.mark.parametrize('mutation',[None,'wrong_old','wrong_current','other_group','foreign_path'])
def test_historical_source_pin_projection_is_not_current_audit_approval(monkeypatch,mutation):
    path=str(repair.BASE.parents[2]/'script_review/S 201129.json')
    row={'path':path,'file_sha256':'old'}
    seed={'group_key':'p023:wife_clause_passive_forced_remarriage','bindings':[row],
          'audit_path':'/original/audit.json'}
    scope={'registry_source_before':{'source_file_sha256':'old'},'source_files':{'review':'new','published':'pub'}}
    monkeypatch.setattr(repair,'file_sha256',lambda p:'wrong' if mutation=='wrong_current' else 'new')
    if mutation=='wrong_old':row['file_sha256']='foreign'
    if mutation=='other_group':seed['group_key']='unaffected'
    if mutation=='foreign_path':row['path']='/another/source'
    original=deepcopy(seed)
    if mutation in {'wrong_old','wrong_current','other_group'}:
        with pytest.raises(ValueError):repair.historical_seed_projection(seed,scope)
    else:
        checked,changes=repair.historical_seed_projection(seed,scope)
        assert seed==original and checked['audit_path']=='/original/audit.json'
        if mutation=='foreign_path':assert changes==[] and checked==seed
        else:assert changes==[row] and checked['bindings'][0]['file_sha256']=='new'


@pytest.mark.parametrize('mutation',[None,'omitted_change','changed_operation','foreign_operation','duplicate_operation'])
def test_actual_plan_not_draft_controls_unchanged_evidence_revision(monkeypatch,mutation):
    monkeypatch.setattr(epoch,'merge_over_existing',lambda p,b,s:deepcopy(p))
    before={('evidence_steps','E'):{'evidence_step_id':'E','revision':1,'statement':'same'}}
    normal={'evidence_steps':{'E':{**before['evidence_steps','E'],'revision':2}}}
    stated={('evidence_steps','E'):{'revision','statement','evidence_step_id'}}
    plan={'operations':[]}
    if mutation=='omitted_change':normal['evidence_steps']['E']['statement']='different'
    if mutation in {'changed_operation','foreign_operation','duplicate_operation'}:
        operation={'collection':'evidence_steps','object_id':'E','after_revision':2,
                   'payload':{'evidence_step_id':'E','statement':'same'}}
        if mutation=='changed_operation':operation['payload']['statement']='wrong'
        if mutation=='foreign_operation':operation['object_id']='foreign'
        plan['operations']=[operation]
        if mutation=='duplicate_operation':plan['operations'].append(deepcopy(operation))
    if mutation:
        with pytest.raises(ValueError):epoch.planned_postimages(normal,stated,before,plan)
    else:
        result=epoch.planned_postimages(normal,stated,before,plan)
        assert result['evidence_steps']['E']==before['evidence_steps','E']
        assert normal['evidence_steps']['E']['revision']==2
