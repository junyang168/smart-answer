"""Revalidate unchanged returned Claim reviews after an authorized validator fix.

The old inputs, model answers, stages and call ledger remain archived facts.
No new call, semantic answer or content approval is generated here.
"""
from copy import deepcopy
from pathlib import Path
import json
import os
import hashlib

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import validate_claim_stage_receipt
from backend.pipeline.cvp_sql_commit import binding, check_binding, pins
from backend.pipeline.source_reconciliation_runner import body,read,write
from backend.api.canonical_repository.postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY,canonical_json,sha256_json

VERSION='wang_claim_review_engineering_recovery_v1'


def recover_returned_review(store,round_id,*,authorization,unrecorded=False):
    check_binding(authorization)
    direction=json.loads(Path(authorization['path']).read_text())
    if (direction.get('schema_version')!=VERSION or direction.get('round_id')!=round_id
            or direction.get('authorized_by')!='owner' or not direction.get('authorization_quote')
            or direction.get('human_content_approval') is not False
            or unrecorded and direction.get('record_unaccepted_result') is not True):
        raise ValueError('exact engineering recovery direction required; no content approval')
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        value=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(round_id,)).fetchone()[0]
        history=value.get('engineering_review_recoveries',[])
        if history and history[-1]['authorization']==authorization:return history[-1]['recovery_root']
        checkpoint=value.get('checkpoint')
        applied=bool(checkpoint=='applied' and direction.get('resume_applied_readback') is True and not unrecorded)
        transaction_refresh = direction.get('refresh_transaction_context') is True and not unrecorded and not applied
        if (value.get('kind')!='claim_management_round' or checkpoint not in ({'prepared'} if unrecorded else {'prepared','reviewed'}|({'applied'} if applied else set()))
                or bool(value.get('change_set_ids')) != applied
                or (value.get('opus_approved') and checkpoint not in {'reviewed','applied'} and not transaction_refresh)
                or (checkpoint in {'reviewed','applied'} and not value.get('opus_approved'))
                or set(value.get('stage_results',{}))!=({'candidate'} if unrecorded else {'candidate','opus'})
                or sha256_json(value)!=direction.get('prior_metadata_sha256')):
            raise ValueError('recovery requires the exact returned candidate/review and authorized commit checkpoint')
        parent=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',
            (value['parent_run_id'],)).fetchone()[0]
        if parent.get('continued_by') or len(parent['latest_rounds'])!=1 or parent['latest_rounds'][value['unit_id']]!=round_id:
            raise ValueError('review recovery cannot fork or revise other units')
        if transaction_refresh and (parent['result'].get('status') != 'paused'
                or not parent['result'].get('detail', '').startswith('transaction current dependency drift:')):
            raise ValueError('review recovery requires its exact paused transaction dependency drift')
        for role in value['materials']['roles']:
            slot=parent['call_reservations'].get(round_id+':'+role,{})
            if unrecorded and role=='opus':
                if slot.get('state')!='started':raise ValueError('unrecorded recovery requires the original returned Opus call')
                continue
            if role not in {'gpt','opus'} or slot.get('state')!='completed' or slot.get('result')!=value['stage_results']['candidate' if role=='gpt' else 'opus']:
                raise ValueError('review recovery cannot repeat an unknown or unrecorded call')
        transaction_context = None
        if applied:
            if direction.get('refresh_transaction_context'):
                raise ValueError('applied readback cannot refresh its committed transaction context')
            # Resume only program readback of the exact committed postimages.
            # The original transaction already checked its broader readset;
            # every model fact must still equal its reviewed postimage now.
            reviewed=value['reviewed'];plan=reviewed['plan']
            if (sha256_json(reviewed)!=value['reviewed_sha256']
                    or value['materials']['request']['operation']!='Update'
                    or value['change_set_ids']!=[plan['change_set_id']]
                    or any(op['operation']!='update' for op in plan['operations'])):
                raise ValueError('applied recovery requires the unchanged ordinary Update plan')
            committed=cursor.execute('SELECT status,fingerprint_sha256 FROM wang_knowledge.change_sets WHERE change_set_id=%s',
                (plan['change_set_id'],)).fetchone()
            if committed!=('applied',plan['fingerprint_sha256']):
                raise ValueError('applied recovery commit is not authenticated')
            expected=deepcopy(value['materials']['before'])
            for op in plan['operations']:
                key=cm.source.key(op['collection'],op['object_id'])
                if key not in expected:raise ValueError('applied write was outside reviewed model scope')
                payload={**op['payload'],'revision':op['after_revision']}
                expected[key]={'payload':payload,'revision':op['after_revision'],
                    'content_sha256':op['after_sha256'],'retired':False}
            unit=cm.Request.model_validate(value['materials']['unit_request'])
            candidate=cm.Candidate.model_validate(value['candidate'])
            if not set(cm.audit_keys(unit,expected,candidate))<=set(expected):
                raise ValueError('applied recovery would introduce unreviewed content')
            store._assert_transaction_read_pins(cursor,pins({cm.source.split_key(k):v for k,v in expected.items()}))
            if 'human_source_decisions' in value['materials']:
                from backend.pipeline.claim_human_source import assert_current
                assert_current(cursor,unit.source_bindings,value['materials']['human_source_decisions'])
        elif direction.get('refresh_transaction_context') is True:
            # A broader SQL consumer may change without changing any fact
            # actually read by either model. Prove that separation rather
            # than reusing stale approval after a content or source change.
            if unrecorded or not value.get('opus_approved'):
                raise ValueError('transaction context recovery requires an accepted complete review')
            transaction_context = _current_rejected_input_context(store, cursor,
                cm.Request.model_validate(value['materials']['request']), value['materials'], include_records=True)
            if transaction_context is None:
                raise ValueError('review recovery requires actual transaction-only drift')
            material = value['materials']
            request = cm.Request.model_validate(material['request'])
            _, sources = cm.load_authority(request, source_documents=cm.source_documents(material['before']),
                selected_source_ids=material['unit_request']['source_bindings'],
                human_source_decisions=material.get('human_source_decisions'))
            if sources != material['sources']:
                raise ValueError('review recovery source drift')
        else:
            store._assert_transaction_read_pins(cursor,pins({cm.source.split_key(k):v
                for k,v in value['materials']['transaction_before'].items()}))
        original=deepcopy(value);original_parent=deepcopy(parent)
        old_root=Path(value['stage_results']['candidate']['path']).parent
        if unrecorded:
            intent=body(read(old_root/'opus.reservation.json'))
            slot=parent['call_reservations'][round_id+':opus']
            if intent!=slot.get('intent') or sha256_json(intent)!=slot.get('intent_sha256'):
                raise ValueError('unrecorded result differs from its original started call intent')
        current_contract=cm.audit_contract(old_root/'recovery-contract', cm.Policy.model_validate(value['materials']['policy']))
        # Role instructions must be identical: this is a program validator fix,
        # not permission to reinterpret an old answer under a different prompt.
        for role in ('proposal','review'):
            key='backend/pipeline/prompts/claim_management_'+role+'_v2.md'
            if current_contract['implementation'][key]!=value['materials']['audit_contract']['implementation'][key]:
                raise ValueError('review recovery cannot change the model instructions')
        if value['materials']['original_goal'].get('joint_update_contract'):
            key='backend/pipeline/prompts/claim_management_joint_update_v1.md'
            if current_contract['implementation'][key] != value['materials']['audit_contract']['implementation'][key]:
                raise ValueError('joint review recovery cannot change the model instructions')
        if value['materials']['policy'].get('model_output_contract'):
            key='backend/pipeline/prompts/claim_management_compact_v1.md'
            if current_contract['implementation'][key]!=value['materials']['audit_contract']['implementation'][key]:
                raise ValueError('compact review recovery cannot change the model instructions')
        root=old_root/('engineering-recovery-'+sha256_json({'authorization':authorization,'contract':current_contract})[:20])
        root.mkdir(parents=True,exist_ok=True)
        for name in ['candidate.json','candidate-normalization.json']+[
                role+suffix for role in value['materials']['roles'] for suffix in ('.raw.json','.reservation.json','.packet.json')]:
            payload=(old_root/name).read_bytes();destination=root/name
            if destination.exists():
                if destination.read_bytes()!=payload:raise ValueError('recovery attempted to overwrite original model evidence')
            else:destination.write_bytes(payload)
        materials=deepcopy(value['materials']);materials['audit_contract']=current_contract
        if transaction_context:
            materials['transaction_before'] = transaction_context.pop('current_transaction_before')
        if cm.model_packet(materials)!=cm.model_packet(value['materials']):
            raise ValueError('validator recovery changed the complete model input')
        write(root/'materials.json',materials)
        receipts={}
        for stage in ('candidate','opus'):
            receipt={'round_id':round_id,'stage':stage,'materials':binding(root/'materials.json'),'candidate':binding(root/'candidate.json')}
            write(root/(stage+'-result.json'),receipt);receipts[stage]=binding(root/(stage+'-result.json'))
            _,candidate,review,passed=validate_claim_stage_receipt(receipt)
        if unrecorded:
            # Authenticate the returned answer, including a real rejection.
            # Resume its ordinary recording path; do not declare approval.
            record={'authorization':authorization,'original_round':original,'original_parent':original_parent,
                'recovery_root':str(root),'current_contract':current_contract,'model_inputs_unchanged':True,
                'model_calls':0,'candidate_unchanged':True,'human_content_approval':False,
                'unaccepted_result_validated':True,'returned_review_passed':passed}
            write(root/'engineering-recovery.json',record)
            parent.update(audit_contract=current_contract,result={'status':'running'})
            if 'gpt' in value['materials']['roles']:
                parent['call_reservations'][round_id+':gpt']['result']=receipts['candidate']
            value.update(materials=materials,stage_results={'candidate':receipts['candidate']},
                opus_approved=False,outcome=None,recovery_root=str(root),engineering_review_recoveries=history+[record])
            for rid,metadata in [(round_id,value),(value['parent_run_id'],parent)]:
                cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",
                    (canonical_json(metadata),rid))
            return str(root)
        if not passed:raise ValueError('unchanged independent review still rejects this candidate')
        request=cm.Request.model_validate(materials['unit_request'])
        unresolved=cm.unresolved_after(parent['action_id'],materials['unresolved'],review,request=request,candidate=candidate,records=materials['before'])
        context=cm.context_findings_after(parent['action_id'],materials['unresolved'],review,request=request,candidate=candidate,records=materials['before'])
        if cm.Policy.model_validate(materials['policy']).all_findings_to_gpt:
            pending=cm.review_findings(parent['action_id'],materials['unresolved'],candidate)
            unresolved.update(cm.pending_context_adjudications(pending,candidate,context,request=request))
        if unresolved:raise ValueError('validator recovery retains a real target blocker or unadjudicated objection')
        obsolete=set(materials['unresolved'])|{f.identity(parent['action_id']) for f in candidate.findings}
        parent['findings']={k:v for k,v in parent['findings'].items() if k not in obsolete}
        parent['context_findings']={k:v for k,v in parent.get('context_findings',{}).items() if k not in obsolete}
        parent['context_findings'].update(context)
        parent.update(audit_contract=current_contract,result={'status':'running'})
        record={'authorization':authorization,'original_round':original,'original_parent':original_parent,
            'recovery_root':str(root),'current_contract':current_contract,'model_inputs_unchanged':True,'model_calls':0,
            'candidate_unchanged':True,'human_content_approval':False}
        if transaction_context:
            record['transaction_context_refresh'] = transaction_context
        write(root/'engineering-recovery.json',record)
        if checkpoint=='reviewed':
            value.pop('reviewed',None);value.pop('reviewed_sha256',None)
        value.update(materials=materials,stage_results=receipts,checkpoint='applied' if applied else 'prepared',opus_approved=True,outcome=None,findings=unresolved,
            context_findings=context,recovery_root=str(root),engineering_review_recoveries=history+[record])
        for rid,metadata in [(round_id,value),(value['parent_run_id'],parent)]:
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",
                (canonical_json(metadata),rid))
    return str(root)


