"""One owner-authorized, source-bound g035 audit exception, pending revisit.

Original independent verdicts remain immutable. This is not an independent pass
and cannot substitute another missing judgment or waive semantic disputes.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

TARGET = 'CV-90bea57c7ef970d48311'
REVISION = 'CVR-0f2118da6b64dfc8dd23'
CLAIM = 'DK-93b1b791c4b9-P06-CL004'
QUOTE = '这样吧, Gemini 4 马上就要 release 了, 我们暂时用 GPT 6.1 sol high. 但是 flag 这组. 以后还可以 revisit'
KEY = 'g035_owner_temporary_audit_exception'
SCHEMA = 'wang_g035_temporary_audit_exception_v1'
PROJECT = Path(__file__).resolve().parents[2]

def binding(path):
    path = Path(path).resolve()
    return {'path': str(path), 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def current_input():
    """Read-only independent reader: no inference or judgment-cache mutation."""
    spec = importlib.util.spec_from_file_location('g035_independent_reader', PROJECT/'scripts/audit-library.py')
    reader = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = reader
    spec.loader.exec_module(reader)
    settings = reader.load_settings()
    store = reader.Store(settings['KNOWLEDGE_DATABASE_URL'])
    sources = reader.SourceIndex(store.collection('source_documents'), Path(settings['DATA_BASE_DIR']))
    class Capture:
        def judge(self, prompt, schema):
            self.prompt, self.schema = prompt, schema
            return {'verdict': 'blocked', 'issue': 'other', 'reason': 'capture only'}
    capture = Capture()
    reader.audit_viewpoints(store, sources, capture, None, random.Random(0), workers=1, only={TARGET})
    return capture.prompt, capture.schema

def derive(original, response, proof_binding):
    """Explicit mixed assessment view, never overwrite original audit."""
    if original.get('meta', {}).get('model') != 'gemini-3.8-flash':
        raise ValueError('foreign original audit model')
    report = original['layers']['4']
    rows = report['results']
    blocked = [r for r in rows if r.get('verdict') == 'blocked']
    if (len(blocked) != 1 or blocked[0]['viewpoint_id'] != TARGET or
            blocked[0].get('revision_id') != REVISION or
            'PROHIBITED_CONTENT' not in blocked[0].get('reason', '') or
            report.get('blocked') != 1 or report.get('judged') != len(rows)-1 or
            report.get('complete') or any(report.get(k, 0) for k in ('model_errors','no_source_text')) or
            len({r['viewpoint_id'] for r in rows}) != len(rows)):
        raise ValueError('not the exact single provider-blocked g035 population')
    if (set(response) != {'verdict','issue','reason','claim_ids_in_question'} or
            response['verdict'] != 'same' or response['issue'] != 'none' or
            response['claim_ids_in_question'] != [] or not response['reason'].strip()):
        raise ValueError('GPT unresolved/disputed response cannot fill authorized item')
    result = deepcopy(original)
    result['meta'][KEY] = proof_binding
    result['meta']['assessment_basis'] = 'owner_temporary_nonindependent_exception_pending_revisit'
    replacement = dict(blocked[0], **response, assessment_basis='owner_authorized_gpt_subscription_exception',
                       actual_model='gpt-6.1-sol', independent=False, revisit_required=True)
    result['layers']['4']['results'] = [replacement if r['viewpoint_id'] == TARGET else r for r in rows]
    result['layers']['4'].update(blocked=0, judged=len(rows), complete=True)
    # These counters describe assessment coverage, NOT independent-model coverage.
    result['layers']['4']['independent_judged'] = len(rows)-1
    return result

def validate(audit):
    """Replay exact exception; reject missing flag, changed original or evidence."""
    marker = audit.get('meta', {}).get(KEY)
    if marker is None:
        if (audit.get('meta', {}).get('assessment_basis') == 'owner_temporary_nonindependent_exception_pending_revisit' or
                any(r.get('independent') is False for r in audit.get('layers',{}).get('4',{}).get('results',[]))):
            raise ValueError('missing temporary exception proof')
        return []
    if binding(marker['path']) != marker:
        raise ValueError('temporary exception proof physical drift')
    proof = read_artifact(Path(marker['path']))
    if (proof.get('schema_version') != SCHEMA or proof.get('target') != TARGET or
            proof.get('revision_id') != REVISION or proof.get('claim_id') != CLAIM or
            proof.get('group_key') != 'p020:p020:whole' or
            proof.get('revisit_required') is not True or proof.get('independent') is not False or
            proof.get('artifact_sha256') != sha256_json({k:v for k,v in proof.items() if k!='artifact_sha256'})):
        raise ValueError('temporary exception identity/flag/SHA drift')
    bound = {b['path']: b for b in proof['bindings']}
    if len(bound) != len(proof['bindings']):
        raise ValueError('duplicate proof binding')
    for b in bound.values():
        if binding(b['path']) != b:
            raise ValueError('temporary exception bound evidence drift')
    def artifact(key):
        path = proof[key]
        if path not in bound:
            raise ValueError('missing required bound evidence')
        return read_artifact(Path(path))
    authorization = artifact('authorization_path')
    if (authorization.get('authorization_quote') != QUOTE or authorization.get('viewpoint_id') != TARGET or
            authorization.get('revision_id') != REVISION or authorization.get('claim_id') != CLAIM or
            authorization.get('group_key') != 'p020:p020:whole' or authorization.get('revisit_required') is not True or
            authorization.get('temporary_model') != 'gpt-6.1-sol' or authorization.get('backend') != 'codex_subscription' or
            authorization.get('reasoning_effort') != 'high' or authorization.get('applies_to_future_groups') is not False or
            authorization.get('other_audit_disputes_waived') is not False):
        raise ValueError('wrong owner authorization scope')
    result, raw, request, preflight = (artifact(k) for k in ('result_path','raw_path','request_path','preflight_path'))
    original_request = artifact('original_request_path')
    original = artifact('original_audit_path')
    if (bound[proof['original_audit_path']]['file_sha256'] != authorization['original_audit_sha256'] or
            result.get('status') != 'returned' or result.get('model') != 'gpt-6.1-sol' or
            result.get('backend') != 'codex_subscription' or result.get('reasoning_effort') != 'high' or
            result.get('raw_artifact_sha256') != bound[proof['raw_path']]['file_sha256'] or
            result['raw_artifact_sha256'] != authorization['saved_gpt_raw_artifact_sha256'] or
            preflight.get('model') != result['model'] or preflight.get('backend') != result['backend'] or
            preflight.get('reasoning_effort') != 'high' or preflight.get('no_api_billing') is not True or
            request['user_prompt'] != original_request['contents'][0]['parts'][0]['text'] or
            hashlib.sha256(request['user_prompt'].encode()).hexdigest() != preflight['prompt_sha256'] or
            json.loads(raw['last_message']) != result['response'] or
            'model: gpt-6.1-sol' not in raw['stderr'] or 'reasoning effort: high' not in raw['stderr'] or
            request['user_prompt'] not in raw['stderr']):
        raise ValueError('actual GPT backend/input/raw/authorization binding drift')
    schema = dict(original_request['generationConfig']['responseSchema'], additionalProperties=False,
                  required=list(original_request['generationConfig']['responseSchema']['properties']))
    expected = derive(original, result['response'], marker)
    if set(audit['layers']) == {'3','4'}:
        expected['layers'] = {k:expected['layers'][k] for k in ('3','4')}
    if request['schema'] != schema or audit != expected:
        raise ValueError('exception changed other original verdicts or semantic input')
    return [marker, *proof['bindings']]

def create(original_path, authorization_path, diagnostic_root, output, source_paths):
    request_path = diagnostic_root/'request.json'
    request = read_artifact(request_path)
    prompt, _ = current_input()
    if prompt != request['user_prompt']:
        raise ValueError('current Registry/physical original request drift')
    paths = {'original_audit_path':original_path,'authorization_path':authorization_path,
             'result_path':diagnostic_root/'result.json','raw_path':diagnostic_root/'raw-gpt.json',
             'request_path':request_path,'preflight_path':diagnostic_root/'preflight.json',
             'original_request_path':authorization_path.parent/'owner-identical-retry/request.json'}
    body = {'schema_version':SCHEMA,'target':TARGET,'revision_id':REVISION,'claim_id':CLAIM,
            'group_key':'p020:p020:whole','revisit_required':True,'independent':False,
            'bindings':[binding(p) for p in dict.fromkeys([*paths.values(),*source_paths])],
            **{k:str(p.resolve()) for k,p in paths.items()}}
    proof_path = output/'exception-proof.json'
    write_immutable(proof_path,body|{'artifact_sha256':sha256_json(body)})
    assessment = derive(read_artifact(original_path),read_artifact(paths['result_path'])['response'],binding(proof_path))
    validate(assessment)
    write_immutable(output/'assessment.json',assessment)
    return output/'assessment.json'

def validate_completion_disclosure(row, audit):
    bindings = validate(audit)
    if bindings and (row.get('group_key') != 'p020:p020:whole' or
            row.get('revisit_required') is not True or row.get('independent_audit_complete') is not False or
            row.get('temporary_audit_exception_path') != audit['meta'][KEY]['path'] or
            any(b not in row.get('bindings',[]) for b in bindings)):
        raise ValueError('completion lost temporary exception/revisit disclosure')

def continue_g035(campaign, authorization, diagnostic, output):
    """Locked single execution, no Registry writes or replay of g035 production."""
    import fcntl
    import os
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint
    from backend.pipeline.viewpoint_evidence_source_context import load_context_source
    from backend.pipeline.viewpoint_audit_source_adjudication import run, validate_population
    from backend.pipeline.viewpoint_audit_owner_resolution import create_gpt_resolution
    from backend.pipeline.viewpoint_cvp_recovery_executor import load_campaign, resume_after_repair
    from backend.api.canonical_repository.viewpoint_production_safety import validate_registry_transition
    base = campaign.parent
    load_dotenv(PROJECT/'.env')
    os.environ['CODEX_EXECUTABLE'] = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
    with (base/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if output.exists():
            raise ValueError('existing exception execution; do not repeat')
        write_immutable(output/'execution-intent.json',{'authorization':binding(authorization),'campaign':str(campaign),
            'maximum_new_adjudication_calls':1,'blocked_item_calls':0,'registry_writes':0,'revisit_required':True})
        try:
            cfg,schedule,manifest,groups = load_campaign(campaign)
            state=read_artifact(campaign/'progress.json')
            if state['sequence']!=35 or state['group_key']!='p020:p020:whole':
                raise ValueError('not exact authorized g035')
            store=PostgresKnowledgeStore()
            states=snapshot(store);collections=sorted({c for c,_ in states})
            before=fingerprint(states,collections)
            write_immutable(output/'registry-before.json',{'collections':collections,'fingerprint_sha256':before})
            batch=list((campaign/'g035/production/run/batches').glob('*/cvp-apply-intent.json'))
            if len(batch)!=1:
                raise ValueError('not one applied g035 batch')
            # Re-verify the real exact full-registry postimage, not CLI exit status.
            post=validate_registry_transition(read_artifact(batch[0]),store=store)
            write_immutable(output/'applied-registry-readback.json',{'post_registry_fingerprint_sha256':post,
                'original_apply_intent':binding(batch[0]),'changeset_replayed':False})
            source_ids=set()
            for cid in groups[state['group_key']]['claim_ids']:
                claim=states['claims',cid]
                for eid in claim['evidence_step_ids']:
                    step=states['evidence_steps',eid]
                    fids=set(step.get('source_fragment_ids',[]))|({step['source_fragment_id']} if step.get('source_fragment_id') else set())
                    source_ids.update(states['source_fragments',fid]['source_id'] for fid in fids)
            sources=[load_context_source(states['source_documents',sid])[0] for sid in sorted(source_ids)]
            audit_path=create(campaign/'g035/independent-audit/audit.json',authorization,diagnostic,output/'temporary-assessment',sources)
            assessment=read_artifact(audit_path)
            targets=[r['viewpoint_id'] for r in assessment['layers']['4']['results']]
            validate_population(assessment,groups[state['group_key']]['claim_ids'],targets)
            report=run(audit_path,output/'source-adjudication',store,final_gpt=True)
            if any(a['verdict']!='audit_objection_rejected' for a in report['actions']):
                raise ValueError('remaining audit objections upheld or unresolved; no automatic completion')
            resolution_path=output/'gpt-final-resolution.json'
            create_gpt_resolution(audit_path,output/'source-adjudication',resolution_path,store)
            if fingerprint(snapshot(store),collections)!=before or current_input()[0]!=read_artifact(diagnostic/'request.json')['user_prompt']:
                raise ValueError('Registry or source changed during exception/adjudication')
            validate_registry_transition(read_artifact(batch[0]),store=store)
            write_immutable(output/'verified-zero-write.json',{'full_registry_fingerprint_sha256':before,'registry_writes':0,
                'revisit_required':True,'original_audit':binding(campaign/'g035/independent-audit/audit.json')})
            result=resume_after_repair(campaign,output/'continuation',base/'immediate-recovery-20261002-v1',
                os.environ['CODEX_EXECUTABLE'],saved_audit_path=audit_path,owner_resolution_path=resolution_path,
                additional_proof_bindings=(binding(output/'verified-zero-write.json'),binding(output/'applied-registry-readback.json')))
            write_immutable(output/'result.json',result)
            return result
        except Exception as exc:
            write_immutable(output/'failure.json',{'error':str(exc),'revisit_required':True,'no_retry':True,'campaign_resumed':False})
            raise

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','authorization','diagnostic','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    print(continue_g035(args.campaign,args.authorization,args.diagnostic,args.output))
