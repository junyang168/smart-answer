"""Reuse complete approvals across an actual, separately reviewed neighbour delta."""
import json

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake
from backend.pipeline import claim_management_batch as batch, claim_review_reuse as reuse
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
from backend.tests.test_claim_review_units import policy_file


def original_pass(store, root):
    store.apply_plan(store.plan_package({'package_id':'connected', 'claim_relations':[
        {'claim_relation_id':'R','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    prepared = intake.prepare(['C'], root/'old', instruction='Review existing content', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'old'/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    ports = SyntheticPorts(store, execution, opus=pass_review)
    assert execute_claim_management(store, request, root=execution, ports=ports)['status']=='completed'
    rid = store.claim_management_qualification('C')['run_id']
    reuse.admit(store, 'D', rid)
    assert store.claim_management_qualification('D')['ready']
    return rid


def update_neighbour(store, root, *, objection=False, bounded=True, refs='馬太福音13:1-52', own_issue=False, shared=False, question=False):
    path = policy_file(root.parent)
    policy = cm.Policy.model_validate_json(path.read_text())
    policy.initial_candidate = 'proposal'
    if not bounded:
        policy.review_unit_contract = None
    path.write_text(policy.model_dump_json())
    prepared = intake.prepare(['C'], root/'peer', instruction='Correct C; retain D as a reference',
                              groups=[['C']] if bounded else None, policy=path, store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root/'peer'/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    if shared or question:
        from backend.tests.test_claim_management_human_source_follow_up import authorize
        authority=json.loads(cm.source.verify_file(request.authorization).read_text())
        permission=cm.source.Permission(collection='questions' if question else 'evidence_steps',
            object_id='Q' if question else 'ED',fields=['answer_claim_ids'] if question else ['produced_claim_ids'])
        existing=next((p for p in request.permissions if p.key==permission.key),None)
        if existing is None:
            request.permissions.append(permission)
        else:
            assert set(permission.fields)<=set(existing.fields)
        authorize(request,{k:v for k,v in authority.items() if k not in {'request','policy'}})
    def propose(packet):
        changes=[{'record_key':'claims/C',
            'fields_json':json.dumps({'scripture_refs':[refs], **({'evidence_step_ids':['EC','ED']} if shared else {})}),
            'reason':'synthetic reviewed scope correction',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}]
        if shared:
            changes.append({**changes[0],'record_key':'evidence_steps/ED',
                            'fields_json':json.dumps({'produced_claim_ids':['D','C']})})
        if question:
            changes.append({**changes[0],'record_key':'questions/Q',
                            'fields_json':json.dumps({'answer_claim_ids':question if isinstance(question,list) else ['D','C']})})
        return {'changes':changes,
            'creations':[], 'retirements':[], 'findings':[], 'finding_resolutions':[]}
    def review(packet):
        if bounded:
            assert list(packet['own_evidence_bindings'])==['claims/C']
            assert 'claims/D' in packet['claim_scope']['neighbour_claim_keys']
        result = pass_review(packet)
        if objection or own_issue:
            result['findings']=[{'target_key':'evidence_steps/EC' if own_issue else 'claims/D','component':'synthetic_reference_objection',
                'category':'content','reason':'unresolved reference concern','acceptance_condition':'adjudicate D',
                'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
                'affected_target_keys':[],'affected_write_keys':[], 'impact_reason':'unrelated to C only'}]
        return result
    ports = SyntheticPorts(store, execution, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status']=='completed', result
    rid = store.claim_management_qualification('C')['run_id']
    assert ports.calls==['gpt','opus']
    return rid, ports


def test_old_green_peer_with_unresolved_own_data_cannot_certify_a_handoff(claims,monkeypatch):
    store,root=claims
    original_pass(store,root)
    actual=cm.finding_blocks
    with monkeypatch.context() as patch:
        patch.setattr(cm,'finding_blocks',lambda finding,*a,**kw:
                      False if finding['target_key']=='evidence_steps/EC' else actual(finding,*a,**kw))
        peer,_=update_neighbour(store,root,own_issue=True)
    from backend.pipeline.claim_qualification_reuse import evidence
    with pytest.raises(ValueError,match='own-data issue'):
        evidence(store.get_cvp_group(peer))
    q=store.claim_management_qualification('D')
    assert not q or not q.get('ready')


def test_neighbour_delta_preserves_complete_reference_approval_without_re_review(claims):
    store, root = claims
    old = original_pass(store, root)
    peer, ports = update_neighbour(store, root)
    q = store.claim_management_qualification('D')
    assert q['ready'] and q['run_id']==old
    proof = store.get_cvp_group(old)['readonly_claim_qualifications']['D']
    assert proof['contract']==reuse.CONTEXT_CONTRACT
    assert list(proof['context_reviews'])==['claims/C']
    assert proof['context_reviews']['claims/C']['round_id']==peer
    assert store.get_cvp_group(peer)['reference_qualification_handoffs'][0]['status']=='current'
    with store.connect() as conn, conn.cursor() as cursor:
        assert reuse.revalidate_context(store, cursor, 'D', old, peer)==proof
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('change,allowed', [
    ({'paragraph_key':'S0058', 'media_time':1021.32, 'source_sha256':'new'}, True),
    ({'verbatim_excerpt':'甲是断言。'}, False),
    ({'source_id':'OTHER'}, False),
    ({'speaker':'audience'}, False),
    ({'extraction_section_index':9}, False),
])
def test_literal_fragment_handoff_allows_only_locator_repairs(change, allowed):
    before={'source_id':'S', 'verbatim_excerpt':'甲是假设，不是断言。',
            'paragraph_key':'S0001', 'media_time':0}
    assert reuse.literal_fragment_rebinding(before, {**before, **change}) is allowed


@pytest.mark.parametrize('changed_words',[False,True])
def test_reviewed_literal_locator_repair_preserves_an_existing_shared_owner_pass(claims,changed_words):
    store,root=claims
    ec=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'shared-literal',
        'evidence_steps':[{**ec,'source_fragment_ids':['FC','FD']}]}))
    old=original_pass(store,root)
    path=policy_file(root.parent)
    policy=cm.Policy.model_validate_json(path.read_text());policy.initial_candidate='proposal'
    path.write_text(policy.model_dump_json())
    prepared=intake.prepare(['C'],root/'locator-peer',instruction='Repair the same literal locator',
        groups=[['C']],policy=path,store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'locator-peer/batch',store=store)
    request,execution=next(iter(jobs.values()))
    def propose(packet):
        return {'changes':[{'record_key':'source_fragments/FD',
            'fields_json':json.dumps({'anchor_state':'source_version_bound',
                **({'verbatim_excerpt':'甲是假设'} if changed_words else {})}),
            'reason':'bind unchanged literal to current source',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}],
            'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,execution,proposal=propose,opus=pass_review)
    assert execute_claim_management(store,request,root=execution,ports=ports)['status']=='completed'
    q=store.claim_management_qualification('D')
    if changed_words:
        assert not q['ready']
        return
    peer=store.claim_management_qualification('C')['run_id']
    assert q['ready'] and q['run_id']==old, store.get_cvp_group(peer).get('reference_qualification_handoffs')
    proof=store.get_cvp_group(old)['readonly_claim_qualifications']['D']
    assert 'source_fragments/FD' in proof['context_reviews']
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('changed_words',[False,True])
def test_two_flat_peer_reviews_share_only_a_reviewed_literal_locator_repair(claims,changed_words):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'third-shared-owner',
        'claims':[{'claim_id':'T','statement':'甲是假设。','claim_type':'exegesis',
            'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['ET']}],
        'evidence_steps':[{'evidence_step_id':'ET','statement':'甲是假设。',
            'produced_claim_ids':['T'],'source_fragment_ids':['FC'],
            'support_eligibility':'eligible_candidate','speaker':'professor','stance':'asserted'}],
        'claim_relations':[
            {'claim_relation_id':'RT','from_id':'D','to_id':'T','relation_type':'supports'},
            {'claim_relation_id':'R','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    initial=intake.prepare(['D'],root/'original',instruction='Complete D itself',
        groups=[['D']],policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(initial['manifest']['path'],root/'original/batch',store=store)
    original,original_root=next(iter(jobs.values()))
    assert execute_claim_management(store,original,root=original_root,
        ports=SyntheticPorts(store,original_root,opus=pass_review))['status']=='completed'
    old=store.claim_management_qualification('D')['run_id']
    reuse.admit(store,'D',old)
    first,_=update_neighbour(store,root)
    path=policy_file(root.parent)
    policy=cm.Policy.model_validate_json(path.read_text());policy.initial_candidate='proposal'
    path.write_text(policy.model_dump_json())
    prepared=intake.prepare(['T'],root/'second-peer',instruction='Repair a shared literal locator',
        groups=[['T']],policy=path,store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'second-peer/batch',store=store)
    request,execution=next(iter(jobs.values()))
    def propose(packet):
        cite=[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]
        return {'changes':[
            {'record_key':'claims/T','fields_json':json.dumps({'scripture_refs':['馬太福音13:1-52']}),
             'reason':'reviewed reference correction','citations':cite},
            {'record_key':'source_fragments/FC','fields_json':json.dumps({
                'anchor_state':'source_version_bound',
                **({'verbatim_excerpt':'甲是假设'} if changed_words else {})}),
             'reason':'bind literal to current source','citations':cite}],
            'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,execution,proposal=propose,opus=pass_review)
    assert execute_claim_management(store,request,root=execution,ports=ports)['status']=='completed'
    q=store.claim_management_qualification('D')
    if changed_words:
        assert not q['ready']
    else:
        assert q['ready'] and q['run_id']==old
        proof=store.get_cvp_group(old)['readonly_claim_qualifications']['D']
        assert proof['context_reviews']['claims/C']['round_id']==first
        assert len(proof['context_reviews'])==2
    assert ports.calls==['gpt','opus']


def test_reviewed_background_locator_does_not_become_an_unresolved_own_objection(claims):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'background-source',
        'claim_relations':[{'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports'}],
        'knowledge_relations':[{'relation_id':'ER','from_id':'ED','to_id':'EC','relation_type':'contextualizes'}]}))
    initial=intake.prepare(['C'],root/'initial-background',instruction='Review C and retain the background lead',
        groups=[['C']],policy=policy_file(root.parent),store=store)
    _,jobs=batch.load(initial['manifest']['path'],root/'initial-background/batch',store=store)
    request,execution=next(iter(jobs.values()))
    def review(packet):
        result=pass_review(packet)
        result['findings']=[{'target_key':'source_fragments/FD','component':'locator',
            'category':'evidence','reason':'background locator to repair with D',
            'acceptance_condition':'bind background source when reviewing D',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}],
            'affected_target_keys':[],'affected_write_keys':[],
            'impact_reason':'ED only contextualizes EC; no impact on C or its own EC binding'}]
        return result
    assert execute_claim_management(store,request,root=execution,
        ports=SyntheticPorts(store,execution,opus=review))['status']=='completed'
    old=store.claim_management_qualification('C')['run_id']
    path=policy_file(root.parent);p=cm.Policy.model_validate_json(path.read_text())
    p.initial_candidate='proposal';path.write_text(p.model_dump_json())
    peer=intake.prepare(['D'],root/'background-peer',instruction='Repair D own source locator',
        groups=[['D']],policy=path,store=store)
    _,jobs=batch.load(peer['manifest']['path'],root/'background-peer/batch',store=store)
    request,execution=next(iter(jobs.values()))
    def propose(packet):
        return {'changes':[{'record_key':'source_fragments/FD',
            'fields_json':json.dumps({'anchor_state':'source_version_bound'}),
            'reason':'repair own literal locator',
            'citations':[{'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}]}],
            'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    ports=SyntheticPorts(store,execution,proposal=propose,opus=pass_review)
    assert execute_claim_management(store,request,root=execution,ports=ports)['status']=='completed'
    newer=store.claim_management_qualification('D')['run_id']
    reuse.admit(store,'C',old,peer_run_id=newer)
    assert store.claim_management_qualification('C')['ready']
    assert store.get_cvp_group(old)['context_findings']
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('defect',[None,'owner_addition','own_evidence','incident_relation','reference_objection','legacy_peer'])
def test_fresh_target_can_join_its_original_pass_to_a_reviewed_neighbour_delta(claims,defect):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'fresh-connected','claim_relations':[
        {'claim_relation_id':'R','from_id':'D','to_id':'C','relation_type':'supports'}]}))
    path=policy_file(root.parent)
    prepared=intake.prepare(['D'],root/'fresh',instruction='Review D as the actual target',
                           groups=[['D']],policy=path,store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'fresh'/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    initial=SyntheticPorts(store,execution,opus=pass_review)
    assert execute_claim_management(store,request,root=execution,ports=initial)['status']=='completed'
    old=store.claim_management_qualification('D')['run_id']
    assert 'D' not in store.get_cvp_group(old).get('readonly_claim_qualifications',{})
    if defect=='own_evidence':
        e=store.get_record_state('evidence_steps','ED')['payload']
        store.apply_plan(store.plan_package({'package_id':'changed-fresh-basis',
            'evidence_steps':[dict(e,statement='a different basis')]}))
    elif defect=='incident_relation':
        e=store.get_record_state('claim_relations','R')['payload']
        store.apply_plan(store.plan_package({'package_id':'changed-fresh-edge',
            'claim_relations':[dict(e,reason='a different argument')]}))
    peer,ports=update_neighbour(store,root,objection=defect=='reference_objection',bounded=defect!='legacy_peer',shared=defect=='owner_addition')
    if defect not in (None,'owner_addition'):
        with pytest.raises(ValueError):reuse.admit(store,'D',old,peer_run_id=peer)
    else:
        with pytest.raises(ValueError,match='changed'):reuse.build(store,'D',old)
        proof=reuse.admit(store,'D',old,peer_run_id=peer)
        assert proof['contract']==reuse.CONTEXT_CONTRACT
        assert store.claim_management_qualification('D')['ready']
    assert initial.calls==['opus'] and ports.calls==['gpt','opus']


@pytest.mark.parametrize('after',[
    {'produced_claim_ids':['C']}, {'produced_claim_ids':['D','C','C']},
    {'produced_claim_ids':['D','C'],'statement':'changed evidence'},
    {'produced_claim_ids':['D','C'],'source_fragment_ids':['foreign']},
])
def test_additive_owner_guard_rejects_evidence_change_or_lost_owner(after):
    original={'produced_claim_ids':['D'],'statement':'same evidence','source_fragment_ids':['F']}
    assert not reuse.owner_extension(original,{**original,**after})


def test_reviewed_answer_addition_preserves_old_full_approval_without_new_calls(claims):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'own-question','questions':[
        {'question_id':'Q','text':'甲是斷言嗎？','question_type':'clarification',
         'answer_state':'answered','answer_claim_ids':['D'],'source_fragment_ids':['FC']}]}))
    old=original_pass(store,root)
    peer,ports=update_neighbour(store,root,question=True)
    assert store.get_record_state('questions','Q')['payload']['answer_claim_ids']==['D','C']
    assert store.claim_management_qualification('D')['ready']
    proof=reuse.admit(store,'D',old,peer_run_id=peer)
    assert 'questions/Q' in proof['context_reviews']
    assert ports.calls==['gpt','opus']
    question=store.get_record_state('questions','Q')['payload']
    store.apply_plan(store.plan_package({'package_id':'question-content-drift',
        'questions':[{**question,'text':'改了問題本身。'}]}))
    assert not store.claim_management_qualification('D')['ready']
    with pytest.raises(ValueError):reuse.admit(store,'D',old,peer_run_id=peer)


@pytest.mark.parametrize('after',[
    {'answer_claim_ids':['C']}, {'answer_claim_ids':['D','C','C']},
    {'answer_claim_ids':['D','C'],'text':'different question'},
    {'answer_claim_ids':['D','C'],'source_fragment_ids':['foreign']},
    {'answer_claim_ids':['D','C'],'answer_state':'unanswered'},
])
def test_answer_addition_cannot_hide_question_or_old_answer_changes(after):
    original={'answer_claim_ids':['D'],'text':'same question',
              'source_fragment_ids':['F'],'answer_state':'answered'}
    assert not reuse.answer_extension(original,{**original,**after})


def test_later_neighbour_review_replaces_flat_context_proof_without_replaying_original(claims):
    store,root=claims
    old=original_pass(store,root)
    first,_=update_neighbour(store,root)
    baseline=store.get_cvp_group(old)['readonly_claim_qualifications']['D']['original_read_pins']
    later,ports=update_neighbour(store,root/'later',refs='馬太福音13:1-51')
    assert later!=first and store.claim_management_qualification('D')['ready']
    proof=store.get_cvp_group(old)['readonly_claim_qualifications']['D']
    assert proof['original_read_pins']==baseline
    assert list(proof['context_reviews'])==['claims/C']
    assert proof['context_reviews']['claims/C']['round_id']==later
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('drift', ['own_evidence','incident_relation','reference_objection','legacy_peer'])
def test_context_handoff_cannot_hide_changed_basis_or_missing_review(claims, drift):
    store, root = claims
    old = original_pass(store, root)
    if drift=='own_evidence':
        payload=store.get_record_state('evidence_steps','ED')['payload']
        store.apply_plan(store.plan_package({'package_id':'changed-own-evidence',
            'evidence_steps':[dict(payload,statement='changed original argument')]}))
    elif drift=='incident_relation':
        payload=store.get_record_state('claim_relations','R')['payload']
        store.apply_plan(store.plan_package({'package_id':'changed-relation',
            'claim_relations':[dict(payload,reason='changed original argument relation')]}))
    peer, ports = update_neighbour(store, root, objection=drift=='reference_objection', bounded=drift!='legacy_peer')
    q=store.claim_management_qualification('D')
    assert not q or not q.get('ready')
    assert store.get_cvp_group(old)['readonly_claim_qualifications']['D']['contract']=='claim_complete_review_reuse_v1'
    if drift!='legacy_peer':
        assert store.get_cvp_group(peer)['reference_qualification_handoffs'][0]['status']=='deferred'
    with store.connect() as conn, conn.cursor() as cursor:
        with pytest.raises(ValueError):
            reuse.revalidate_context(store, cursor, 'D', old, peer)
    assert ports.calls==['gpt','opus']


@pytest.mark.parametrize('drift', ['peer_evidence','new_edge','human_hold','approval_file','peer_readback','source_file'])
def test_transferred_proof_checks_current_sources_basis_and_original_artifacts(claims, drift):
    store, root = claims
    old=original_pass(store,root)
    peer,_=update_neighbour(store,root)
    assert store.claim_management_qualification('D')['ready']
    if drift=='peer_evidence':
        payload=store.get_record_state('evidence_steps','EC')['payload']
        store.apply_plan(store.plan_package({'package_id':'peer-drift',
            'evidence_steps':[dict(payload,statement='new unreviewed premise')]}))
    elif drift=='new_edge':
        store.apply_plan(store.plan_package({'package_id':'new-edge',
            'claim_relations':[{'claim_relation_id':'R2','from_id':'C','to_id':'D','relation_type':'qualifies'}]}))
    elif drift=='human_hold':
        payload=store.get_record_state('claims','D')['payload']
        store.apply_plan(store.plan_package({'package_id':'hold', 'claims':[dict(payload,review_status='human_hold')]}))
    elif drift=='approval_file':
        from pathlib import Path
        approval=store.get_cvp_group(peer)['reviewed']['evidence']['approval']
        Path(approval['path']).write_text('{}')
    elif drift=='peer_readback':
        from pathlib import Path
        bound=store.get_cvp_group(peer)['stage_results']['readback']
        receipt=json.loads(Path(bound['path']).read_text())
        Path(receipt['readback']['path']).write_text('{}')
    else:
        from pathlib import Path
        bound=next(iter(store.get_cvp_group(peer)['materials']['request']['source_bindings'].values()))
        Path(bound['path']).write_text('changed source')
    q=store.claim_management_qualification('D')
    assert not q or not q.get('ready')


def test_context_handoff_cannot_create_a_missing_original_qualification(claims):
    store,root=claims
    old=original_pass(store,root)
    peer,_=update_neighbour(store,root)
    with store.connect() as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='existing complete qualification'):
            reuse.revalidate_context(store,cursor,'never-reviewed',old,peer)


@pytest.mark.parametrize('paragraph,located,allowed',[
    ('S0002', True, True), ('S0001', True, False), ('S0002', False, False),
])
def test_readonly_reuse_reconciles_only_unrelated_human_source_decisions(monkeypatch,paragraph,located,allowed):
    from backend.pipeline import claim_human_source as human
    decision={'decision_id':'new','source_id':'S','paragraph_sha256':{paragraph:'a'*64}}
    monkeypatch.setattr(human,'snapshot',lambda cursor,sources:[decision])
    rows={'claims/C':{'payload':{'occurrences':[{'source_id':'S','anchors':[
        {'paragraph_key':'S0001'}] if located else []}]}}}
    if allowed:
        assert reuse.human_evidence_snapshot(None,{'S':{}},[],rows)==[decision]
    else:
        with pytest.raises(ValueError,match='human source evidence changed'):
            reuse.human_evidence_snapshot(None,{'S':{}},[],rows)


def test_reused_complete_qualification_cannot_ignore_later_human_source_evidence(claims):
    from backend.tests.test_claim_human_source_decisions import facts,save
    store,root=claims
    old=original_pass(store,root)
    value=store.get_cvp_group(old)
    request=cm.Request.model_validate(value['materials']['request'])
    _,entries=facts(request,root)
    save(store,entries)
    assert not store.claim_management_qualification('D')['ready']
    with pytest.raises(ValueError,match='human source evidence changed'):
        reuse.admit(store,'D',old)


def test_readonly_scope_does_not_resurrect_a_reviewed_retired_edge(claims):
    from backend.pipeline.claim_direct_scope import scope
    store,root=claims
    old=original_pass(store,root)
    value=store.get_cvp_group(old)
    request=cm.Request.model_validate(value['materials']['unit_request']).model_copy(
        update={'target_keys':['claims/C'],'permissions':[]})
    rows={cm.source.key(*k):cm.model_state(v)
          for k,v in store.read_claim_context([('claims','C')]).items()}
    rows['claim_relations/R']['retired']=True
    rows.pop('claims/D')
    selected=scope(request,rows,include_write_context=False)
    assert 'claim_relations/R' not in selected['record_keys']
    assert 'claims/D' not in selected['neighbour_claim_keys']
    with pytest.raises(ValueError):
        scope(request,rows)


def test_an_unreviewed_new_answer_cannot_inherit_an_old_claim_approval(claims):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'question-with-unreviewed-answer',
        'claims':[{'claim_id':'X','statement':'甲是假设。','claim_type':'exegesis',
                   'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['EX']}],
        'evidence_steps':[{'evidence_step_id':'EX','statement':'甲是假设。','produced_claim_ids':['X'],
                          'source_fragment_ids':['FC'],'support_eligibility':'eligible_candidate',
                          'speaker':'professor','stance':'asserted'}],
        'questions':[{'question_id':'Q','text':'甲是斷言嗎？','question_type':'clarification',
                      'answer_state':'answered','answer_claim_ids':['D'],'source_fragment_ids':['FC']}]}))
    old=original_pass(store,root)
    peer,ports=update_neighbour(store,root,question=['D','X'])
    assert not store.claim_management_qualification('D')['ready']
    with pytest.raises(ValueError,match='reviewed additive'):
        reuse.admit(store,'D',old,peer_run_id=peer)
    assert ports.calls==['gpt','opus']


def run_scoped_peer(store, root, targets, proposal, *, review=pass_review, permissions=(), initial='proposal'):
    path=policy_file(root.parent)
    policy=cm.Policy.model_validate_json(path.read_text());policy.initial_candidate=initial
    path.write_text(policy.model_dump_json())
    prepared=intake.prepare(targets,root,instruction='Review the specified delta; preserve reference owners',
                            groups=[targets],policy=path,store=store)
    _,jobs=batch.load(prepared['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    if permissions:
        from backend.tests.test_claim_management_human_source_follow_up import authorize
        authority=json.loads(cm.source.verify_file(request.authorization).read_text())
        for permission in permissions:
            existing=next((p for p in request.permissions if p.key==permission.key),None)
            if existing is None:
                request.permissions.append(permission)
            else:
                assert set(permission.fields)<=set(existing.fields)
        authorize(request,{k:v for k,v in authority.items() if k not in {'request','policy'}})
    ports=SyntheticPorts(store,execution,proposal=proposal,opus=review)
    result=execute_claim_management(store,request,root=execution,ports=ports)
    assert result['status']=='completed',result
    return store.claim_management_qualification(targets[0])['run_id'],ports


def test_a_peer_unrelated_private_evidence_drift_does_not_repeat_the_preserved_claim(claims):
    store,root=claims
    store.apply_plan(store.plan_package({'package_id':'separate-peer-member',
        'claims':[{'claim_id':'X','statement':'甲是假设。','claim_type':'exegesis',
                   'occurrences':[{'source_id':'S','anchors':[]}],'evidence_step_ids':['EX']}],
        'evidence_steps':[{'evidence_step_id':'EX','statement':'甲是假设。','produced_claim_ids':['X'],
                          'source_fragment_ids':['FX'],'support_eligibility':'eligible_candidate',
                          'speaker':'professor','stance':'asserted'}],
        'source_fragments':[{'fragment_id':'FX','source_id':'S','paragraph_key':'S0001',
                             'verbatim_excerpt':'甲是假设，不是断言。'}],
        'questions':[{'question_id':'Q','text':'甲是斷言嗎？','question_type':'clarification',
                      'answer_state':'answered','answer_claim_ids':['D'],'source_fragment_ids':['FC']}]}))
    old=original_pass(store,root)
    cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def proposal(packet):
        return {'changes':[{'record_key':'questions/Q','fields_json':json.dumps({'answer_claim_ids':['D','C']}),
                            'reason':'add the reviewed answer','citations':[cite]}],
                'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    peer,ports=run_scoped_peer(store,root/'broad-peer',['C','X'],proposal,
        permissions=[cm.source.Permission(collection='questions',object_id='Q',fields=['answer_claim_ids'])])
    proof=store.get_cvp_group(old)['readonly_claim_qualifications']['D']
    def later(packet):
        return {'changes':[{'record_key':'evidence_steps/EX','fields_json':json.dumps({'statement':'甲不是断言。'}),
                            'reason':'another member own correction','citations':[cite]}],
                'creations':[],'retirements':[],'findings':[],'finding_resolutions':[]}
    run_scoped_peer(store,root/'later-private-correction',['X'],later)
    assert store.claim_management_qualification('D')['ready']
    assert store.get_cvp_group(old)['readonly_claim_qualifications']['D']==proof
    assert ports.calls==['gpt','opus']
    # The new answer's own evidence is still protected, despite excluding X.
    own=store.get_record_state('evidence_steps','EC')['payload']
    store.apply_plan(store.plan_package({'package_id':'relevant-answer-drift',
        'evidence_steps':[{**own,'statement':'a changed answer basis'}]}))
    assert not store.claim_management_qualification('D')['ready']


def test_a_resolved_shared_finding_keeps_its_history_without_blocking_the_old_owner(claims):
    store,root=claims;old=original_pass(store,root)
    cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def proposal(packet):
        return {'changes':[{'record_key':'claims/C','fields_json':json.dumps({'evidence_step_ids':['EC','ED']}),
                            'reason':'bind the existing shared step','citations':[cite]},
                           {'record_key':'evidence_steps/ED','fields_json':json.dumps({'produced_claim_ids':['D','C']}),
                            'reason':'preserve old owner and add C','citations':[cite]}],
                'creations':[],'retirements':[],'findings':[],
                'finding_resolutions':[{'finding_id':fid,'disposition':'resolved','reason':'reciprocal binding added',
                    'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':['evidence_steps/ED'],
                    'impact_reason':'old owner and evidence preserved'} for fid in packet['unresolved']]}
    def review(packet):
        value=pass_review(packet)
        if not packet['unresolved']:
            value['findings']=[{'target_key':'evidence_steps/ED','component':'shared_binding','category':'mapping',
                'reason':'C needs the shared step','acceptance_condition':'bind C reciprocally','citations':[cite],
                'affected_target_keys':['claims/C'],'affected_write_keys':[],'impact_reason':'shared support'}]
        else:
            value['prior_findings']=[{'finding_id':fid,'disposition':'resolved','reason':'reciprocal binding correct',
                'citations':[cite],'affected_target_keys':['claims/C'],'affected_write_keys':['evidence_steps/ED'],
                'impact_reason':'old owner preserved'} for fid in packet['unresolved']]
        return value
    peer,ports=run_scoped_peer(store,root/'shared-adjudication',['C'],proposal,review=review,initial='existing',
        permissions=[cm.source.Permission(collection='evidence_steps',object_id='ED',fields=['produced_claim_ids'])])
    assert store.claim_management_qualification('D')['ready']
    assert reuse.admit(store,'D',old,peer_run_id=peer)['contract']==reuse.CONTEXT_CONTRACT
    assert ports.calls==['opus','gpt','opus']
    # A newer independent rejection remains blocking; no blanket green flag.
    rejected=store.get_cvp_group(peer)
    for field in ('run_id','status','error'):
        rejected.pop(field,None)
    rejected.update(outcome='revision_required',opus_approved=False,
                    program_readback_complete=False,blocked_current_claim_ids=['D'],
                    findings=rejected['materials']['unresolved'])
    with store.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.pipeline_runs (run_id,subject_id,stage,trigger,status,metadata) VALUES ('new-rejection','D','review','cli','succeeded',%s::jsonb)",
            (json.dumps(rejected),))
    assert not store.claim_management_qualification('D')['ready']
    with pytest.raises(ValueError,match='newer unresolved'):
        reuse.admit(store,'D',old,peer_run_id=peer)


@pytest.mark.parametrize('direction',['outgoing','incoming'])
def test_reviewed_incident_edge_retirement_keeps_the_active_reference_claim(claims,direction):
    store,root=claims;old=original_pass(store,root)
    if direction=='incoming':
        # Establish a separate real full pass with the incoming qualifier.
        relation=store.get_record_state('claim_relations','R')['payload']
        store.apply_plan(store.plan_package({'package_id':'incoming-qualifier',
            'claim_relations':[{**relation,'from_id':'C','to_id':'D','relation_type':'qualifies'}]}))
        prepared=intake.prepare(['D'],root/'incoming-full',instruction='Review D and its current qualifier',
            groups=[['D']],policy=policy_file(root.parent),store=store)
        _,jobs=batch.load(prepared['manifest']['path'],root/'incoming-full'/'batch',store=store)
        request,execution=next(iter(jobs.values()))
        assert execute_claim_management(store,request,root=execution,
            ports=SyntheticPorts(store,execution,opus=pass_review))['status']=='completed'
        old=store.claim_management_qualification('D')['run_id']
    cite={'source_id':'S','paragraph_key':'S0001','exact_quote':'甲是假设，不是断言。'}
    def proposal(packet):
        return {'changes':[],'creations':[],'findings':[],'finding_resolutions':[],
                'retirements':[{'record_key':'claim_relations/R','reason':'source does not establish this edge','citations':[cite]}]}
    peer,ports=run_scoped_peer(store,root/'retire-edge',['C'],proposal)
    assert store.get_record_state('claim_relations','R')['retired']
    proof=reuse.admit(store,'D',old,peer_run_id=peer)
    assert 'claim_relations/R' in proof['context_reviews']
    assert store.claim_management_qualification('D')['ready']
    assert not store.get_record_state('claims','D')['retired']
    assert ports.calls==['gpt','opus']


def test_an_unreviewed_retirement_cannot_reuse_an_unrelated_peer_pass(claims):
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store,root=claims;old=original_pass(store,root)
    peer,_=update_neighbour(store,root)
    # Fault injection: the peer did not review or retire this edge.
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET retired_at=now(),revision=revision+1 WHERE collection='claim_relations' AND object_id='R'")
    with pytest.raises((ValueError,ChangeSetConflict)):
        reuse.admit(store,'D',old,peer_run_id=peer)


@pytest.mark.parametrize('paragraph,allowed',[('S0002',True),('S0001',False)])
def test_peer_handoff_scopes_later_human_confirmation_to_relevant_basis(claims,monkeypatch,paragraph,allowed):
    from backend.pipeline import claim_human_source as human
    store,root=claims
    old=original_pass(store,root)
    peer,ports=update_neighbour(store,root)
    calls=list(ports.calls)
    decision={'decision_id':'new','source_id':'S','paragraph_sha256':{paragraph:'a'*64}}
    monkeypatch.setattr(human,'snapshot',lambda cursor,sources:[decision])
    if allowed:
        proof=reuse.admit(store,'D',old,peer_run_id=peer)
        assert proof['human_source_decisions']==[decision]
        assert proof['context_reviews']
    else:
        with pytest.raises(ValueError,match='human source evidence changed'):
            reuse.admit(store,'D',old,peer_run_id=peer)
    assert ports.calls==calls


@pytest.mark.parametrize('defect',[None,'words','paragraph','segment','hash','speaker'])
def test_same_position_fragment_metadata_preserves_vote_but_not_content_changes(claims,defect):
    store,root=claims
    fragment=store.get_record_state('source_fragments','FD')['payload']
    fragment['source_segment_index']=0
    store.apply_plan(store.plan_package({'package_id':'typed-locator-preimage','source_fragments':[fragment]}))
    old=original_pass(store,root)
    snapshot=store.get_cvp_group(old)['materials']['sources']['S']
    current=store.get_record_state('source_fragments','FD')['payload']
    current.update(source_segment_index='0',source_sha256=snapshot['body_sha256'],source_binding_status='source_version_bound')
    if defect=='words':current['verbatim_excerpt']='甲是假设'
    if defect=='paragraph':current['paragraph_key']='S0002'
    if defect=='segment':current['source_segment_index']='1'
    if defect=='hash':current['source_sha256']='wrong'
    if defect=='speaker':current['speaker']='audience'
    store.apply_plan(store.plan_package({'package_id':'metadata-locator-postimage','source_fragments':[current]}))
    if defect:
        with pytest.raises(ValueError):reuse.admit(store,'D',old)
        q=store.claim_management_qualification('D');assert not q or not q.get('ready')
    else:
        proof=reuse.admit(store,'D',old)
        assert proof['program_locator_rebindings']['source_fragments/FD']['kind']=='same_position_fragment_metadata'
        assert store.claim_management_qualification('D')['ready']


@pytest.mark.parametrize('defect',[None,'manual','content','plan_drift'])
def test_verified_program_anchor_sync_reuses_exact_review_without_new_vote(claims,defect):
    store,root=claims;old=original_pass(store,root)
    claim=store.get_record_state('claims','D')['payload']
    claim['occurrences'][0]['anchor_migration_notes']=[{'state':'source_version_bound','model_calls':0}]
    if defect=='content':claim['statement']='A different professor judgment.'
    package={'package_id':'archived-locator-fixture','claims':[claim]}
    plan=store.plan_package(package);store.apply_plan(plan)
    # Disposable historical fixture. The real locator writer's package/owner
    # checks are exercised independently in test_transcript_claim_anchor_sync.
    path=root.parent/'saved-anchor-plan.json'
    path.write_text(json.dumps({'schema_version':'wang_transcript_claim_anchor_sync_v1',
        'change_set_id':plan.change_set_id,'intent':{'source_id':'S'},'package':package}))
    from backend.pipeline.cvp_sql_commit import binding
    metadata={'actor':'owner_authorized_locator_sync','model_calls':0,
        'claim_prose_changed':False,'new_qualification_granted':False,'migration_plan':binding(path)}
    if defect!='manual':
        with store.connect() as conn:
            conn.execute("UPDATE wang_knowledge.change_sets SET source_kind='wang_transcript_claim_anchor_sync_v1',metadata=%s::jsonb WHERE change_set_id=%s",(json.dumps(metadata),plan.change_set_id))
    if defect=='plan_drift':path.write_text(path.read_text()+' ')
    if defect:
        with pytest.raises(ValueError):reuse.admit(store,'D',old)
        q=store.claim_management_qualification('D');assert not q or not q.get('ready')
        return
    proof=reuse.admit(store,'D',old)
    assert proof['program_locator_rebindings']['claims/D']['kind']=='verified_claim_anchor_sync'
    assert store.claim_management_qualification('D')['ready']
    from copy import deepcopy
    forged=deepcopy(proof);forged['program_locator_rebindings']['claims/D']['before_pin'][0]+=1
    forged['proof_sha256']=cm.sha256_json({k:v for k,v in forged.items()if k!='proof_sha256'})
    with store.connect() as conn,conn.cursor() as cursor:
        with pytest.raises(ValueError,match='program locator qualification proof drift'):
            reuse.verify(store,cursor,'D',forged)


@pytest.mark.parametrize('old_index,new_index,allowed',[(7,'7',True),(None,None,True),
    (None,'None',False),(True,'True',False),(7,'07',False)])
def test_fragment_locator_index_normalization_is_not_arbitrary_string_coercion(old_index,new_index,allowed):
    payload={'fragment_id':'F','source_id':'S','paragraph_key':'S0001',
        'verbatim_excerpt':'same original words','source_segment_index':old_index}
    before={'revision':1,'content_sha256':'old','retired':False,'payload':payload}
    after={'revision':2,'content_sha256':'new','retired':False,'payload':{
        **payload,'source_segment_index':new_index,'source_sha256':'body','source_binding_status':'source_version_bound'}}
    sources={'S':{'body_sha256':'body','paragraphs':{'S0001':{'text':'same original words'}}}}
    assert bool(reuse._program_locator_rebinding(None,'source_fragments/F',before,after,sources,{})) is allowed


@pytest.mark.parametrize('old_quote,new_quote,repeated,allowed',[
    ('原話。','原話',False,True),('為何？」','為何？',False,True),
    ('原話？','原話',False,False),('原話！','原話',False,False),
    ('原話。','新話',False,False),('原話。','原話',True,False)])
def test_program_quote_metadata_preserves_words_and_question_force(old_quote,new_quote,repeated,allowed):
    import hashlib
    text=old_quote+(' '+old_quote if repeated else '')
    if new_quote not in text:text+=' '+new_quote
    a={'fragment_id':'F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':old_quote}
    b={**a,'verbatim_excerpt':new_quote,'verbatim_excerpt_sha256':hashlib.sha256(new_quote.encode()).hexdigest(),
       'source_sha256':'body','source_binding_status':'source_version_bound'}
    source={'S':{'body_sha256':'body','paragraphs':{'S0001':{'text':text}}}}
    states=lambda p,n:{'payload':p,'revision':n,'content_sha256':str(n),'retired':False}
    result=reuse._program_locator_rebinding(None,'source_fragments/F',states(a,1),states(b,2),source,{})
    assert bool(result) is allowed


@pytest.mark.parametrize('defect',[None,'missing_old','changed_words','wrong_old_speaker'])
def test_fragment_speaker_fill_comes_only_from_unchanged_reviewed_evidence(defect):
    a={'fragment_id':'F','source_id':'S','paragraph_key':'S0001','verbatim_excerpt':'原話。'}
    b={**a,'speaker':'professor','source_sha256':'body','source_binding_status':'source_version_bound'}
    state=lambda p,n:{'payload':p,'revision':n,'content_sha256':str(n),'retired':False}
    step={'evidence_step_id':'E','statement':'原論證','speaker':'professor','stance':'asserted','source_fragment_ids':['F']}
    current={'evidence_steps/E':state(step,1)}
    old={} if defect=='missing_old' else {'evidence_steps/E':state(dict(step),1)}
    if defect=='changed_words':old['evidence_steps/E']['payload']['statement']='另一個論證'
    if defect=='wrong_old_speaker':old['evidence_steps/E']['payload']['speaker']='audience'
    source={'S':{'body_sha256':'body','paragraphs':{'S0001':{'text':'原話。'}}}}
    result=reuse._program_locator_rebinding(None,'source_fragments/F',state(a,1),state(b,2),source,current,reviewed_records=old)
    assert bool(result) is (defect is None)


def test_locator_history_does_not_expand_reference_claim_private_evidence(monkeypatch):
    from backend.pipeline import claim_source_epoch_reassessment as epoch
    calls=[]
    monkeypatch.setattr(epoch,'_locator_lineage',lambda *args:calls.append(args) or ['verified-fixture-history'])
    a={'claim_id':'REF','statement':'原判斷','occurrences':[{'source_id':'S'}],'evidence_step_ids':['PRIVATE_NOT_LOADED']}
    b={**a,'occurrences':[{'source_id':'S','anchor_migration_notes':[]}]}
    state=lambda p,n:{'payload':p,'revision':n,'content_sha256':str(n),'retired':False}
    result=reuse._program_locator_rebinding(None,'claims/REF',state(a,1),state(b,2),{'S':{}},{},cursor=object())
    assert result['kind']=='verified_claim_anchor_sync' and len(calls)==1