CANDIDATE_IDENTITY_VERSION = 'wang_claim_candidate_identity_recovery_v1'


def recover_returned_candidate(store, round_id, *, authorization):
    """Bind one proved mechanical correction; reuse the SAME spent GPT answer.

    Keeps the original request, prompt, packet, source and call slots. Only proved
    program-owned identity/provenance metadata may change. The pending
    independent review must still approve the complete corrected candidate.
    """
    from backend.pipeline.claim_model_output import retained_candidate_projection, candidate as expand_candidate
    from backend.pipeline.cvp_sql_actions import validate_claim_stage
    check_binding(authorization)
    direction = json.loads(Path(authorization['path']).read_text())
    if (direction.get('schema_version') != CANDIDATE_IDENTITY_VERSION or direction.get('round_id') != round_id
            or direction.get('authorized_by') != 'owner' or not direction.get('authorization_quote')
            or direction.get('human_content_approval') is not False):
        raise ValueError('exact candidate identity recovery direction required')
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        value = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (round_id,)).fetchone()[0]
        history = value.get('engineering_candidate_recoveries', [])
        if history and history[-1]['authorization'] == authorization:
            cm.require_current_implementation(history[-1]['current_contract'])
            return history[-1]['recovery_root']
        parent = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (value['parent_run_id'],)).fetchone()[0]
        if (sha256_json(value) != direction.get('prior_metadata_sha256')
                or sha256_json(parent) != direction.get('prior_parent_sha256')
                or value.get('kind') != 'claim_management_round' or value['checkpoint'] != 'prepared'
                or value.get('stage_results') or value.get('candidate') or value.get('change_set_ids')
                or parent.get('continued_by') or len(parent['latest_rounds']) != 1
                or parent['latest_rounds'].get(value['unit_id']) != round_id
                or parent['result'].get('reason') != 'engineering_required'):
            raise ValueError('identity recovery requires the exact unaccepted, uncommitted latest proposal')
        slot = parent['call_reservations'].get(round_id + ':gpt', {})
        if slot.get('state') != 'started' or parent['call_reservations'].get(round_id + ':opus', {}).get('state') != 'planned':
            raise ValueError('identity recovery needs a returned original proposal and its unused independent review')
        material = value['materials']; policy = cm.Policy.model_validate(material['policy'])
        metadata_mode = direction.get('program_metadata_projection') is True
        if material['request']['operation'] != 'Update' or (policy.model_output_contract and not (metadata_mode and policy.model_output_contract == 'claim_changes_v4')):
            raise ValueError('identity recovery requires its original legacy or explicitly managed metadata contract')
        old_root = Path(value.get('recovery_root', Path(parent['execution_root']) / round_id))
        intent = body(read(old_root / 'gpt.reservation.json'))
        if intent != slot.get('intent') or sha256_json(intent) != slot.get('intent_sha256'):
            raise ValueError('original GPT reservation drift')
        cm.load_authority(cm.Request.model_validate(material['request']),
            source_documents=cm.source_documents(material['before']), selected_source_ids=material['unit_request']['source_bindings'])
        context = (_current_rejected_input_context(store, cursor, cm.Request.model_validate(material['request']), material, include_records=True) if metadata_mode else None)
        if not metadata_mode:
            store._assert_transaction_read_pins(cursor, pins({cm.source.split_key(k):v for k,v in material['transaction_before'].items()}))
        raw = validate_claim_stage(old_root, 'gpt', policy, cm.model_packet(material))
        if direction.get('original_raw') != binding(old_root / 'gpt.raw.json'):
            raise ValueError('identity recovery direction must bind the exact returned raw')
        if metadata_mode:
            exit_proof = returned_proposal_transport(old_root, raw)
            if slot.get('rejection') and slot['rejection'].get('raw') != binding(old_root/'gpt.raw.json'):
                raise ValueError('recorded rejection does not bind this exact original raw')
            # Reproduce the original refusal before changing its interpretation.
            if material.get('program_metadata_projection'):
                raise ValueError('managed metadata recovery cannot stack or replace a prior projection')
            old_candidate, old_normalization = expand_candidate(raw['response'], material)
            error = parent['result'].get('detail')
            if error == 'Claim operation cannot override a protected review hold':
                try:
                    cm.validate_candidate(cm.Request.model_validate(material['unit_request']), material['before'], old_candidate, material['sources'], created_keys=material['created_keys'], original_replacements=material['replacements'], metadata_projection=material.get('program_metadata_projection'))
                except ValueError as exc:
                    if str(exc) != error:raise
                else:raise ValueError('saved proposal no longer reproduces its protected marker refusal')
            elif isinstance(error,str) and error.startswith('create identity already exists: '):
                key = error.split(': ',1)[1]
                if old_candidate.model_dump(mode='json') != body(read(old_root/'candidate.json')) or old_normalization != body(read(old_root/'candidate-normalization.json')):
                    raise ValueError('unaccepted support candidate differs from its original saved projection')
                if not any(d.action=='create' and d.record_key==key for d in old_candidate.dispositions) or key.split('/',1)[0] not in {'source_fragments','evidence_steps'} or not cursor.execute('SELECT 1 FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',cm.source.split_key(key)).fetchone():
                    raise ValueError('saved proposal does not reproduce its support identity collision')
            else:raise ValueError('managed metadata recovery cannot reinterpret a semantic or unrelated rejection')
            mappings = []
        else:
            corrected, mappings = retained_candidate_projection(raw['response'], material['before'], material['sources'])
        current = cm.audit_contract(old_root / 'recovery-contract', policy)
        for path in ['backend/pipeline/prompts/claim_management_'+role+'_v2.md' for role in ('proposal','review')] + [
                'backend/pipeline/prompts/claim_management_joint_update_v1.md']:
            if material['audit_contract']['implementation'].get(path) != current['implementation'][path]:
                raise ValueError('identity recovery cannot change original model instructions')
        root = old_root / ('engineering-candidate-' + sha256_json({'authorization':authorization,'contract':current})[:20])
        root.mkdir(parents=True, exist_ok=True)
        for name in ['gpt.raw.json', 'gpt.reservation.json', 'gpt.packet.json']:
            target = root / name
            payload = (old_root / name).read_bytes()
            if target.exists() and target.read_bytes() != payload:
                raise ValueError('identity recovery cannot overwrite original evidence')
            if not target.exists():
                with target.open('xb') as stream:
                    stream.write(payload); stream.flush()
                    os.fsync(stream.fileno())
        material = deepcopy(material)
        material['audit_contract'] = current
        if metadata_mode:
            from backend.pipeline.claim_metadata_projection import compile_projection, FIELD
            material[FIELD] = compile_projection(cursor, cm.Request.model_validate(material['unit_request']), material['before'], policy)
            if not material[FIELD]:raise ValueError('managed metadata recovery has no verified projection')
            if context:material['transaction_before'] = context['current_transaction_before']
        else:
            material['candidate_identity_recovery'] = {'rule':'retained_fragment_exact_citation_v1',
                'raw_response_sha256':sha256_json(raw['response']), 'before_sha256':sha256_json(material['before']),
                'corrections':mappings}
        if cm.model_packet(material) != cm.model_packet(value['materials']):
            raise ValueError('identity recovery changed the original complete GPT input')
        candidate, normalization = expand_candidate(raw['response'], material)
        cm.validate_candidate(cm.Request.model_validate(material['unit_request']), material['before'], candidate,
            material['sources'], created_keys=material['created_keys'], original_replacements=material['replacements'], metadata_projection=material.get('program_metadata_projection'))
        if policy.all_findings_to_gpt:
            cm.validate_proposer_resolutions(candidate, material['unresolved'], material['sources'],
                request=cm.Request.model_validate(material['unit_request']))
        if metadata_mode:
            for d in candidate.dispositions:
                if d.action=='create' and cursor.execute('SELECT 1 FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',cm.source.split_key(d.record_key)).fetchone():
                    raise ValueError('program-assigned created identity is already occupied')
            mappings = normalization.get('support_creation_identities', [])
        write(root / 'materials.json', material)
        write(root / 'candidate.json', candidate.model_dump(mode='json'))
        write(root / 'candidate-normalization.json', normalization)
        record = {'authorization':authorization, 'original_round':deepcopy(value), 'original_parent':deepcopy(parent),
            'original_raw':binding(old_root / 'gpt.raw.json'), 'current_contract':current,
            'recovery_root':str(root), 'identity_corrections':mappings, 'model_inputs_unchanged':True,
            'model_calls':0, 'call_reservations_unchanged':True, 'human_content_approval':False,
            **({'transport_exit':exit_proof,'program_metadata_projection':material['program_metadata_projection']} if metadata_mode else {})}
        write(root / 'engineering-recovery.json', record)
        value.update(materials=material, recovery_root=str(root), outcome=None,
                     engineering_candidate_recoveries=history + [record])
        parent.update(audit_contract=current, result={'status':'running'})
        for rid, metadata in [(round_id,value),(value['parent_run_id'],parent)]:
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",
                (canonical_json(metadata),rid))
        return str(root)


