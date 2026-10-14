"""Revalidate unchanged returned Claim reviews after an authorized validator fix.

The old inputs, model answers, stages and call ledger remain archived facts.
No new call, semantic answer or content approval is generated here.
"""
from copy import deepcopy
from pathlib import Path
import json

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import validate_claim_stage_receipt
from backend.pipeline.cvp_sql_flow import binding,check_binding,pins
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
        if (value.get('kind')!='claim_management_round' or value['checkpoint']!='prepared' or value.get('change_set_ids')
                or value.get('opus_approved') or set(value.get('stage_results',{}))!=({'candidate'} if unrecorded else {'candidate','opus'})
                or sha256_json(value)!=direction.get('prior_metadata_sha256')):
            raise ValueError('recovery requires the exact uncommitted, returned candidate and review')
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
        store._assert_transaction_read_pins(cursor,pins({cm.source.split_key(k):v
            for k,v in value['materials']['transaction_before'].items()}))
        original=deepcopy(value);original_parent=deepcopy(parent)
        old_root=Path(value['stage_results']['candidate']['path']).parent
        if unrecorded:
            intent=body(read(old_root/'opus.reservation.json'))
            slot=parent['call_reservations'][round_id+':opus']
            if intent!=slot.get('intent') or sha256_json(intent)!=slot.get('intent_sha256'):
                raise ValueError('unrecorded result differs from its original started call intent')
        current_contract=cm.audit_contract(old_root/'recovery-contract')
        # Role instructions must be identical: this is a program validator fix,
        # not permission to reinterpret an old answer under a different prompt.
        for role in ('proposal','review'):
            key='backend/pipeline/prompts/claim_management_'+role+'_v2.md'
            if current_contract['implementation'][key]!=value['materials']['audit_contract']['implementation'][key]:
                raise ValueError('review recovery cannot change the model instructions')
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
        if unresolved:raise ValueError('validator recovery retains a real target blocker')
        obsolete=set(materials['unresolved'])|{f.identity(parent['action_id']) for f in candidate.findings}
        parent['findings']={k:v for k,v in parent['findings'].items() if k not in obsolete}
        parent['context_findings']={k:v for k,v in parent.get('context_findings',{}).items() if k not in obsolete}
        parent['context_findings'].update(context)
        parent.update(audit_contract=current_contract,result={'status':'running'})
        record={'authorization':authorization,'original_round':original,'original_parent':original_parent,
            'recovery_root':str(root),'current_contract':current_contract,'model_inputs_unchanged':True,'model_calls':0,
            'candidate_unchanged':True,'human_content_approval':False}
        write(root/'engineering-recovery.json',record)
        value.update(materials=materials,stage_results=receipts,opus_approved=True,outcome=None,findings=unresolved,
            context_findings=context,recovery_root=str(root),engineering_review_recoveries=history+[record])
        for rid,metadata in [(round_id,value),(value['parent_run_id'],parent)]:
            cursor.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb,status='running',finished_at=NULL,heartbeat_at=now() WHERE run_id=%s",
                (canonical_json(metadata),rid))
    return str(root)
