"""Explicit capacity authority reuses a saved GPT, not an extra model vote."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch, claim_review_units as units
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file


def authorize(root, parent, materials_path, candidate_path):
    from backend.pipeline.source_reconciliation_runner import body, read
    materials = body(read(materials_path))
    candidate = cm.Candidate.model_validate(body(read(candidate_path)))
    value = dict(schema_version='wang_claim_review_capacity_exception_v1', authorized_by='owner',
        authorization_quote='Accept these four proposals and the original-eight/proposed-four review group',
        human_content_approval=False, default_limit=10, limit=12,
        action_id=parent['request']['action_id'], round_id=materials['round_id'],
        prior_metadata_sha256=cm.sha256_json({k:v for k,v in parent.items() if k not in {'run_id','status','error'}}),
        materials=binding(materials_path), candidate=binding(candidate_path),
        candidate_sha256=candidate.digest, original_target_keys=parent['request']['target_keys'],
        proposed_claim_keys=sorted(d.record_key for d in candidate.dispositions
                                  if d.action=='create' and d.record_key.startswith('claims/')))
    path=root/'capacity-authority.json';path.write_text(json.dumps(value))
    return binding(path)


def test_default_is_still_ten():
    assert units.review_capacity(None, None, []) == 10


def test_original_eight_and_proposed_four_resume_with_only_unspent_opus(claims):
    from backend.api.canonical_repository.claim_identity_allocation import allocated_ids
    from backend.pipeline.claim_review_recovery import resume_saved_pre_review
    store, root = claims
    members=['C','D','E','F','G','H','I','J']
    store.apply_plan(store.plan_package({'package_id':'eight-originals',
        'claims':[{'claim_id':cid,'statement':'甲是假设。','claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['E'+cid]}for cid in members[2:]],
        'evidence_steps':[{'evidence_step_id':'E'+cid,'statement':'甲是假设。','produced_claim_ids':[cid],
            'source_fragment_ids':['FC'],'support_eligibility':'eligible_candidate',
            'speaker':'professor','stance':'asserted'}for cid in members[2:]]}))
    prepared=intake.prepare(members,root,instruction='Review eight and fill four source judgments',
        groups=[members],creation_count=4,policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    authority=json.loads(cm.source.verify_file(request.authorization).read_text())
    ids=allocated_ids(authority['claim_creation_allocations'])
    citation=dict(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')
    def review(packet):
        result=pass_review(packet)
        if not packet['unresolved']:
            result['findings']=[dict(target_key='evidence_steps/EC',component='independent_judgments',
                category='mapping',reason='synthetic four judgments need separate records',
                acceptance_condition='create four',citations=[citation],affected_target_keys=['claims/C'],
                affected_write_keys=[],impact_reason='own evidence')]
        else:
            assert len(packet['own_evidence_bindings'])==12
            assert packet['program_owned_fields'][units.CAPACITY_EXCEPTION]['maximum_claims']==12
            result['prior_findings']=[dict(finding_id=fid,disposition='resolved',reason='all four present',
                citations=[citation],affected_target_keys=['claims/C'],affected_write_keys=['evidence_steps/EC'],
                impact_reason='owners complete')for fid in packet['unresolved']]
        return result
    def propose(packet):
        changes=[dict(record_key='evidence_steps/E'+cid,
            fields_json=json.dumps(dict(statement='甲是假设，不是断言。',
                **({'produced_claim_ids':['C',*ids]}if cid=='C'else{}))),
            reason='synthetic full own evidence statement',citations=[citation])for cid in members]
        return dict(changes=changes,creations=[dict(collection='claims',object_id=cid,
            fields_json=json.dumps(dict(statement='synthetic judgment '+str(i),claim_type='exegesis',
                evidence_step_ids=['EC'],occurrences=[dict(source_id='S',anchors=[])])),
            reason='synthetic independent judgment',citations=[citation])for i,cid in enumerate(ids)],
            retirements=[],findings=[],finding_resolutions=[dict(finding_id=fid,disposition='resolved',
                reason='all four proposed',citations=[citation],affected_target_keys=['claims/C'],
                affected_write_keys=['evidence_steps/EC'],impact_reason='reciprocal owners complete')
                for fid in packet['unresolved']])
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='paused' and 'exceeds ten' in result['detail'],result
    assert ports.calls==['opus','gpt']
    parent_id='CMA-'+cm.sha256_json(request.action_id);parent=store.get_cvp_group(parent_id)
    rid=next(iter(parent['latest_rounds'].values()));saved=store.get_cvp_group(rid)
    calls=deepcopy(parent['call_reservations']);raw=(execution/rid/'gpt.raw.json').read_bytes()
    with pytest.raises(ValueError,match='exceeds ten'):
        resume_saved_pre_review(store,rid)
    bound=authorize(root,parent,execution/rid/'materials.json',execution/rid/'candidate.json')
    materials=deepcopy(saved['materials']);materials[units.CAPACITY_EXCEPTION]=bound
    candidate=cm.Candidate.model_validate(saved['candidate'])
    assert units.review_capacity(materials,candidate,request.target_keys+['claims/'+i for i in ids])==12
    for field in ('request','sources','before'):
        changed=deepcopy(materials)
        if field=='request':changed[field]['action_id']='foreign-action'
        else:changed[field]['foreign']={}
        with pytest.raises(ValueError,match='exact owner-authorized'):
            units.review_capacity(changed,candidate,[])
    with pytest.raises(ValueError,match='exact owner-authorized'):
        units.review_capacity(materials,candidate,['claims/foreign'])
    proof=resume_saved_pre_review(store,rid,review_capacity_authorization=bound)
    assert resume_saved_pre_review(store,rid,review_capacity_authorization=bound)==proof
    assert store.get_cvp_group(parent_id)['call_reservations']==calls
    assert (execution/rid/'gpt.raw.json').read_bytes()==raw
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    assert ports.calls==['opus','gpt','opus']
    assert set(store.get_cvp_group(parent_id)['created_keys'])=={'claims/'+i for i in ids}