INVALID_REVIEW_VERSION = 'wang_claim_invalid_review_input_recovery_v1'


def consumed_invalid_review(previous, key, slot):
    """Authenticate a returned input defect for accounting, never approval.

    The original started slot stays unchanged. A later explicit qualification
    refresh must still charge this known returned answer to its predecessor.
    """
    entries = previous.get('original_goal', {}).get('unaccepted_review_inputs', [])
    root = Path(previous['execution_root']) / slot['round_id']
    matches = [entry for entry in entries if entry['raw']['path'] == str(root/'opus.raw.json')]
    if not matches:
        return False
    if len(matches) != 1 or slot['role'] != 'opus' or slot['state'] != 'started' or key != slot['round_id']+':opus':
        raise ValueError('invalid review input accounting identity drift')
    entry = matches[0]; check_binding(entry['raw'])
    materials = body(read(root/'materials.json'))
    if (materials['request'] != previous['request'] or materials['round_number'] != 0
            or materials['roles'] != ['opus'] or materials['round_id'] != slot['round_id']):
        raise ValueError('invalid review input accounting requires original initial review')
    from backend.pipeline.cvp_sql_actions import claim_management_review_packet, validate_claim_candidate_origin
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    from backend.pipeline.source_reconciliation_runner import validate_bound_call
    policy = cm.Policy.model_validate(materials['policy'])
    candidate = cm.Candidate.model_validate(body(read(root/'candidate.json')))
    validate_claim_candidate_origin(root, materials, policy, materials['sources'], candidate)
    if body(read(root/'opus.reservation.json')) != slot['intent'] or sha256_json(slot['intent']) != slot['intent_sha256']:
        raise ValueError('invalid review input reservation drift')
    packet = claim_management_review_packet(materials, candidate)
    raw = validate_bound_call(root, 'opus', packet, claim_management_model_adapter('opus', policy))
    if raw['response'] != entry['response']:
        raise ValueError('invalid review input response drift')
    from backend.pipeline.claim_model_output import review as expand_review
    review = expand_review(raw['response'], materials, candidate)
    try:
        cm.validate_review(candidate, review, packet['denominator'], packet['required_questions'],
            materials['unresolved'], materials['sources'], request=cm.Request.model_validate(materials['unit_request']), records=materials['before'])
    except ValueError as exc:
        if str(exc) != 'citation is not an exact quote from the complete own source':
            raise
    else:
        raise ValueError('accepted review cannot be charged as an invalid input')
    return True


