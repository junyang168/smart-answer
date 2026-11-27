"""Shared support consumers are frozen references, never more review roots."""
from copy import deepcopy
import json

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch, claim_review_reuse as reuse
from backend.pipeline.claim_direct_scope import shared_support_owner_references
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file


def test_consumer_lookup_follows_argument_chains_but_stops_at_claims():
    def state(payload, retired=False):
        return {'payload': payload, 'retired': retired}
    request=cm.Request(action_id='shared', operation='Update', allowed_operations=['Update'],
        target_keys=['claims/A'],source_bindings={'S':{'path':'/source','file_sha256':'a'*64}},
        policy={'path':'/policy','file_sha256':'b'*64},
        authorization={'path':'/auth','file_sha256':'0'*64})
    rows={}
    for cid in ('A','B','C','Z','T','R'):
        rows['claims/'+cid]=state({'claim_id':cid,'statement':cid,
            'occurrences':[{'source_id':'T' if cid=='T' else 'S'}],
            'evidence_step_ids':['E'+cid]+(['PRIVATE'] if cid=='B' else [])},retired=cid=='R')
        rows['evidence_steps/E'+cid]=state({'evidence_step_id':'E'+cid,'statement':cid,
            'produced_claim_ids':[cid],'source_fragment_ids':['FT' if cid=='T' else 'F']})
    rows.update({'source_documents/S':state({'source_id':'S'}),
        'source_documents/T':state({'source_id':'T'}),
        'source_fragments/F':state({'fragment_id':'F','source_id':'S'}),
        'source_fragments/FT':state({'fragment_id':'FT','source_id':'T'}),
        'source_fragments/FP':state({'fragment_id':'FP','source_id':'S'}),
        'observations/O':state({'observation_id':'O','statement':'source judgment','source_fragment_ids':['F']}),
        'evidence_steps/X':state({'evidence_step_id':'X','statement':'intermediate'}),
        'evidence_steps/Y':state({'evidence_step_id':'Y','statement':'intermediate'}),
        'evidence_steps/PRIVATE':state({'evidence_step_id':'PRIVATE','statement':'private',
            'produced_claim_ids':['B','Z'],'source_fragment_ids':['FP']}),
        'claim_relations/BC':state({'claim_relation_id':'BC','from_id':'B','to_id':'C','relation_type':'supports'})})
    # C is connected only through Claim B; do not traverse B's outgoing
    # knowledge relation or its private support while discovering users.
    for ident,left,right,retired in [('OX','O','X',False),('XY','X','Y',False),
            ('YX','Y','X',False),('YB','Y','EB',False),('BC','B','EC',False),
            ('OT','O','ET',False),('OR','O','ER',False),('OC','O','EC',True)]:
        rows['knowledge_relations/'+ident]=state({'relation_id':ident,'from_id':left,
            'to_id':right,'relation_type':'supports'},retired)
    saved=deepcopy(rows)
    refs=shared_support_owner_references(request,rows,{'observations/O'})
    assert set(refs)=={'claims/B'}
    refs['claims/B']['payload']['statement']='forged'
    assert rows==saved
    assert shared_support_owner_references(request,rows,{'claims/B','claim_relations/BC'})=={}
    rows['evidence_steps/EB']['payload']['produced_claim_ids'].append('MISSING')
    with pytest.raises(ValueError,match='shared support owner missing'):
        shared_support_owner_references(request,rows,{'observations/O'})


