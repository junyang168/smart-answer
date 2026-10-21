"""One fixed-parent recovery of a proven pre-generation schema rejection.

Not a timeout/quota retry, content correction, or new finder/root. Original
request and failure remain immutable; semantic material and normal budgets stay.
"""
import json
from copy import copy
from pathlib import Path
from backend.api.canonical_repository.postgres_store import sha256_json

STAGE='proposal_schema_recovery'
NAME='proposal-schema-recovery-binding.json'


def eligible(packet):
    return bool(packet.get('manifest',{}).get('anchor_qualification') or
                packet.get('source_paragraph_profile')=='wang_source_paragraph_execution_v1')


def select(root, packet, *, persist=False):
    from backend.pipeline.source_reconciliation_runner import read,body,write
    from backend.pipeline.cvp_sql_flow import binding
    root=Path(root)
    if (root/'proposal.raw.json').exists() or not (root/'proposal.reservation.json').exists():return 'proposal'
    # This source management schema is the exact defective open-record transport.
    if not eligible(packet):
        return 'proposal'  # Normal missing-answer reservation remains uncertain.
    failed=body(read(root/'proposal.failure.json'));provider=failed.get('provider_raw') or {}
    errors=[]
    for line in provider.get('stderr','').splitlines():
        if line.startswith('ERROR: '):errors.append(json.loads(line[7:]))
    if (provider.get('stdout') or provider.get('last_message') is not None or not errors
            or any(e.get('status')!=400 or e.get('error',{}).get('code')!='invalid_json_schema'
                or e.get('error',{}).get('param')!='text.format.schema' for e in errors)
            or ((root/'initial_review.reservation.json').exists() and not (root/NAME).exists())):
        raise ValueError('not an exact pre-generation schema rejection; no retry authority')
    reserved=body(read(root/'proposal.reservation.json'))
    if (reserved['model_id']!='gpt-6.1-sol' or reserved['backend']!='codex_subscription'
            or reserved['request_payload_sha256']!=sha256_json(packet)
            or body(read(root/'proposal.packet.json'))!=packet
            or reserved['request_encoding_proof']['exact_roundtrip_verified'] is not True):
        raise ValueError('schema recovery original request/model/encoding drift')
    value={'schema_version':'wang_source_schema_engineering_recovery_v1',
        'fixed_parent':str(root.resolve()),'original_request':binding(root/'proposal.reservation.json'),
        'original_failure':binding(root/'proposal.failure.json'),'original_packet':binding(root/'proposal.packet.json'),
        'semantic_payload_sha256':sha256_json(packet),'new_proposal_limit':1,'additional_corrections':0,
        'content_approval':False,'automatic_retry':False,'defect':'open-record response schema rejected before generation',
        'repair':'lossless JSON payload strings; same full semantic source request'}
    if persist:write(root/NAME,value)
    elif body(read(root/NAME))!=value:raise ValueError('schema recovery fixed-parent binding differs')
    return STAGE


def adapter(original,packet,stage):
    if not eligible(packet) or stage not in {STAGE,'correction'}:
        return original
    from backend.pipeline.deferred_claim_repair import JsonProposal
    result=copy(original)
    result._response_model=JsonProposal;result._schema_name='source_lossless_json_records_v1'
    suffix='\n完整后像用 records.payload_json JSON 字符串无损返回；unadmitted_candidates_json 同理。每一原字段、值、顺序与完整正文保留，不由此扩权或批准。'
    result._base_prompt=original._base_prompt+suffix;result._prompt=original._prompt+suffix
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    result.generation_config_sha256=sha256_json({'base':original.generation_config_sha256,'response_transport':'source_lossless_json_records_v1'})
    return result