def resume_invalid_initial_review(store, round_id, *, authorization):
    """Send an authentic but unaccepted review to the next ordinary GPT round.

    The original answer is input, never an approved review. No quote is edited,
    no source is supplemented, and the existing action's budget is unchanged.
    """
    from backend.pipeline.cvp_sql_actions import claim_management_review_packet, validate_claim_candidate_origin
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    from backend.pipeline.source_reconciliation_runner import validate_bound_call
    from backend.pipeline.claim_model_output import review as expand_review
    check_binding(authorization)
    direction = json.loads(Path(authorization['path']).read_text())
    if (direction.get('schema_version') != INVALID_REVIEW_VERSION or direction.get('round_id') != round_id
            or direction.get('authorized_by') != 'owner' or not direction.get('authorization_quote')
            or direction.get('human_content_approval') is not False):
        raise ValueError('exact invalid-review engineering direction required')
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        value = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(round_id,)).fetchone()[0]
        history = value.get('invalid_review_input_recoveries',[])
        if history and history[-1]['authorization']==authorization:return history[-1]['recovery_root']
        parent = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(value['parent_run_id'],)).fetchone()[0]
        materials = value['materials']; request = cm.Request.model_validate(materials['unit_request'])
        policy = cm.Policy.model_validate(materials['policy'])
        if (sha256_json(value)!=direction.get('prior_metadata_sha256') or request.operation!='Update'
                or value['round_number']!=0 or value['checkpoint']!='prepared' or value.get('change_set_ids')
                or materials['roles']!=['opus'] or set(value['stage_results'])!={'candidate'}
                or len(parent['latest_rounds'])!=1 or list(parent['latest_rounds'].values())!=[round_id]
                or parent.get('continued_by') or parent['result'].get('returned_rejected_call')!=round_id+':opus'
                or parent['call_reservations'][round_id+':opus']['state']!='started'
                or not policy.all_findings_to_gpt or policy.budget.max_revisions<1):
            raise ValueError('requires exact returned initial review and original unused correction budget')
        root = Path(value.get('recovery_root',Path(parent['execution_root'])/round_id))
        candidate = cm.Candidate.model_validate(value['candidate'])
        validate_claim_candidate_origin(root,materials,policy,materials['sources'],candidate)
        packet = claim_management_review_packet(materials,candidate)
        raw = validate_bound_call(root,'opus',packet,claim_management_model_adapter('opus',policy))
        review = expand_review(raw['response'],materials,candidate)
        bad = []
        for finding in review.findings:
            for citation in finding.citations:
                try:cm.validate_citations([citation],materials['sources'])
                except ValueError:
                    if citation.source_id not in materials['sources'] or citation.paragraph_key not in materials['sources'][citation.source_id]['paragraphs']:
                        raise ValueError('invalid-review recovery cannot invent a source or paragraph')
                    bad.append(citation)
        if not bad:raise ValueError('review does not have the proved citation contract failure')
        try:
            cm.validate_review(candidate,review,packet['denominator'],packet['required_questions'],materials['unresolved'],materials['sources'],request=request,records=materials['before'])
        except ValueError as exc:
            if str(exc)!='citation is not an exact quote from the complete own source':raise
        else:raise ValueError('valid review must follow its ordinary recording path')
        store._assert_transaction_read_pins(cursor,pins({cm.source.split_key(k):v for k,v in materials['transaction_before'].items()}))
        _, current_sources = cm.load_authority(request, source_documents=cm.source_documents(materials['before']),
            human_source_decisions=materials.get('human_source_decisions'))
        if current_sources != materials['sources']:
            raise ValueError('invalid-review input recovery cannot refresh a changed source')
        if 'human_source_decisions' in materials:
            from backend.pipeline.claim_human_source import assert_current
            assert_current(cursor, request.source_bindings, materials['human_source_decisions'])
        original = deepcopy(value); original_parent = deepcopy(parent)
        destination = root/('invalid-review-input-'+sha256_json(direction)[:20])
        contract = cm.audit_contract(destination,policy)
        if any(contract['implementation'][k]!=v for k,v in parent['audit_contract']['implementation'].items() if '/prompts/' in k):
            raise ValueError('invalid-review recovery cannot change original model instructions')
        # A program finding reports the INPUT defect, not a content judgment.
        # The full unchanged raw response supplies every objection to GPT.
        citation = bad[0]
        current = materials['sources'][citation.source_id]['paragraphs'][citation.paragraph_key]['text']
        finding = cm.Finding(target_key=request.target_keys[0],category='engineering',component='unaccepted_initial_review_citations',
            reason='The returned initial Opus report has non-exact or unavailable-epoch citations. Its complete unchanged response is attached in original_goal.unaccepted_review_inputs. Examine EVERY objection against the complete current source; the old report is not content approval.',
            acceptance_condition='GPT assesses every original objection, proposes necessary complete Claim maintenance with exact current citations, and normal Opus review validates the resulting candidate.',
            citations=[cm.Citation(source_id=citation.source_id,paragraph_key=citation.paragraph_key,exact_quote=current)],
            affected_target_keys=request.target_keys,affected_write_keys=[],impact_reason='the original review could not enter the normal correction ledger')
        parent.setdefault('findings',{})[finding.identity(parent['action_id'])]=finding.model_dump(mode='json')
        parent['original_goal'].setdefault('unaccepted_review_inputs',[]).append({
            'raw':binding(root/'opus.raw.json'),'response':raw['response'],
            'invalid_citations':[c.model_dump(mode='json') for c in bad],
            'instruction':'This report is unaccepted input, not an approval. Assess all objections, including context objections. Do not copy its invalid citations; use the supplied complete current source.'})
        record={'authorization':authorization,'original_round':original,'original_parent':original_parent,
            'recovery_root':str(destination),'raw':binding(root/'opus.raw.json'),'raw_unchanged':True,
            'model_calls':0,'new_budget':False,'human_content_approval':False,'review_not_accepted':True}
        write(destination/'engineering-recovery.json',record)
        parent.update(audit_contract=contract,result={'status':'running','reason':'revision_required','round_id':round_id,'unit_id':value['unit_id']})
        value.update(outcome='revision_required',invalid_review_input_recoveries=history+[record])
        for rid,metadata in [(round_id,value),(value['parent_run_id'],parent)]:
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',heartbeat_at=now() WHERE run_id=%s",(canonical_json(metadata),rid))
    return str(destination)


CITATION_ERROR = 'citation is not an exact quote from the complete own source'
CREATION_FIELDS_ERROR = 'created fields exceed creation permission'
DUPLICATE_CHANGE_ERROR = 'changed record repeated or absent from frozen input'
# Named diagnostics remain import-compatible; admission reproduces the actual
# validator rejection rather than treating this set as a retry whitelist.
RECOVERABLE_OUTPUT_ERRORS = {CITATION_ERROR, CREATION_FIELDS_ERROR, DUPLICATE_CHANGE_ERROR}


def returned_proposal_transport(root, raw):
    """Require a real saved successful CLI exit, not just a raw JSON file."""
    from backend.pipeline.subscription_process import digest_file
    import re
    transport = Path(root) / 'gpt.transport'
    receipt = json.loads((transport / 'exit.json').read_text())
    invocation = json.loads((transport / 'invocation.json').read_text())
    names = ['stdout.log', 'stderr.log', 'response.json']
    if (receipt.get('returncode') != 0
            or receipt.get('files') != {n:digest_file(transport/n) for n in names}
            or raw['response'] != json.loads((transport/'response.json').read_text())
            or raw.get('provider_raw',{}).get('stdout') != (transport/'stdout.log').read_text()
            or raw.get('provider_raw',{}).get('stderr') != (transport/'stderr.log').read_text()
            or invocation.get('cwd') != str(transport)
            or any(not re.fullmatch('[0-9a-f]{64}',str(invocation.get(k,''))) for k in ('command_sha256','input_sha256','executable_sha256'))
            or invocation['executable_sha256'] != (raw.get('runtime_identity') or {}).get('file_sha256')):
        raise ValueError('returned proposal transport drift')
    return binding(transport/'exit.json')


