from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_source_binding_projection as projection
from backend.pipeline import claim_management_intake as intake, claim_management_batch as batch
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file


def fragment(source):
    text=source['paragraphs']['S0001']['text'];digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
    return dict(fragment_id='FC',source_id='S',paragraph_key='S0001',verbatim_excerpt=text,
        source_sha256=source['body_sha256'],paragraph_text_sha256=digest(text),
        verbatim_excerpt_sha256=digest(text),anchor_state='source_version_bound',
        source_binding_status='source_deleted',deleted_in_source_sha256=source['body_sha256'])


@pytest.mark.parametrize('fault',['quote','source','paragraph_hash','excerpt_hash','anchor'])
def test_bad_binding_cannot_clear_deletion_metadata(fault):
    source={'body_sha256':'body','paragraphs':{'S0001':{'text':'exact words'}}}
    f=fragment(source)
    field={'quote':'verbatim_excerpt','source':'source_sha256','paragraph_hash':'paragraph_text_sha256',
           'excerpt_hash':'verbatim_excerpt_sha256','anchor':'anchor_state'}[fault]
    f[field]='wrong'
    assert projection.canonical_fields('source_fragments/FC',f,{}, {'S':source})=={}


def test_calibration_is_deterministic_and_cannot_write_readonly_or_approve_evidence():
    source={'body_sha256':'body','paragraphs':{'S0001':{'text':'exact words'}}}
    before={'source_fragments/FC':{'payload':fragment(source),'retired':False},
        'evidence_steps/EC':{'payload':dict(evidence_step_id='EC',source_fragment_ids=['FC'],
            source_binding_status='partially_deleted',support_eligibility='withheld_unreviewed'),'retired':False}}
    candidate=cm.Candidate(records=[],dispositions=[cm.ObjectDisposition(record_key=k,action='retain',reason='keep')for k in before])
    materials=dict(before=before,sources={'S':source},unit_request={'permissions':[]})
    fixed,_=projection.project(candidate,{},materials,force=True)
    assert fixed==candidate
    materials['unit_request']['permissions']=[dict(collection=k.split('/')[0],object_id=k.split('/')[1])for k in before]
    fixed,receipt=projection.project(candidate,{},materials,force=True)
    shuffled=deepcopy(materials);shuffled['before']=dict(reversed(list(before.items())))
    again,again_receipt=projection.project(candidate,{},shuffled,force=True)
    assert fixed==again and receipt==again_receipt
    rows={r.key:r.payload for r in fixed.records}
    assert rows['evidence_steps/EC']['support_eligibility']=='withheld_unreviewed'
    assert len(receipt['source_binding_status_corrections'])==3
    assert before['source_fragments/FC']['payload']['source_binding_status']=='source_deleted'


