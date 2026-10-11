from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.pipeline import claim_evidence_completion as m
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import sha256_json


def material():
    direction={'schema_version':m.SCHEMA,'evidence_ids':['E1','E2'],
               'context_claim_ids':['C1','C2'],'unchanged_held_context_claim_ids':['C2'],
               'withdraw_relation_ids':['R1']}
    states={
        'evidence_steps/E1':{'evidence_step_id':'E1','statement':'age example','revision':3,
            'produced_claim_ids':['C1'],'support_eligibility':'withheld_missing_anchor','anchor_quality':'missing'},
        'evidence_steps/E2':{'evidence_step_id':'E2','statement':'dual mission','revision':3,
            'produced_claim_ids':['C2'],'support_eligibility':'withheld_missing_anchor','anchor_quality':'missing'},
        'claims/C1':{'claim_id':'C1','statement':'unchanged claim','revision':2,'evidence_step_ids':['E1'],
            'eligible_evidence_step_ids':[],'withheld_evidence_step_ids':['E1'],'occurrences':[]},
        'claims/C2':{'claim_id':'C2','statement':'held context','revision':2,'review_status':'human_review_required',
            'evidence_step_ids':['E2'],'eligible_evidence_step_ids':[],'withheld_evidence_step_ids':['E2'],'occurrences':[]},
        'knowledge_relations/R1':{'relation_id':'R1','from_id':'E1','to_id':'E2','relation_type':'supports','revision':1}}
    frozen={'claim_issue_context':{},'manifest':{'source_id':'SRC'},'before_records':c.rows(states)}
    after=deepcopy(states)
    for eid in ('E1','E2'):
        after['evidence_steps/'+eid].update(revision=4,source_fragment_id='F'+eid,
            anchor_quality='verified_candidate',support_eligibility='eligible_with_label')
        after['source_fragments/F'+eid]={'fragment_id':'F'+eid,'source_id':'SRC','anchor_state':'source_version_bound'}
    after['claims/C1'].update(revision=3,eligible_evidence_step_ids=['E1'],withheld_evidence_step_ids=[])
    return direction,frozen,after


@pytest.mark.parametrize('fault',[None,'missing_endpoint','statement','qualification','no_binding',
                                  'foreign_source','unbound_quote','claim_text','ownership','extra_bucket',
                                  'hold_cleared','held_bucket','relation_type','relation_endpoint'])
def test_joint_completion_requires_both_own_evidence_and_preserves_hold(monkeypatch,fault):
    d,f,post=material()
    monkeypatch.setattr(m,'direction_for',lambda *a,**kw:d)
    if fault=='missing_endpoint':post['evidence_steps/E2']=deepcopy(c.index(f['before_records'])['evidence_steps/E2'])
    if fault=='statement':post['evidence_steps/E1']['statement']='different teaching'
    if fault=='qualification':post['evidence_steps/E1']['support_eligibility']='context_only'
    if fault=='no_binding':post['evidence_steps/E1'].pop('source_fragment_id')
    if fault=='foreign_source':post['source_fragments/FE1']['source_id']='FOREIGN'
    if fault=='unbound_quote':post['source_fragments/FE1']['anchor_state']='unresolved'
    if fault=='claim_text':post['claims/C1']['statement']='changed claim'
    if fault=='ownership':post['evidence_steps/E1']['produced_claim_ids']=['C2']
    if fault=='extra_bucket':post['claims/C1']['eligible_evidence_step_ids'].append('OTHER')
    if fault=='hold_cleared':post['claims/C2']['review_status']='ai_consensus_reviewed'
    if fault=='held_bucket':post['claims/C2']['eligible_evidence_step_ids']=['E2']
    if fault=='relation_type':post['knowledge_relations/R1']['relation_type']='qualifies'
    if fault=='relation_endpoint':post['knowledge_relations/R1']['to_id']='C1'
    if fault:
        with pytest.raises(ValueError):m.validate_postimages(f,None,post)
    else:
        m.validate_postimages(f,None,post)
        assert not any(p.object_id=='C2' for p in m.extra_permissions(d,c.index(f['before_records'])))
        assert len(m.review_items(f))==2


def test_joint_withdrawal_requires_all_extra_independent_verdicts(monkeypatch):
    d,f,post=material()
    monkeypatch.setattr(m,'direction_for',lambda *a,**kw:d)
    packet={'proposal':{'dispositions':[{'record_key':'evidence_steps/E1'}]},
            'proposal_sha256':'a'*64,'claim_issue_context':{},
            'knowledge_relation_withdrawal_review':m.review_items(f)}
    verdicts=[{'record_key':k,'approved':True,'problems':[],'reason':'independent'} for k in
              ['evidence_steps/E1','knowledge_relation_withdrawal/R1','evidence_shared_owner/C2']]
    response={'proposal_sha256':'a'*64,'verdicts':verdicts}
    assert c.validate_review(packet,response)
    with pytest.raises(ValueError):c.validate_review(packet,{**response,'verdicts':verdicts[:-1]})
    bad=deepcopy(response);bad['verdicts'][1].update(approved=False,problems=['no source argument'])
    assert not c.validate_review(packet,bad)


def test_package_does_not_acquire_arbitrary_retirements(monkeypatch):
    d,f,_=material();monkeypatch.setattr(m,'direction_for',lambda *a,**kw:d)
    package={'source_repair_application':{'knowledge_relation_withdrawals':['R1']}}
    assert m.package_withdrawals(package,f)==(('knowledge_relations','R1'),)
    for wrong in ([],['R2'],['R1','R2'],['R1','R1']):
        with pytest.raises(ValueError):m.package_withdrawals({'source_repair_application':{'knowledge_relation_withdrawals':wrong}},f)


@pytest.mark.skipif(os.environ.get('WKP357_REAL_SAVED_READONLY')!='1',reason='actual production readonly opt-in')
@pytest.mark.parametrize('fault',[None,'budget','policy','owner_denominator','evidence_denominator','withdrawal','hold_disclosure','root'])
def test_actual_old_inputs_immutable_and_exact_new_scope(fault):
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.source_reconciliation_runner import read
    from backend.pipeline.claim_issue_rescope import validate_live
    p=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/owner-g111-joint-evidence-completion.json')
    d=read(p);root=Path(d['old_action_root'])
    hashes={file:c.file_sha256(file) for file in root.iterdir() if file.is_file()}
    if fault=='budget':d['additional_correction_limit']=2
    if fault=='policy':d['new_policy']={**d['new_policy'],'file_sha256':'f'*64}
    if fault=='owner_denominator':d['context_claim_ids'].remove('CL-0007')
    if fault=='evidence_denominator':d['evidence_ids'].append('L3-E045')
    if fault=='withdrawal':d['withdraw_relation_ids']=['AR-0043']
    if fault=='hold_disclosure':d['unchanged_held_context_claim_ids']=[]
    if fault=='root':d['old_action_id']='another'
    d['artifact_sha256']=sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})
    if fault:
        with pytest.raises((ValueError,KeyError)):validate_live(PostgresKnowledgeStore(),d)
    else:
        frozen,_=validate_live(PostgresKnowledgeStore(),d)
        assert len(frozen['sql_scope']['read_pins'])==268
    assert hashes=={file:c.file_sha256(file) for file in root.iterdir() if file.is_file()}