def returned_no_progress_input(previous, key, slot):
    """Account for an authenticated returned proposal, without accepting it.

    A linked action may change its prompt or granted fields. The old SQL
    intent and byte-bound transport remain the authority for the old call;
    do not reinterpret that call as having used the successor's instructions.
    """
    if previous.get('result', {}).get('reason') != 'no_progress' or slot.get('role') != 'gpt':
        return None
    rid = slot.get('round_id')
    if slot.get('state') != 'started' or key != str(rid) + ':gpt' or rid not in previous['latest_rounds'].values():
        return None
    root = Path(previous['execution_root']) / rid
    if not (root / 'gpt.raw.json').exists():
        return None
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.pipeline.claim_model_output import candidate as expand_candidate
    material = body(read(root / 'materials.json'))
    raw = read_artifact(root / 'gpt.raw.json')
    intent = body(read(root / 'gpt.reservation.json'))
    exit_proof = returned_proposal_transport(root, raw)
    if (intent != slot.get('intent') or sha256_json(intent) != slot.get('intent_sha256')
            or any(raw.get(k) != v for k, v in intent.items())
            or raw.get('request_payload_sha256') != sha256_json(cm.model_packet(material))
            or raw.get('response_sha256') != sha256_json(raw.get('response'))
            or raw.get('provider_raw_sha256') != sha256_json(raw.get('provider_raw'))
            or raw.get('backend') != 'codex_subscription'
            or raw.get('model_id') != previous['policy']['models']['gpt']['model_id']
            or material['request'] != previous['request'] or material['round_id'] != rid):
        raise ValueError('returned no-progress proposal binding drift')
    candidate, normalization = expand_candidate(raw['response'], material)
    request = cm.Request.model_validate(material['unit_request'])
    cm.validate_candidate(request, material['before'], candidate, material['sources'],
        created_keys=material['created_keys'], original_replacements=material['replacements'], metadata_projection=material.get('program_metadata_projection'))
    cm.validate_proposer_resolutions(candidate, material['unresolved'], material['sources'], request=request)
    repeated = cm.candidate_state(material['before'], candidate) in previous['candidate_fingerprints'].get(material['unit_id'], [])
    existing = cm.candidate_state(material['before'], candidate) == cm.candidate_state(material['before'],
        cm.existing_candidate(material['before'], replacements=material['replacements']))
    ids = {r.finding_id for r in candidate.finding_resolutions or []}
    if (not (repeated or existing) or ids and not ids.intersection(previous.get('clarified_finding_ids', []))
            or candidate.model_dump(mode='json') != body(read(root / 'candidate.json'))
            or normalization != body(read(root / 'candidate-normalization.json'))):
        raise ValueError('returned proposal does not reproduce the no-progress stop')
    return {'round_id': rid, 'role': 'gpt', 'raw': binding(root / 'gpt.raw.json'),
        'transport_exit': exit_proof, 'response': raw['response'],
        'original_prompt_sha256': raw['prompt_sha256'], 'response_state': 'returned_unaccepted',
        'content_approved': False, 'reason': 'no_progress'}


def rejected_citation_input(value, parent, role):
    return rejected_model_input(value,parent,role,error=CITATION_ERROR)


def fragment_binding_diagnostics(response, materials):
    """Locate literal excerpts for correction, without repairing the candidate."""
    invalid=[]
    for change in response.get('changes',[]):
        record_key=change.get('record_key','')
        if not record_key.startswith('source_fragments/'):
            continue
        record={**materials['before'].get(record_key,{}).get('payload',{}),
                **json.loads(change['fields_json'])}
        sid=record.get('source_id');paragraphs=materials['sources'].get(sid,{}).get('paragraphs',{})
        excerpt=record.get('verbatim_excerpt','');key=record.get('paragraph_key')
        if not excerpt or excerpt in paragraphs.get(key,{}).get('text',''):
            continue
        invalid.append({'record_key':record_key,
            'source_id':sid,'provided_paragraph_key':key,
            'literal_matches':[{'paragraph_key':pk,**{k:v for k,v in paragraph.items() if k!='text'}}
                for pk,paragraph in paragraphs.items() if excerpt in paragraph.get('text','')]})
    return invalid


