from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import claim_first_management as m, source_reconciliation as c


@pytest.fixture
def draft(monkeypatch):
    monkeypatch.setattr(c, 'validate_proposal', lambda *a, **kw: {})
    rows = [c.Row(collection='evidence_steps', object_id='E1', payload={'evidence_step_id':'E1'}),
            c.Row(collection='viewpoint_claim_links', object_id='V1', payload={'viewpoint_claim_link_id':'V1'})]
    proposal = SimpleNamespace(records=rows,
        dispositions=[c.Disposition(record_key=r.key, action='update', reason='original') for r in rows]
            + [c.Disposition(record_key='canonical_viewpoints/CV1', action='unresolved', reason='original finding')],
        cvp_impacts=[SimpleNamespace(action='update', reason='original')])
    return {'manifest':{'claim_issue':{'fixture':True}}}, proposal


def test_projects_only_source_records_without_mutating_original(draft):
    frozen, original=draft; before=deepcopy(original)
    projected=m.project(frozen,original)
    assert [r.key for r in projected.records]==['evidence_steps/E1']
    assert all(d.action=='unresolved' for d in projected.dispositions if c.split_key(d.record_key)[0] in m.CVP_COLUMNS)
    assert projected.cvp_impacts[0].action=='needs_direction'
    assert original.__dict__==before.__dict__


@pytest.mark.parametrize('change',['no_issue','no_source_repair','foreign_write','non_cvp_omission'])
def test_projection_cannot_expand_or_silently_drop_writes(draft,change):
    frozen,original=draft
    if change=='no_issue':frozen['manifest'].pop('claim_issue')
    elif change=='no_source_repair':original.records=original.records[1:]
    elif change=='foreign_write':original.records.append(c.Row(collection='source_documents',object_id='S',payload={}))
    else:original.dispositions.append(c.Disposition(record_key='observations/O',action='update',reason='not source-owned'))
    with pytest.raises(ValueError):m.project(frozen,original)


def answer_packet():
    packet={'proposal_sha256':'a'*64,'proposal':{'dispositions':[{'record_key':'evidence_steps/E1'},
        {'record_key':'canonical_viewpoints/CV1'}]},
        'source_first_pending_cvp':{'pending_record_keys':['canonical_viewpoints/CV1']}}
    answer={'proposal_sha256':'a'*64,'verdicts':[
        {'record_key':'evidence_steps/E1','approved':True,'problems':[],'reason':'source approved'},
        {'record_key':'canonical_viewpoints/CV1','approved':False,'problems':[m.PENDING_PROBLEM],'reason':'CVP unreviewed/pending'}]}
    return packet,answer


def test_source_approval_is_not_cvp_approval():
    packet,answer=answer_packet(); assert c.validate_review(packet,answer)
    assert answer['verdicts'][1]['approved'] is False


@pytest.mark.parametrize('change',['source_refused','source_problem','cvp_pass','cvp_other_refusal','missing','duplicate','foreign_id'])
def test_complete_review_denominator_and_pending_states_are_strict(change):
    packet,answer=answer_packet()
    if change=='source_refused':answer['verdicts'][0]['approved']=False
    elif change=='source_problem':answer['verdicts'][0]['problems']=['real problem']
    elif change=='cvp_pass':answer['verdicts'][1].update(approved=True,problems=[])
    elif change=='cvp_other_refusal':answer['verdicts'][1]['problems']=['semantic contradiction']
    elif change=='missing':answer['verdicts'].pop()
    elif change=='duplicate':answer['verdicts'].append(answer['verdicts'][0])
    else:answer['verdicts'][0]['record_key']='claims/FOREIGN'
    try:assert not c.validate_review(packet,answer)
    except ValueError:pass


def test_existing_other_final_reservation_cannot_be_replaced(tmp_path):
    (tmp_path/'final_review.reservation.json').write_text('{}')
    from backend.pipeline.source_reconciliation_runner import write
    write(tmp_path/'final_review.packet.json',{'different_contract':True})
    with pytest.raises(ValueError,match='already reserved'):
        m.final_packet(tmp_path,{},None,None)


@pytest.mark.parametrize('kind',['plain','sealed','drifted_seal'])
def test_original_manifest_envelope_is_verified_before_business_validation(tmp_path,kind):
    import json
    from backend.pipeline.cvp_sql_actions import read_source_manifest
    from backend.pipeline.source_reconciliation_runner import write
    path=tmp_path/'manifest.json';value={'schema_version':'fixture','source_id':'unchanged'}
    if kind=='plain':path.write_text(json.dumps(value))
    else:
        write(path,value)
        if kind=='drifted_seal':
            changed=json.loads(path.read_text());changed['source_id']='foreign';path.write_text(json.dumps(changed))
    if kind=='drifted_seal':
        with pytest.raises(ValueError,match='seal drift'):read_source_manifest(path)
    else:assert read_source_manifest(path)==value


def test_new_prompt_profile_preserves_old_adapter_identity():
    from backend.pipeline.source_reconciliation_runtime import model_adapter
    adapter=model_adapter('final_review',issue=True,evidence_eligibility=True)
    original=adapter.prompt_sha256
    assert m.scoped_adapter(adapter,{}) is adapter
    scoped=m.scoped_adapter(adapter,{'manifest':{'claim_issue':True},'claim_first_management':{'schema_version':m.VERSION}})
    assert adapter.prompt_sha256==original and scoped.prompt_sha256!=original
    assert scoped.model_id==adapter.model_id and scoped.backend==adapter.backend
    assert scoped.generation_config_sha256==adapter.generation_config_sha256


@pytest.mark.parametrize('change',[None,'cvp_layer','wrong_model','missing','duplicate','disputed','quota','blocked','shortened','not_complete','mechanical_error'])
def test_source_claim_audit_never_completes_cvp_or_accepts_partial_claims(change):
    report={'meta':{'model':'gemini-3.8-flash','registry_read_scope':{
        'kind':'sql_transaction_group','complete_target_members':True,'claim_ids':['CL1','CL2'],'viewpoint_ids':[]}},
        'layers':{'1':{'counts':{'pass':2},'findings':[]},'2':{},'3':{'complete':True,'judged':2,
            'results':[{'claim_id':'CL1','verdict':'supported'},{'claim_id':'CL2','verdict':'supported'}]}}}
    if change=='cvp_layer':report['layers']['4']={'complete':True,'results':[]}
    elif change=='wrong_model':report['meta']['model']='different'
    elif change=='missing':report['layers']['3']['results'].pop()
    elif change=='duplicate':report['layers']['3']['results'][1]['claim_id']='CL1'
    elif change=='disputed':report['layers']['3']['results'][0]['verdict']='disputed'
    elif change=='quota':report['layers']['3']['model_errors']=1
    elif change=='blocked':report['layers']['3']['blocked']=1
    elif change=='shortened':report['layers']['3']['results'][0]['paragraphs_dropped_to_pass_filter']=1
    elif change=='not_complete':report['layers']['3']['complete']=False
    elif change=='mechanical_error':report['layers']['2']['dangling']=['missing']
    if change is None:m.validate_claim_audit_report(report,['CL1','CL2'])
    else:
        with pytest.raises(ValueError):m.validate_claim_audit_report(report,['CL1','CL2'])
