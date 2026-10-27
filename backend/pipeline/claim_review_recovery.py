"""Revalidate unchanged returned Claim reviews after an authorized validator fix.

The old inputs, model answers, stages and call ledger remain archived facts.
No new call, semantic answer or content approval is generated here.
"""
from copy import deepcopy
from pathlib import Path
import json
import os

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
        if (value.get('kind')!='claim_management_round' or checkpoint not in ({'prepared'} if unrecorded else {'prepared','reviewed'}|({'applied'} if applied else set()))
                or bool(value.get('change_set_ids')) != applied
                or (value.get('opus_approved') and checkpoint not in {'reviewed','applied'})
                or (checkpoint in {'reviewed','applied'} and not value.get('opus_approved'))
                or set(value.get('stage_results',{}))!=({'candidate'} if unrecorded else {'candidate','opus'})
                or sha256_json(value)!=direction.get('prior_metadata_sha256')):
            raise ValueError('recovery requires the exact returned candidate/review and authorized commit checkpoint')
        parent=cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE',
            (value['parent_run_id'],)).fetchone()[0]
        if parent.get('continued_by') or len(parent['latest_rounds'])!=1 or parent['latest_rounds'][value['unit_id']]!=round_id:
            raise ValueError('review recovery cannot fork or revise other units')
        for role in value['materials']['roles']:
            slot=parent['call_reservations'].get(round_id+':'+role,{})
            if unrecorded and role=='opus':
                if slot.get('state')!='started':raise ValueError('unrecorded recovery requires the original returned Opus call')
                continue
            if role not in {'gpt','opus'} or slot.get('state')!='completed' or slot.get('result')!=value['stage_results']['candidate' if role=='gpt' else 'opus']:
                raise ValueError('review recovery cannot repeat an unknown or unrecorded call')
        if applied:
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
        unresolved=cm.unresolved_after(parent['action_id'],materials['unresolved'],review,request=request,candidate=candidate)
        context=cm.context_findings_after(parent['action_id'],materials['unresolved'],review,request=request,candidate=candidate)
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

    Keeps the original request, prompt, packet, source and call slots. Only the
    program interpretation of a uniquely cited retain key changes. The pending
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
        if policy.model_output_contract or material['request']['operation'] != 'Update':
            raise ValueError('identity recovery is for a legacy existing Update, not a new output contract')
        old_root = Path(value.get('recovery_root', Path(parent['execution_root']) / round_id))
        intent = body(read(old_root / 'gpt.reservation.json'))
        if intent != slot.get('intent') or sha256_json(intent) != slot.get('intent_sha256'):
            raise ValueError('original GPT reservation drift')
        cm.load_authority(cm.Request.model_validate(material['request']),
            source_documents=cm.source_documents(material['before']), selected_source_ids=material['unit_request']['source_bindings'])
        store._assert_transaction_read_pins(cursor, pins({cm.source.split_key(k):v for k,v in material['transaction_before'].items()}))
        raw = validate_claim_stage(old_root, 'gpt', policy, cm.model_packet(material))
        if direction.get('original_raw') != binding(old_root / 'gpt.raw.json'):
            raise ValueError('identity recovery direction must bind the exact returned raw')
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
        material['candidate_identity_recovery'] = {'rule':'retained_fragment_exact_citation_v1',
            'raw_response_sha256':sha256_json(raw['response']), 'before_sha256':sha256_json(material['before']),
            'corrections':mappings}
        if cm.model_packet(material) != cm.model_packet(value['materials']):
            raise ValueError('identity recovery changed the original complete GPT input')
        candidate, normalization = expand_candidate(raw['response'], material)
        cm.validate_candidate(cm.Request.model_validate(material['unit_request']), material['before'], candidate,
            material['sources'], created_keys=material['created_keys'], original_replacements=material['replacements'])
        if policy.all_findings_to_gpt:
            cm.validate_proposer_resolutions(candidate, material['unresolved'], material['sources'],
                request=cm.Request.model_validate(material['unit_request']))
        write(root / 'materials.json', material)
        write(root / 'candidate.json', candidate.model_dump(mode='json'))
        write(root / 'candidate-normalization.json', normalization)
        record = {'authorization':authorization, 'original_round':deepcopy(value), 'original_parent':deepcopy(parent),
            'original_raw':binding(old_root / 'gpt.raw.json'), 'current_contract':current,
            'recovery_root':str(root), 'identity_corrections':mappings, 'model_inputs_unchanged':True,
            'model_calls':0, 'call_reservations_unchanged':True, 'human_content_approval':False}
        write(root / 'engineering-recovery.json', record)
        value.update(materials=material, recovery_root=str(root), outcome=None,
                     engineering_candidate_recoveries=history + [record])
        parent.update(audit_contract=current, result={'status':'running'})
        for rid, metadata in [(round_id,value),(value['parent_run_id'],parent)]:
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",
                (canonical_json(metadata),rid))
        return str(root)


INVALID_REVIEW_VERSION = 'wang_claim_invalid_review_input_recovery_v1'


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