def rejected_model_input(value, parent, role, *, error):
    """Authenticate and reproduce a supported rejection; never approve its content."""
    if not isinstance(error,str) or not error or role not in {'gpt','opus'}:
        raise ValueError('exact returned validation rejection and role required')
    from backend.pipeline.cvp_sql_actions import validate_claim_stage, claim_management_review_packet
    from backend.pipeline.claim_model_output import candidate as expand_candidate, review as expand_review
    materials = value['materials']; policy = cm.Policy.model_validate(materials['policy'])
    root = Path(value.get('recovery_root', Path(parent['execution_root']) / materials['round_id']))
    if body(read(root/'materials.json')) != materials:
        raise ValueError('rejected citation materials drift')
    slot = parent['call_reservations'][materials['round_id']+':'+role]
    if (body(read(root/(role+'.reservation.json'))) != slot.get('intent')
            or sha256_json(slot.get('intent')) != slot.get('intent_sha256')):
        raise ValueError('rejected citation reservation drift')
    candidate = cm.Candidate.model_validate(value['candidate']) if role == 'opus' else None
    packet = cm.model_packet(materials) if role == 'gpt' else claim_management_review_packet(materials,candidate)
    raw = validate_claim_stage(root,role,policy,packet)
    if slot.get('rejection') != {'response_state':'returned_rejected','raw':binding(root/(role+'.raw.json')),
            'detail':error,'accepted':False,'automatic_retry':False}:
        raise ValueError('rejected citation must retain its authenticated rejection')
    try:
        request = cm.Request.model_validate(materials['unit_request'])
        if role == 'gpt':
            candidate,_ = expand_candidate(raw['response'],materials)
            cm.validate_candidate(request,materials['before'],candidate,materials['sources'],
                created_keys=materials['created_keys'],original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
            cm.validate_proposer_resolutions(candidate,materials['unresolved'],materials['sources'],request=request)
        else:
            review = expand_review(raw['response'],materials,candidate)
            cm.validate_review(candidate,review,packet['denominator'],packet['required_questions'],
                materials['unresolved'],materials['sources'],request=request,records=materials['before'])
    except ValueError as exc:
        if str(exc) != error:raise
    else:raise ValueError('valid model answer cannot enter rejected-citation recovery')
    if error==DUPLICATE_CHANGE_ERROR:
        from collections import Counter
        changes=raw['response'].get('changes',[])
        keys=[item['record_key'] for item in changes]
        allowed={p.key for p in request.permissions}
        if any(key not in materials['before'] or key not in allowed for key in keys):
            raise ValueError('repeated-change recovery cannot add absent or unauthorized records')
        repeated=sorted(key for key,n in Counter(keys).items() if n>1)
        if not repeated:raise ValueError('no authenticated repeated change to correct')
        evidence=next((item['citations'][0] for item in changes if item['record_key'] in repeated and item['citations']),None)
        if evidence is None:raise ValueError('repeated-change recovery lacks exact source evidence')
        cm.validate_citations([cm.Citation.model_validate(evidence)],materials['sources'])
        return {'role':role,'round_id':materials['round_id'],'raw':binding(root/(role+'.raw.json')),
            'response':raw['response'],'rejection_detail':error,'repeated_change_keys':repeated,
            'correction_citation':evidence,'review_not_accepted':True,
            'instruction':'This unchanged rejected answer is input, never approval. Reassess all decisions and produce exactly one coherent change per existing authorized record. Reconcile repeated changes yourself; no entry has been merged, discarded or approved. Pass ordinary full validation and Opus review.'}
    if error==CREATION_FIELDS_ERROR:
        proposed=cm.source.index(candidate.records)
        provisional={k:v['payload'] for k,v in materials['before'].items()} | proposed
        invalid=[];evidence=None
        for disposition in candidate.dispositions:
            if disposition.action!='create':continue
            col,_=cm.source.split_key(disposition.record_key);payload=proposed[disposition.record_key]
            own=cm.own_source_ids(payload,provisional)
            grants=[g for g in request.creation_permissions if g.collection==col and g.source_id in own]
            if len(grants)!=1:raise ValueError('creation recovery requires an existing unique source grant')
            mechanical=cm.MECHANICAL_FIELDS | (cm.FRAGMENT_HASH_FIELDS if col=='source_fragments' else set())
            extra=set(payload)-set(grants[0].fields)-mechanical-{cm.KNOWLEDGE_COLLECTIONS[col][1]}
            if extra:
                cm.validate_citations(disposition.citations,materials['sources'])
                if not disposition.citations:raise ValueError('rejected creation lacks exact source evidence')
                evidence=evidence or disposition.citations[0].model_dump(mode='json')
                invalid.append({'record_key':disposition.record_key,'ungranted_fields':sorted(extra),'allowed_fields':grants[0].fields})
        if not invalid:raise ValueError('no authenticated ungranted creation fields to correct')
        return {'role':role,'round_id':materials['round_id'],'raw':binding(root/(role+'.raw.json')),
            'response':raw['response'],'rejection_detail':error,'invalid_creation_fields':invalid,
            'correction_citation':evidence,'review_not_accepted':True,
            'instruction':'This unchanged rejected answer is input, never approval. Reassess all decisions, use only the original granted creation fields, and pass ordinary proposal validation and Opus review. Do not widen permissions.'}
    bad = [];located = [];aliases = []
    def visit(item):
        if isinstance(item,list):
            for child in item:visit(child)
        elif isinstance(item,dict):
            if {'source_id','paragraph_key','exact_quote'} <= set(item):
                citation = cm.Citation.model_validate(item)
                source = materials['sources'].get(citation.source_id,{})
                original = citation
                if citation.source_id not in materials['sources']:
                    matches = {v['payload'].get('source_id') for k,v in materials['before'].items()
                        if k.startswith('source_documents/')
                        and v['payload'].get('transcript_id') == citation.source_id
                        and v['payload'].get('source_id') in materials['sources']}
                    if len(matches) == 1:
                        citation = citation.model_copy(update={'source_id':next(iter(matches))})
                        source = materials['sources'][citation.source_id]
                if citation.paragraph_key not in source.get('paragraphs',{}):
                    raise ValueError('rejected-input recovery cannot invent a source or paragraph')
                located.append(citation.model_dump(mode='json'))
                if citation.source_id != original.source_id:
                    # Locate the failure only. The original answer stays invalid
                    # and unchanged; normal GPT correction and Opus are required.
                    aliases.append({'provided_source_id':original.source_id,'canonical_source_id':citation.source_id})
                    bad.append(original.model_dump(mode='json'))
                try:cm.validate_citations([citation],materials['sources'])
                except ValueError:
                    source = materials['sources'].get(citation.source_id,{})
                    if citation.paragraph_key not in source.get('paragraphs',{}):
                        raise ValueError('citation recovery cannot invent a source or paragraph')
                    bad.append(citation.model_dump(mode='json'))
            else:
                for child in item.values():visit(child)
    visit(raw['response'])
    if error!=CITATION_ERROR:
        if not located:raise ValueError('rejected-input correction requires a bound own-source location')
        return {'role':role,'round_id':materials['round_id'],'raw':binding(root/(role+'.raw.json')),
            'response':raw['response'],'rejection_detail':error,'correction_citation':located[0],
            'review_not_accepted':True,
            'instruction':'This unchanged answer failed deterministic validation and is unaccepted input, never approval. Correct the exact recorded error against the frozen source and authorized objects. Reassess all decisions; preserve original scope and budget, and pass ordinary full validation and independent Opus review.'}
    if not bad:raise ValueError('no authenticated non-exact citation to correct')
    return {'role':role,'round_id':materials['round_id'],'raw':binding(root/(role+'.raw.json')),
        'response':raw['response'],'invalid_citations':bad,'review_not_accepted':True,
        **({'correction_citation':located[0],'source_identity_aliases':aliases} if aliases else {}),
        'instruction':'This unchanged rejected answer is input, never approval. Reassess every decision against the complete source, repair exact citations, and satisfy every original obligation through normal proposal and Opus review.'}


def resume_rejected_citations(store, round_id, *, role):
    return resume_rejected_output(store,round_id,role=role,error=CITATION_ERROR)


def _current_rejected_input_context(store, cursor, request, materials, *, include_records=False):
    """Keep the model input exact while refreshing transaction-only consumers."""
    from backend.pipeline.claim_joint_update import enabled
    keys = {cm.source.split_key(k) for k in request.target_keys}
    keys.update(('source_documents', sid) for sid in request.source_bindings)
    if not enabled(request):
        keys.update((p.collection, p.object_id) for p in request.permissions)
    reader = store.read_affected_context if cm.is_source_impact(request) else store.read_claim_context
    records = reader(sorted(keys))
    locked = store._assert_transaction_read_pins(cursor, pins(records))
    current = {cm.source.key(*k): cm.model_state(v) for k, v in locked.items()}
    if cm.original_goal(request, current)['original_objects'] != materials['before']:
        raise ValueError('rejected-input recovery content scope drift')
    previous = materials['transaction_before']
    if current == previous:
        return None
    return {**({'current_transaction_before': current} if include_records else {}),
            'content_scope_sha256': sha256_json(materials['before']),
            'previous_transaction_sha256': sha256_json(previous),
            'current_transaction_sha256': sha256_json(current),
            'current_pins': {cm.source.key(*k): list(v) for k, v in pins(records).items()},
            'changed_transaction_keys': sorted(k for k in set(previous) | set(current)
                                               if previous.get(k) != current.get(k))}


def resume_rejected_output(store, round_id, *, role, error):
    """Advance to ordinary correction using original authority and remaining slots.

    No fuzzy matching, raw editing, acceptance, new budget or content writes.
    A rejected proposal has no accepted previous candidate.
    """
    if role not in {'gpt','opus'}:raise ValueError('content role required')
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        value = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(round_id,)).fetchone()[0]
        parent = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',(value['parent_run_id'],)).fetchone()[0]
        marker='citation_input_recovery' if error==CITATION_ERROR else 'model_input_recovery'
        if not isinstance(error,str) or not error:raise ValueError('exact returned validation rejection required')
        if value.get(marker):
            check_binding(value[marker])
            record=body(read(value[marker]['path']))
            if record['input']['role']!=role or record['input'].get('rejection_detail',CITATION_ERROR)!=error:
                raise ValueError('recovery belongs to another role or rejection')
            return value[marker]
        materials = value['materials']; request = cm.Request.model_validate(materials['request'])
        policy = cm.Policy.model_validate(materials['policy'])
        slot = parent['call_reservations'].get(round_id+':'+role,{})
        expected_stages = {'candidate'} if role=='opus' else set()
        if (request.operation!='Update' or not policy.all_findings_to_gpt
                or value['checkpoint']!='prepared' or set(value.get('stage_results',{}))!=expected_stages
                or value.get('change_set_ids') or parent.get('continued_by')
                or ('candidate' in value)!=(role=='opus')
                or len(parent['latest_rounds'])!=1 or len(parent['original_goal']['units'])!=1
                or parent['latest_rounds'].get(value['unit_id'])!=round_id
                or parent['result'].get('returned_rejected_call')!=round_id+':'+role
                or slot.get('state')!='started' or slot.get('role')!=role or slot.get('round_id')!=round_id):
            raise ValueError('citation recovery requires the exact latest uncommitted returned rejection')
        entry = rejected_model_input(value,parent,role,error=error)
        if role=='gpt' and error=='new/updated fragment lacks an exact complete own source paragraph/excerpt':
            entry['invalid_fragment_bindings']=fragment_binding_diagnostics(entry['response'],materials)
        # Check the complete next pair before changing any stage or cancelling
        # its unused slots. Rejected generations remain spent.
        for next_role in cm.required_roles(request,value['round_number']+1,policy):
            spent = sum(s['role']==next_role and s['state'] in {'started','completed'} for s in parent['call_reservations'].values())
            if spent>=policy.budget.limit(next_role):raise ValueError('original Claim budget cannot fund citation correction and review')
        revisions = dict(parent['revision_rounds'])
        revisions[round_id] = bool(value['round_number'] or parent.get('continuation') and 'gpt' in materials['roles'])
        if sum(bool(v) for v in revisions.values())>=policy.budget.max_revisions:
            raise ValueError('original Claim revision budget cannot fund citation correction')
        current_context = _current_rejected_input_context(store, cursor, request, materials)
        _,sources = cm.load_authority(request,source_documents=cm.source_documents(materials['before']),
            selected_source_ids=materials['unit_request']['source_bindings'],human_source_decisions=materials.get('human_source_decisions'))
        if sources!=materials['sources']:raise ValueError('citation recovery source drift')
        if 'human_source_decisions' in materials:
            from backend.pipeline.claim_human_source import assert_current
            assert_current(cursor,request.source_bindings,materials['human_source_decisions'])
        destination = Path(parent['execution_root'])/round_id/'rejected-citation-input'
        contract = cm.audit_contract(destination,policy)
        if any(contract['implementation'][k]!=v for k,v in parent['audit_contract']['implementation'].items() if '/prompts/' in k):
            raise ValueError('citation recovery cannot change original model instructions')
        original = deepcopy(value); original_parent = deepcopy(parent)
        first = entry.get('correction_citation') or entry['invalid_citations'][0]
        exact = sources[first['source_id']]['paragraphs'][first['paragraph_key']]['text']
        finding = cm.Finding(target_key=request.target_keys[0],category='engineering',component='rejected_model_citations' if error==CITATION_ERROR else 'rejected_model_structure',
            reason='A returned '+role+' answer failed validation: '+error+'. The unchanged answer and exact rejected elements are in original_goal.unaccepted_model_inputs. Reassess all decisions; this is not approval.',
            acceptance_condition='Correct rejected elements within the original authority and pass ordinary full candidate validation and Opus review.',
            citations=[cm.Citation(source_id=first['source_id'],paragraph_key=first['paragraph_key'],exact_quote=exact)],
            affected_target_keys=request.target_keys,affected_write_keys=[],impact_reason='the rejected answer cannot be used as approval')
        parent['findings'][finding.identity(parent['action_id'])] = finding.model_dump(mode='json')
        parent['original_goal'].setdefault('unaccepted_model_inputs',[]).append(entry)
        for key,s in parent['call_reservations'].items():
            if s['round_id']==round_id and s['state']=='planned':
                s.update(state='cancelled',reason='rejected citation input; ordinary next round required')
        record = {'original_round':original,'original_parent':original_parent,'input':entry,
            'authorization':request.authorization.model_dump(mode='json'),'new_budget':False,'model_calls':0,
            'content_approved':False,'current_contract':contract}
        if current_context:
            record['transaction_context_refresh'] = current_context
        write(destination/'engineering-recovery.json',record)
        parent.update(audit_contract=contract,revision_rounds=revisions,result={'status':'running','reason':'revision_required','round_id':round_id,'unit_id':value['unit_id']})
        value.update(outcome='revision_required',**{marker:binding(destination/'engineering-recovery.json')})
        for rid,metadata in [(round_id,value),(value['parent_run_id'],parent)]:
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",(canonical_json(metadata),rid))
        return value[marker]


