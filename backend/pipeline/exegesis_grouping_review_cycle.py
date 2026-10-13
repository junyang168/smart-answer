"""One original-grounded L2 correction and terminal independent review."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from backend.pipeline import exegesis_intelligent_grouping_job as core
from backend.pipeline.exegesis_passage_unit_job import retain
from backend.pipeline.exegesis_grouping_transport import call
from backend.pipeline.viewpoint_passage_grouping_preflight import plan_reviewed_passage_unit
from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
from backend.api.canonical_repository.viewpoint_resolution import _strict_json_schema
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def reader():
    spec=importlib.util.spec_from_file_location('l2_independent_source_reader',core.REVIEWER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def normalize_legacy_review(raw,proposal):
    expected={g['group_key'] for g in proposal['groups']};findings=[];extras=[]
    for f in raw['findings']:
        key=f['key'];normalized=key[6:] if key.startswith('group:') else key
        if normalized in expected:findings.append(dict(f,key=normalized))
        elif key.startswith(('unit:','proposal:')):extras.append(f)
        else:raise ValueError('unknown legacy reviewer key')
    core.exact([f['key'] for f in findings],expected,'legacy review exact groups')
    return dict(status='pass' if all(f['status']=='pass' for f in findings) else 'needs_resolution',findings=findings),extras


def review(directory,payload,proposal,args,previous=None,legacy=None):
    binding=sha256_json(dict(payload=payload,proposal=proposal,reviewer_model='gpt-6.1-sol',previous=previous))
    request=dict(payload=payload,proposal=proposal,binding=binding,proposer_provider='claude',reviewer_provider='gpt',reviewer_model='gpt-6.1-sol',max_request_bytes=args.max_request_bytes,effort='high')
    if previous:request['payload']=dict(payload,previous_findings=previous)
    legacy_bindings=dict(input_sha256=core.checked(legacy/'input.json')['artifact_sha256'],response_sha256=core.checked(legacy/'call/response.json')['artifact_sha256']) if legacy else None
    fingerprint=sha256_json(dict(request=request,legacy_bindings=legacy_bindings,reviewer_code_sha256=__import__('hashlib').sha256(core.REVIEWER.read_bytes()).hexdigest()))
    def generate():
        directory.mkdir(parents=True,exist_ok=False);input_=retain(directory,'input.json',request)
        if legacy:
            old_input=core.checked(legacy/'input.json')
            if old_input['proposal']!=proposal or old_input['payload']['input_manifest_sha256']!=payload['input_manifest_sha256'] or old_input['payload']['claims']!=payload['claims'] or old_input['reviewer_model']!='gpt-6.1-sol':
                raise ValueError('legacy review does not bind current split/L1')
            old_call=core.checked(legacy/'call/request.json')
            if old_call['binding']!=old_input['binding'] or old_call['provider']!='gpt' or old_call['model']!='gpt-6.1-sol' or old_call['effort']!='high' or old_call['payload']['proposal']!=proposal:
                raise ValueError('legacy reviewer request/model/binding mismatch')
            raw_art=core.checked(legacy/'call/response.json');raw=raw_art['response']
            normalized,extras=normalize_legacy_review(raw,proposal)
            module=reader();_,strings=module.original_sources(payload['sources'])
            report=module.validate_report(normalized,proposal,strings,binding)
            retain(directory,'legacy-key-normalization.json',dict(raw_response_path=str(legacy/'call/response.json'),raw_response_sha256=raw_art['artifact_sha256'],original_review_input_sha256=old_input['artifact_sha256'],scope_extras_preserved=extras,semantic_findings_unchanged=True))
            return retain(directory,'report.json',dict(**report,input_sha256=input_['artifact_sha256'],provider='gpt',model='gpt-6.1-sol',derived_key_normalization=True))
        completed=subprocess.run([sys.executable,str(core.REVIEWER),'--input',str(directory/'input.json'),'--output-dir',str(directory/'call')],capture_output=True,text=True)
        retain(directory,'controller.raw.json',dict(returncode=completed.returncode,stdout=completed.stdout,stderr=completed.stderr))
        if completed.returncode:raise ValueError('L2 review failed; raw response retained')
        return core.checked(directory/'call/report.json')
    return core.obtain(directory,fingerprint,generate)


def complete_originals(payload):
    """Whole affected sources, SHA checked; SVG never passed to runtime."""
    module=reader();fresh=copy.deepcopy(payload)
    for source in fresh['sources']:
        # Remove extracted-context view so independent reader supplies actual complete text.
        for key in ['selected_locations','source_context','original_text']:source.pop(key,None)
    originals,_=module.original_sources(fresh['sources']);fresh['sources']=originals
    fresh['source_context_scope']='complete originals for this frozen passage; no new L1/primary task'
    return fresh


def cycle(root,unit,payload,answer,args,legacy=None,on_stage=None):
    if on_stage:on_stage('independent_review')
    initial=review(root/'initial',payload,answer,args,legacy=legacy)
    if initial['status']=='pass':return answer,initial
    physical=complete_originals(payload)
    prompt=(core.PROMPTS/'exegesis_argument_grouping.md').read_text()+'\n这是对独立审核意见的一次原文修正。完整输入该段所有Claim、当前全部组、审核意见及SHA核验母本。沿原文论证节点改正分组，不重做第一层/primary，不改Claim，不调和张力。rationale明确各来源的承接、限定及跨组支持Claim ID，不预先合并观点。不能确认的在rationale具体说明。只有一次修正，随后终局独立复核，不循环。'
    if unit['passage_key']=='Matt.16.19':prompt+='\n'+(core.PROMPTS/'exegesis_matthew_16_19_regression.md').read_text()
    schema=_strict_json_schema(ClaimGroupingResponse.model_json_schema());schema['properties']['scope_label']['const']=unit['unit_id']
    correction_payload=dict(physical,current_grouping=answer,independent_review=initial,scope_label=unit['unit_id'])
    fingerprint=sha256_json(dict(payload=correction_payload,prompt=prompt,schema=schema,model=args.model,effort='high'))
    if on_stage:on_stage('original_source_correction')
    def correct():
        response=call(provider='claude',model=args.model,effort='high',prompt=prompt,payload=correction_payload,schema=schema,directory=root/'correction',max_bytes=args.max_request_bytes)
        return plan_reviewed_passage_unit(unit_id=unit['unit_id'],claim_ids=unit['claim_ids'],batch_size=20,model_split=ClaimGroupingResponse.model_validate(response)).model_dump(mode='json')
    corrected=core.obtain(root/'correction',fingerprint,correct)
    if on_stage:on_stage('final_independent_review')
    final=review(root/'final',physical,corrected,args,previous=dict(initial=initial,one_correction_used=True))
    retain(root,'original-source-closeout.json',dict(initial_review_sha256=initial['artifact_sha256'],final_review_sha256=final['artifact_sha256'],corrected_grouping_sha256=sha256_json(corrected),unresolved=[f for f in final['findings'] if f['status']!='pass'],maximum_corrections=1,primary_unchanged=True))
    return corrected,final
