"""Context classifications are reviewable, not self-approving or replayable."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_issue_management as m
from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline.source_reconciliation_runtime import model_adapter
from backend.tests.test_claim_issue_management import (
    issue_setup, issue_for, materials, retain_proposal, IssuePorts,
)
from backend.tests.test_source_reconciliation import scenario, put
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import postgres_store, _temporary_postgres
from backend.pipeline.cvp_sql_actions import execute_source_action


def eligibility_setup(issue_setup, tmp_path):
    manifest, states, report, binding = issue_setup
    states = deepcopy(states)
    states['evidence_steps/E-test'].update(
        support_eligibility='context_only', valid_for_identity_review=False,
        speaker='professor', stance='asserted', discourse_role='dramatic_paraphrase_of_Jesus')
    policy = m.read_policy(binding).model_copy(update={'evidence_eligibility_review': True})
    binding = put(tmp_path/'eligibility-policy.json', policy.model_dump())
    return manifest, states, report, binding


def eligibility_proposal(frozen, issue):
    proposal = retain_proposal(frozen, issue, 'evidence_binding')
    payload = deepcopy(c.index(frozen['before_records'])['evidence_steps/E-test'])
    payload.update(support_eligibility='eligible_with_label', valid_for_identity_review=True,
                   revision=payload['revision']+1, review_status='candidate')
    proposal.records = [c.Row(collection='evidence_steps', object_id='E-test', payload=payload)]
    next(d for d in proposal.dispositions if d.record_key=='evidence_steps/E-test').action='update'
    return proposal


def test_old_policy_and_old_prompt_contract_are_not_upgraded(issue_setup):
    _, states, _, binding = issue_setup
    states['evidence_steps/E-test'].update(support_eligibility='context_only', speaker='professor', stance='asserted')
    assert not m.read_policy(binding).evidence_eligibility_review
    assert not any(p.object_id=='E-test' for p in m.policy_permissions(m.read_policy(binding), states, 'SRC-test', ['CL-test']))
    old = model_adapter('proposal', issue=True)
    # Pre-change offline adapter hash; runtime response binding additionally
    # removes program-owned SHA instructions without changing the old prompt file.
    assert old.prompt_sha256 == '95b8c06ed950e73d889e50f1cda690d0a17f780fe7ed4cae88d2a895717ddcb9'
    new = model_adapter('proposal', issue=True, evidence_eligibility=True)
    assert new.prompt_sha256 != old.prompt_sha256
    assert new._response_model is old._response_model
    assert (new.model_id,new.backend)==('gpt-6.1-sol','codex_subscription')
    with pytest.raises(ValueError): model_adapter('proposal', evidence_eligibility=True)


def test_exact_label_change_can_enter_independent_review_without_changing_claim(issue_setup,tmp_path):
    setup = eligibility_setup(issue_setup,tmp_path)
    manifest, frozen, issue = materials(setup,tmp_path)
    permission = next(p for p in manifest.permissions if p.object_id=='E-test')
    assert set(permission.fields)==m.ELIGIBILITY_FIELDS
    before = c.index(frozen['before_records'])
    actual = c.validate_proposal(frozen, eligibility_proposal(frozen,issue))
    assert actual['claims/CL-test']==before['claims/CL-test']
    assert actual['source_fragments/FR-test']==before['source_fragments/FR-test']
    assert actual['evidence_steps/E-test']['discourse_role']=='dramatic_paraphrase_of_Jesus'
    assert before['evidence_steps/E-test']['support_eligibility']=='context_only'
    assert m.eligibility_profile(r.body(r.build_proposal_packet(frozen)))


@pytest.mark.parametrize('bound_target', [True, False])
def test_qualification_only_own_body_diagnostics_do_not_become_new_evidence(issue_setup, tmp_path, bound_target):
    _, states, report, policy = eligibility_setup(issue_setup, tmp_path)
    own = put(tmp_path/'diagnostic-body.json', {'script': [{'text': '甲是假设。完整背景。'}]})
    snapshot = c.source_snapshot(own)
    states['source_documents/SRC-test'].update(source_path=own['path'], source_file_sha256=own['file_sha256'],
        source_body_sha256=snapshot['body_sha256'], source_sha256=snapshot['body_sha256'])
    states['source_fragments/FR-test']['source_sha256'] = snapshot['body_sha256']
    _, frozen, issue = materials((None, states, report, policy), tmp_path)
    proposal = eligibility_proposal(frozen, issue)
    cite = m.Citation(source_id='SRC-test', paragraph_key='S0001', exact_quote='完整背景。')
    proposal.claim_assessments[0].citations = ([proposal.claim_assessments[0].citations[0], cite]
                                             if bound_target else [cite])
    if bound_target:
        post = c.validate_proposal(frozen, proposal)
        assert post['source_fragments/FR-test'] == c.index(frozen['before_records'])['source_fragments/FR-test']
    else:
        with pytest.raises(ValueError, match='actual eligible bound Evidence'): c.validate_proposal(frozen, proposal)


@pytest.mark.parametrize('field,value',[
    ('review_status','human_review_required'),('review_status','human_hold'),
    ('review_status','rejected'),('review_status','human_approved'),
    ('support_eligibility','withheld_unreviewed'),('speaker','audience'),
    ('speaker','opponent'),('stance','opposed'),
])
def test_protected_or_nonasserted_evidence_is_not_given_qualification_permission(issue_setup,tmp_path,field,value):
    _,states,_,binding=eligibility_setup(issue_setup,tmp_path)
    states['evidence_steps/E-test'][field]=value
    permissions=m.policy_permissions(m.read_policy(binding),states,'SRC-test',['CL-test'])
    assert not any(p.object_id=='E-test' for p in permissions)


@pytest.mark.parametrize('mutation',['statement','role','speaker','quote','foreign','missing','shared','cause','eligible_unlabelled'])
def test_label_review_cannot_smuggle_content_or_skip_shared_owner(issue_setup,tmp_path,mutation):
    setup=eligibility_setup(issue_setup,tmp_path)
    _,frozen,issue=materials(setup,tmp_path)
    proposal=eligibility_proposal(frozen,issue)
    payload=proposal.records[0].payload
    if mutation=='statement':payload['statement']='another assertion'
    elif mutation=='role':payload['discourse_role']='scripture_verbatim'
    elif mutation=='speaker':payload['speaker']='Jesus'
    elif mutation=='quote':proposal.claim_assessments[0].citations[0].exact_quote='invented'
    elif mutation in {'foreign','missing'}:
        before=c.index(frozen['before_records'])
        fragment=before['source_fragments/FR-test']
        if mutation=='foreign':fragment['source_id']='foreign'
        else:fragment['verbatim_excerpt']=''
        frozen['before_records']=c.rows(before)
    elif mutation=='shared':
        before=c.index(frozen['before_records']);before['evidence_steps/E-test']['produced_claim_ids'].append('OTHER')
        frozen['before_records']=c.rows(before)
        payload['produced_claim_ids'].append('OTHER')
    elif mutation=='cause':proposal.claim_assessments[0].cause='already_sufficient'
    else:payload['support_eligibility']='eligible_candidate'
    with pytest.raises(ValueError):
        m.validate_resolution(frozen,proposal,{**c.index(frozen['before_records']),'evidence_steps/E-test':payload})


@pytest.mark.parametrize('quote,claim,refs',[
    ('你真的有心要知道的話，我就給你看。','耶穌重視具有真實求知道動機的神蹟，但拒絕為看秀的人表演神蹟。',[]),
    ('「你們這一個不信的世代，我要一個神蹟給你們看，將來我要死，被你們抓去釘十字架，三天之後我要復活。」',
     '耶穌只給不信的世代約拿的神蹟；教授將此解釋為耶穌被釘十字架後第三天復活。',['馬太福音12:40','馬太福音16:4']),
])
def test_saved_e035_e041_shape_is_reviewable_not_automatic_approval(issue_setup,tmp_path,quote,claim,refs):
    _,states,report,binding=eligibility_setup(issue_setup,tmp_path)
    own=put(tmp_path/'own.json',{'script':[{'text':quote}]})
    snapshot=c.source_snapshot(own)
    states['source_documents/SRC-test'].update(source_path=own['path'],source_file_sha256=own['file_sha256'],
        source_body_sha256=snapshot['body_sha256'],source_sha256=snapshot['body_sha256'])
    states['source_fragments/FR-test'].update(verbatim_excerpt=quote,source_sha256=snapshot['body_sha256'])
    states['claims/CL-test']['statement']=claim
    states['evidence_steps/E-test'].update(statement='教授戲劇化代言耶穌作解釋。',scripture_refs=refs)
    _,frozen,issue=materials((None,states,report,binding),tmp_path)
    proposal=eligibility_proposal(frozen,issue)
    proposal.claim_assessments[0].citations[0].exact_quote=quote
    post=c.validate_proposal(frozen,proposal)
    assert post['claims/CL-test']['statement']==claim
    assert post['evidence_steps/E-test']['speaker']=='professor'
    assert not m.read_policy(binding).human_content_approval
    # Passing program validation constructs the review packet, not a pass verdict.
    assert 'review' not in c.review_packet(frozen,proposal)


class EligibilityPorts(IssuePorts):
    def claim_issue_adapter(self,stage,*,evidence_eligibility=False):
        assert evidence_eligibility
        adapter=model_adapter(stage,issue=True,evidence_eligibility=True)
        def generate(packet):
            self.calls.append(stage)
            if 'review' in stage:
                return {'verdicts':[{'record_key':d['record_key'],'approved':not self.refuse,
                    'reason':'exact label and attribution reviewed','problems':['拒绝'] if self.refuse else []}
                    for d in packet['proposal']['dispositions']]}
            if stage=='correction':return deepcopy(packet['initial_proposal'])
            frozen={'inventory':packet['inventory'],'before_records':packet['records']}
            p=eligibility_proposal(frozen,self.issue)
            return {**p.model_dump(exclude={'records','unadmitted_candidates'}),
                'records':[{'collection':x.collection,'object_id':x.object_id,'payload_json':json.dumps(x.payload)} for x in p.records],
                'unadmitted_candidates_json':[]}
        adapter.generate=generate
        return adapter


def test_eligibility_review_sql_late_rollback_then_saved_resume_no_model_replay(postgres_store,issue_setup,tmp_path,monkeypatch):
    manifest,states,report,policy=eligibility_setup(issue_setup,tmp_path)
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scope')
    ports=EligibilityPorts(issue)
    monkeypatch.setattr('backend.pipeline.cvp_sql_commit.verified_group_backup',lambda *args:{'dump':report,'restore_catalog_nonempty':True})
    before=deepcopy(postgres_store.get_record_state('evidence_steps','E-test'))
    original=postgres_store._assert_transaction_edges_and_reviews
    def fail(*args):raise ValueError('late eligibility transaction failure')
    monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',fail)
    with pytest.raises(ValueError,match='late eligibility transaction'):
        execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert postgres_store.get_record_state('evidence_steps','E-test')==before
    monkeypatch.setattr(postgres_store,'_assert_transaction_edges_and_reviews',original)
    result=execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert ports.calls==['proposal','initial_review']
    assert len(postgres_store.get_change_history(result['change_set_id'])['operations'])==1
    assert postgres_store.get_record('evidence_steps','E-test')['support_eligibility']=='eligible_with_label'
    assert postgres_store.get_record('claims','CL-test')==states['claims/CL-test']
    assert execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)['status']=='already_applied'
    assert ports.calls==['proposal','initial_review']


def test_eligibility_terminal_refusal_retained_no_second_correction(postgres_store,issue_setup,tmp_path):
    manifest,states,report,policy=eligibility_setup(issue_setup,tmp_path)
    run=source_run(postgres_store,(manifest,states,{}));issue=issue_for(states,report)
    postgres_store.register_claim_issue(run,issue.model_dump(mode='json'))
    scoped=m.manifest_for_issues(postgres_store,run,[issue],policy,tmp_path/'scope')
    ports=EligibilityPorts(issue,refuse=True)
    for _ in range(2):
        with pytest.raises(r.Stop,match='terminal Opus'):
            execute_source_action(postgres_store,run,scoped.model_dump(),root=tmp_path/'action',ports=ports)
    assert ports.calls==['proposal','initial_review','correction','final_review']
    assert postgres_store.get_record('evidence_steps','E-test')['support_eligibility']=='context_only'


def test_new_production_policy_authorizes_review_not_human_content_approval():
    path=Path(__file__).resolve().parents[1]/'pipeline/policies/wang_claim_issue_management_eligibility_v2.json'
    policy=m.Policy.model_validate_json(path.read_text())
    assert policy.evidence_eligibility_review and not policy.human_content_approval
    assert set(policy.evidence_fields)==m.EVIDENCE_FIELDS and not policy.cvp_permissions


def test_unknown_eligibility_call_cannot_be_retried_by_restart(issue_setup,tmp_path):
    setup=eligibility_setup(issue_setup,tmp_path)
    _,frozen,issue=materials(setup,tmp_path)
    class UnknownPorts(EligibilityPorts):
        def claim_issue_adapter(self,stage,**kwargs):
            adapter=super().claim_issue_adapter(stage,**kwargs)
            def fail(packet):
                self.calls.append(stage)
                raise RuntimeError('transport outcome unknown')
            adapter.generate=fail
            return adapter
    ports=UnknownPorts(issue)
    stages=r.Runner(tmp_path/'unknown',ports)
    stages.root=tmp_path/'unknown'
    stages.root.mkdir()
    packet=r.build_proposal_packet(frozen)
    with pytest.raises((r.Stop,RuntimeError)):stages.call('proposal',packet)
    with pytest.raises((r.Stop,ValueError)):stages.call('proposal',packet)
    assert ports.calls==['proposal']


@pytest.mark.skipif(__import__('os').environ.get('WKP_G094_ELIGIBILITY_READONLY')!='1',reason='explicit saved scoped g094 read-only acceptance')
def test_actual_saved_g094_e035_e041_can_receive_exact_qualification_permissions():
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution/CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83')
    action=next(root.glob('claim-issues/*/action/*'))
    frozen=json.loads((action/'scoped-materials.json').read_text())
    states=c.index(frozen['before_records']);saved=deepcopy(states)
    sid=frozen['manifest']['source_id']
    source=states[c.key('source_documents',sid)]
    c.source_snapshot(c.FileBinding(path=source['source_path'],file_sha256=source['source_file_sha256']),source=source)
    policy_path=Path(__file__).resolve().parents[1]/'pipeline/policies/wang_claim_issue_management_eligibility_v2.json'
    policy=m.Policy.model_validate_json(policy_path.read_text())
    ids=frozen['claim_issue_context']['claim_ids']
    permissions=m.policy_permissions(policy,states,sid,ids)
    for suffix in ['E035','E041']:
        oid='DK-a26f0a7e9ba4-P02-'+suffix
        before=states[c.key('evidence_steps',oid)]
        assert before['support_eligibility']=='context_only'
        assert before['speaker']=='professor' and before['stance']=='asserted'
        assert before['discourse_role']=='dramatic_paraphrase_of_Jesus'
        assert set(next(p for p in permissions if p.object_id==oid).fields)==m.ELIGIBILITY_FIELDS
    assert states==saved
    # Original complete answer and its unresolved connections are not edited
    # or magically accepted under this new policy.
    raw=json.loads((action/'proposal.raw.json').read_text())
    assert raw['response']['records']==[]
    assert any(d['action']=='unresolved' for d in raw['response']['dispositions'])