def consumed_rejected_citations(previous,key,slot):
    entries = [e for e in previous['original_goal'].get('unaccepted_model_inputs',[]) if e['round_id']+':'+e['role']==key]
    error=slot.get('rejection',{}).get('detail')
    if not entries and error not in RECOVERABLE_OUTPUT_ERRORS:return False
    if len(entries)>1 or slot['state']!='started':raise ValueError('rejected citation accounting drift')
    root = Path(previous['execution_root'])/slot['round_id']
    materials = body(read(root/'materials.json'))
    value = {'materials':materials,'candidate':body(read(root/'candidate.json')) if slot['role']=='opus' else None}
    observed = rejected_model_input(value,previous,slot['role'],error=error)
    # Old saved recovery inputs keep their exact original shape. New fragment
    # diagnostics are reproduced only when that projection was actually saved.
    if entries and 'invalid_fragment_bindings' in entries[0]:
        if slot['role']!='gpt' or error!='new/updated fragment lacks an exact complete own source paragraph/excerpt':
            raise ValueError('fragment diagnostics belong only to a returned fragment rejection')
        observed['invalid_fragment_bindings']=fragment_binding_diagnostics(observed['response'],materials)
    if entries and observed!=entries[0]:raise ValueError('rejected citation input drift')
    return True