def test_saved_metadata_repair_reuses_gpt_and_requires_fresh_full_opus(claims):
    store,root=claims
    document=store.get_record_state('source_documents','S')['payload']
    source=cm.source.source_snapshot(cm.source.FileBinding(path=document['source_path'],
        file_sha256=hashlib.sha256(Path(document['source_path']).read_bytes()).hexdigest()),source=document)
    ec=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'legacy-deleted-marker',
        'source_fragments':[fragment(source)],'evidence_steps':[{**ec,'source_binding_status':'partially_deleted'}]}))
    prepared=intake.prepare(['C'],root,instruction='refresh C',groups=[['C']],policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    # This reproduces a request frozen before program binding calibration.
    direction=json.loads(cm.source.verify_file(request.authorization).read_text());direction.pop('source_binding_projection')
    path=root/'legacy-authorization.json';path.write_text(json.dumps(direction));request.authorization=cm.source.FileBinding.model_validate(binding(path))
    citation=dict(source_id='S',paragraph_key='S0001',exact_quote=source['paragraphs']['S0001']['text'])
    def review(packet):
        result=pass_review(packet)
        if not packet['unresolved']:
            result['findings']=[dict(target_key='source_fragments/FC',component='old_binding_metadata',category='evidence',
                reason='refresh the locator and clear source_binding_status and deletion metadata',
                acceptance_condition='current physical binding and consistent derived metadata',citations=[citation],
                affected_target_keys=[],affected_write_keys=[],impact_reason='source fragment')]
        else:
            fixed=packet['candidate']['records']
            payloads={r['collection']+'/'+r['object_id']:r['payload']for r in fixed}
            complete=payloads['source_fragments/FC']['source_binding_status']=='source_version_bound'
            if complete:
                assert 'deleted_in_source_sha256'not in payloads['source_fragments/FC']
                assert payloads['evidence_steps/EC']['source_binding_status']=='source_version_bound'
            result['prior_findings']=[dict(finding_id=fid,disposition='resolved'if complete else'persistent',
                reason='source_binding_status is canonical'if complete else'source_binding_status remains deleted despite the correct current quote',
                citations=[citation],affected_target_keys=[],affected_write_keys=['source_fragments/FC'],impact_reason='mechanical metadata')for fid in packet['unresolved']]
        return result
    def propose(packet):
        return dict(changes=[dict(record_key='source_fragments/FC',fields_json=json.dumps(dict(source_segment_index='1')),
                reason='bind the existing quote',citations=[citation]),dict(record_key='evidence_steps/EC',
                fields_json=json.dumps(dict(statement=source['paragraphs']['S0001']['text'])),reason='complete own evidence',citations=[citation])],
            creations=[],retirements=[],findings=[],finding_resolutions=[dict(finding_id=fid,disposition='resolved',
                reason='locator repaired',citations=[citation],affected_target_keys=[],affected_write_keys=['source_fragments/FC'],impact_reason='own locator')for fid in packet['unresolved']])
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    stopped=execute_claim_management(store,request,root=execution,ports=ports)
    assert stopped['reason']=='disagreement',stopped
    assert ports.calls==['opus','gpt','opus']
    parent=store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id));rid=next(iter(parent['latest_rounds'].values()))
    old_raw=(execution/rid/'opus.raw.json').read_bytes();ledger=deepcopy(parent['call_reservations'])
    tests=root/'repair-tests.json';tests.write_text(json.dumps(dict(fixed_commit='a'*40,passed=1,failed=0)))
    proof=projection.resume_saved_metadata(store,rid,fixed_commit='a'*40,test_evidence=binding(tests))
    assert projection.resume_saved_metadata(store,rid,fixed_commit='a'*40,test_evidence=binding(tests))==proof
    assert store.get_cvp_group(parent['run_id'])['call_reservations']==ledger
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus','opus']
    assert (execution/rid/'opus.raw.json').read_bytes()==old_raw
    assert store.get_record_state('source_fragments','FC')['payload']['source_binding_status']=='source_version_bound'


def test_new_request_calibrates_deletion_flags_before_first_review_without_gpt(claims):
    store,root=claims;document=store.get_record_state('source_documents','S')['payload']
    source=cm.source.source_snapshot(cm.source.FileBinding(path=document['source_path'],
        file_sha256=hashlib.sha256(Path(document['source_path']).read_bytes()).hexdigest()),source=document)
    ec=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'pure-physical-deletion-flags',
        'source_fragments':[fragment(source)],'evidence_steps':[{**ec,'source_binding_status':'partially_deleted'}]}))
    prepared=intake.prepare(['C'],root,instruction='refresh C',groups=[['C']],policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store);request,execution=next(iter(jobs.values()))
    def review(packet):
        rows={r['collection']+'/'+r['object_id']:r['payload']for r in packet['candidate']['records']}
        assert rows['source_fragments/FC']['source_binding_status']=='source_version_bound'
        assert 'deleted_in_source_sha256'not in rows['source_fragments/FC']
        assert rows['evidence_steps/EC']['source_binding_status']=='source_version_bound'
        assert rows['evidence_steps/EC']['support_eligibility']==ec['support_eligibility']
        return pass_review(packet)
    ports=SyntheticPorts(store,execution,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus']