@pytest.mark.parametrize('defect',[None,'statement','remove_old','unreviewed_fragment','changed_fragment'])
def test_new_intake_carries_shared_observation_owner_and_only_safe_proofs_continue(claims,defect):
    store,root=claims
    observations=[{'observation_id':'O','statement':'甲是假设。','source_fragment_ids':['FD']}]
    relations=[{'relation_id':'OC','from_id':'O','to_id':'EC','relation_type':'supports'},
        {'relation_id':'OX','from_id':'O','to_id':'X','relation_type':'supports'},
        {'relation_id':'XD','from_id':'X','to_id':'ED','relation_type':'supports'}]
    if defect!='unreviewed_fragment':
        observations.append({'observation_id':'O2','statement':'甲是假设。','source_fragment_ids':['FC']})
        relations.append({'relation_id':'O2D','from_id':'O2','to_id':'ED','relation_type':'supports'})
    store.apply_plan(store.plan_package({'package_id':'shared-observation-users',
        'observations':observations,'knowledge_relations':relations,
        'evidence_steps':[{'evidence_step_id':'X','statement':'甲是假设。'}],
        'claims':[{'claim_id':'B','statement':'甲是假设。','claim_type':'exegesis','occurrences':[{'source_id':'S'}]}],
        'claim_relations':[{'claim_relation_id':'DB','from_id':'D','to_id':'B','relation_type':'supports'}]}))
    path=policy_file(root.parent)
    old_prepared=intake.prepare(['D'],root/'old',instruction='Review D complete own basis',
        groups=[['D']],policy=path,store=store)
    _,jobs=batch.load(old_prepared['manifest']['path'],root/'old/batch',store=store)
    request,execution=next(iter(jobs.values()));initial=SyntheticPorts(store,execution,opus=pass_review)
    assert execute_claim_management(store,request,root=execution,ports=initial)['status']=='completed'
    old=store.claim_management_qualification('D')['run_id'];proof=reuse.admit(store,'D',old)
    assert ('source_fragments/FC' in proof['protected_record_keys'])==(defect!='unreviewed_fragment')
    original=store.get_record_state('claims','D')
    policy=cm.Policy.model_validate_json(path.read_text());policy.initial_candidate='proposal'
    path.write_text(policy.model_dump_json())
    prepared=intake.prepare(['C'],root/'peer',instruction='Update C and shared Observation only',
        groups=[['C']],policy=path,store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'peer/batch',store=store)
    request,execution=next(iter(jobs.values()))
    authority=json.loads(cm.source.verify_file(request.authorization).read_text())
    assert 'claims/D' in authority['evidence_owner_context_keys']
    assert not any(p.key in {'claims/D','evidence_steps/ED','observations/O2'} for p in request.permissions)
    def check(packet):
        assert 'claims/D' in packet['before'] and 'evidence_steps/ED' in packet['before']
        assert 'claims/D' in packet['claim_scope']['neighbour_claim_keys']
        assert packet['claim_scope']['content_claim_keys']==['claims/C']
        assert set(packet.get('own_evidence_bindings',{'claims/C':{}}))=={'claims/C'}
        assert not {'claims/B','claim_relations/DB'} & set(packet['before'])
    citation={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def propose(packet):
        check(packet)
        fields={'source_fragment_ids':['FC'] if defect=='remove_old' else ['FD','FC']}
        if defect=='statement':fields['statement']='另一個判斷。'
        changes=[{'record_key':'observations/O','fields_json':json.dumps(fields),
            'reason':'add current source binding while preserving the original observation','citations':[citation]}]
        if defect=='changed_fragment':changes.append({'record_key':'source_fragments/FC',
            'fields_json':json.dumps({'verbatim_excerpt':'甲是假设。'}),
            'reason':'different literal source span','citations':[citation]})
        return {'changes':changes,'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    def review(packet):
        check(packet)
        return pass_review(packet)
    ports=SyntheticPorts(store,execution,proposal=propose,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    peer=store.claim_management_qualification('C')['run_id']
    assert store.get_record_state('claims','D')==original
    if defect:
        with pytest.raises(ValueError):reuse.admit(store,'D',old,peer_run_id=peer)
        q=store.claim_management_qualification('D');assert not q or not q.get('ready')
    else:
        assert store.claim_management_qualification('D')['ready']
        proof=store.get_cvp_group(old)['readonly_claim_qualifications']['D']
        assert 'observations/O' in proof['context_reviews']
        assert store.get_cvp_group(peer)['reference_qualification_handoffs'][0]['status']=='current'
        with pytest.raises(ValueError):reuse.build(store,'D',old)  # No peer approval, no handoff.
        with store.connect() as conn,conn.cursor() as cursor:
            assert reuse.revalidate_context(store,cursor,'D',old,peer)==proof
    assert initial.calls==['opus'] and ports.calls==['gpt','opus']