def resume_saved_pre_review(store, round_id, *, stopped_capacity_proof=None):
    """Resume a saved pre-review stage after a verified local fix.

    Transaction-only drift may rebind consumers; mixed old/new objections may
    re-enter the normal candidate validator. Neither requires a new GPT call.
    No Opus invocation may have started. Content, source, candidate decisions,
    reservations and their spent states remain unchanged. A bounded-unit guard
    failure before any model call may add only frozen read-only references to
    the compiled input; other paths preserve the exact model packet.
    The original files and SQL metadata are archived in the recovery receipt.
    """
    from backend.pipeline.cvp_sql_actions import validate_claim_candidate_origin
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        row = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (round_id,)).fetchone()
        if not row or row[0].get('kind') != 'claim_management_round':
            raise ValueError('transaction recovery requires its original Claim round')
        value = row[0]
        parent = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (value['parent_run_id'],)).fetchone()[0]
        prior_recovery = value.get('pre_review_saved_recovery')
        if prior_recovery:
            check_binding(prior_recovery)
            cm.require_current_implementation(parent['audit_contract'])
            if parent['result'].get('status') == 'running':
                return prior_recovery
            raise ValueError('this round already used its transaction-only recovery')
        unrecorded = (parent['result'].get('reason') == 'no_progress'
            and parent['result'].get('detail') == 'Claim correction made no semantic progress or repeats a previous candidate')
        transaction_drift = (parent['result'].get('reason') == 'engineering_required'
            and parent['result'].get('detail', '').startswith('transaction current dependency drift:'))
        bounded_guard = (parent['result'].get('reason') == 'engineering_required'
            and parent['result'].get('detail') == 'direct Claim scope omitted a current neighbour or maintenance owner'
            and parent['policy'].get('review_unit_contract') == 'claim_bounded_review_unit_v1')
        capacity_stop = False
        if stopped_capacity_proof is not None:
            check_binding(stopped_capacity_proof)
            stopped = json.loads(Path(stopped_capacity_proof['path']).read_text())
            artifact = stopped.get('artifacts', '')
            if (stopped.get('result_state') != 'unknown' or stopped.get('retry_authorized') is not False
                    or stopped.get('all_slot_locks_acquired') is not True or stopped.get('model_calls') != 0
                    or 'invocation.json' not in stopped.get('files', {})
                    or not set(stopped['files']) <= {'invocation.json','stdout.log','stderr.log','response.json'}
                    or not Path(artifact).is_absolute() or not Path(artifact).is_dir()
                    or (Path(artifact) / 'exit.json').exists()
                    or any(hashlib.sha256((Path(artifact) / name).read_bytes()).hexdigest() != digest
                           for name, digest in stopped['files'].items())):
                raise ValueError('saved-candidate recovery needs the unchanged stopped-capacity proof')
            capacity_stop = (parent['result'].get('reason') == 'engineering_required'
                and parent['result'].get('detail') in {
                    'opus: unsettled previous transport: ' + artifact,
                    'opus: unsettled transport: ' + artifact})
            if not capacity_stop:
                raise ValueError('stopped-capacity proof does not match the exact pre-review block')
        if (value['checkpoint'] != 'prepared'
                or set(value.get('stage_results', {})) != (set() if unrecorded else {'candidate'})
                or bool(value.get('candidate')) != (not unrecorded)
                or value.get('change_set_ids') or value.get('opus_approved')
                or parent.get('continued_by') or parent['latest_rounds'].get(value['unit_id']) != round_id
                or len(parent['latest_rounds']) != 1 or not (transaction_drift or unrecorded or capacity_stop or bounded_guard)):
            raise ValueError('saved-candidate recovery requires the exact uncommitted pre-review failure')
        opus = parent['call_reservations'].get(round_id + ':opus', {})
        if opus.get('state') != 'planned':
            raise ValueError('transaction recovery cannot repeat an invoked or unknown review')
        old_root = Path(value.get('recovery_root', Path(parent['execution_root']) / round_id))
        if any((old_root / name).exists() for name in ('opus.reservation.json', 'opus.raw.json', 'opus.transport/invocation.json')):
            raise ValueError('transaction recovery cannot repeat an invoked or unknown review')
        materials = body(read(old_root / 'materials.json'))
        if materials != value['materials']:
            raise ValueError('transaction recovery original materials drift')
        request = cm.Request.model_validate(materials['request'])
        policy, sources = cm.load_authority(request, source_documents=cm.source_documents(materials['before']),
            selected_source_ids=materials['unit_request']['source_bindings'], human_source_decisions=materials.get('human_source_decisions'))
        if sources != materials['sources'] or policy.model_dump(mode='json') != materials['policy']:
            raise ValueError('transaction recovery source or policy drift')
        if request.operation != 'Update':
            raise ValueError('transaction recovery is limited to an existing Update')
        if 'human_source_decisions' in materials:
            from backend.pipeline.claim_human_source import assert_current
            assert_current(cursor, request.source_bindings, materials['human_source_decisions'])
        candidate = cm.Candidate.model_validate(body(read(old_root / 'candidate.json')))
        if not unrecorded and candidate.model_dump(mode='json') != value['candidate']:
            raise ValueError('transaction recovery saved candidate drift')
        validate_claim_candidate_origin(old_root, materials, policy, sources, candidate)
        cm.validate_candidate(cm.Request.model_validate(materials['unit_request']), materials['before'], candidate, sources,
            created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
        if 'gpt' in materials['roles']:
            slot = parent['call_reservations'].get(round_id + ':gpt', {})
            if slot.get('state') != ('started' if unrecorded else 'completed') or body(read(old_root / 'gpt.reservation.json')) != slot.get('intent') or sha256_json(slot['intent']) != slot.get('intent_sha256'):
                raise ValueError('transaction recovery original GPT accounting drift')
            if policy.all_findings_to_gpt:
                cm.validate_proposer_resolutions(candidate, materials['unresolved'], sources,
                    request=cm.Request.model_validate(materials['unit_request']))
        if unrecorded:
            if 'gpt' not in materials['roles'] or not policy.all_findings_to_gpt:
                raise ValueError('unrecorded adjudication requires its original GPT reply')
            ids = {r.finding_id for r in candidate.finding_resolutions or []}
            judged = set(parent.get('clarified_finding_ids', []))
            if not ids & judged or not ids - judged:
                raise ValueError('unrecorded adjudication requires mixed old and genuinely new findings')
        # The exact-input helper intentionally rejects a changed projection.
        # Before the first bounded call, that projection is the defect being
        # repaired; verify all original transaction pins and the new semantic
        # frontier below instead of requiring the old projection to recur.
        context = (None if bounded_guard else
                   _current_rejected_input_context(store, cursor, request, materials, include_records=True))
        if context is None and not (unrecorded or capacity_stop or bounded_guard):
            raise ValueError('transaction recovery requires actual transaction-only drift')
        if bounded_guard:
            if context is not None:
                raise ValueError('bounded scope guard recovery requires unchanged original transaction inputs')
            if (materials['roles'] != ['opus'] or candidate.records
                    or any(d.action != 'retain' for d in candidate.dispositions)
                    or any(s.get('state') != 'planned' for s in parent['call_reservations'].values())):
                raise ValueError('bounded input repair is only allowed before the first actual model call')
            from backend.pipeline.claim_direct_scope import scope
            selected = scope(cm.Request.model_validate(materials['unit_request']), materials['transaction_before'])
            repaired_before = {k:materials['transaction_before'][k] for k in selected['record_keys']}
            if any(repaired_before.get(k) != v for k,v in materials['before'].items()):
                raise ValueError('bounded input repair must preserve every original record unchanged')
            audited = {cm.source.split_key(k) for k in repaired_before}
            pins_before = {cm.source.split_key(k):v for k,v in materials['transaction_before'].items()}
            observed = store._assert_transaction_read_pins(cursor, pins(pins_before))
            store._assert_claim_management_scope(cursor, {k:v for k,v in pins(pins_before).items() if k in audited},
                records={k:v for k,v in observed.items() if k in audited}, request=materials['unit_request'],
                actual_write_keys=[d.record_key for d in candidate.dispositions if d.action != 'retain'])
        current_records = context.pop('current_transaction_before') if context else materials['transaction_before']
        original_value, original_parent = deepcopy(value), deepcopy(parent)
        current_contract = cm.audit_contract(old_root / 'transaction-recovery-contract', policy)
        if any(current_contract['implementation'].get(k) != digest for k, digest in materials['audit_contract']['implementation'].items() if '/prompts/' in k):
            raise ValueError('transaction recovery cannot change model instructions')
        root = old_root / ('transaction-only-' + sha256_json(context)[:20])
        root.mkdir(parents=True, exist_ok=True)
        for name in (*([] if bounded_guard else ['candidate.json', 'candidate-normalization.json']), *(['gpt.packet.json', 'gpt.reservation.json', 'gpt.raw.json'] if 'gpt' in materials['roles'] else [])):
            payload = (old_root / name).read_bytes()
            target = root / name
            if target.exists() and target.read_bytes() != payload:
                raise ValueError('transaction recovery cannot overwrite an original answer')
            if not target.exists():
                with target.open('xb') as stream:
                    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        rebound = deepcopy(materials)
        rebound.update(transaction_before=current_records, audit_contract=current_contract)
        if bounded_guard:
            rebound['before'] = repaired_before
            # This is the pre-inference compiled read denominator, not an
            # expansion of owner targets, write authority or maintenance.
            # Keep the original goal in the recovery archive and extend only
            # reference facts from the already frozen transaction snapshot.
            for goal in (rebound['original_goal'], parent['original_goal']):
                for key in set(repaired_before)-set(materials['before']):
                    goal['original_objects'][key] = deepcopy(repaired_before[key])
            candidate, normalized = cm.normalize_candidate(cm.existing_candidate(repaired_before).model_dump(mode='json'), sources)
            cm.validate_candidate(cm.Request.model_validate(materials['unit_request']), repaired_before, candidate, sources,
                created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
            write(root / 'candidate.json', candidate.model_dump(mode='json'))
            write(root / 'candidate-normalization.json', normalized)
        if not bounded_guard and cm.model_packet(rebound) != cm.model_packet(materials):
            raise ValueError('transaction recovery changed the exact model input')
        write(root / 'materials.json', rebound)
        receipt = {'round_id': round_id, 'stage': 'candidate', 'materials': binding(root / 'materials.json'), 'candidate': binding(root / 'candidate.json')}
        write(root / 'candidate-result.json', receipt)
        record = {'original_round': original_value, 'original_parent': original_parent,
            'transaction_context_refresh': context, 'current_contract': current_contract,
            'recovery_root': str(root), 'unrecorded_new_finding_adjudication': unrecorded,
            'stopped_capacity_proof': stopped_capacity_proof,
            'bounded_scope_guard_repaired': bounded_guard,
            'model_inputs_unchanged': not bounded_guard, 'candidate_sha256': candidate.digest,
            'readonly_context_added_before_first_call': sorted(set(rebound['before'])-set(materials['before'])),
            'model_calls': 0, 'call_reservations_unchanged': True, 'content_approved': False}
        write(root / 'engineering-recovery.json', record)
        proof = binding(root / 'engineering-recovery.json')
        value.update(materials=rebound, recovery_root=str(root), outcome=None, pre_review_saved_recovery=proof)
        if bounded_guard:
            value['candidate'] = candidate.model_dump(mode='json')
            value['claim_ids'] = [k.split('/',1)[1] for k in cm.audit_keys(
                cm.Request.model_validate(rebound['unit_request']), rebound['before'], candidate)
                if k.startswith('claims/')]
        if not unrecorded:
            value['stage_results']['candidate'] = binding(root / 'candidate-result.json')
        parent.update(audit_contract=current_contract, result={'status': 'running'})
        for rid, metadata in ((round_id, value), (value['parent_run_id'], parent)):
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s", (canonical_json(metadata), rid))
        return proof
