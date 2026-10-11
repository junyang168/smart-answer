"""Explicit once-only input repair; never turn a saved rejection into approval."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

SCHEMA = 'cvp_sql_source_review_input_repair.v1'
NAME = 'source-review-input-repair-authorization.json'
STAGE = 'final_review_input_repair'
FIELDS = {'source_sha256', 'paragraph_text_sha256', 'verbatim_excerpt_sha256', 'anchor_state'}


def receipt(frozen, response, proposal):
    """Prove exact source-derived fields without editing model content/reasons."""
    original = {}
    for row in response['records']:
        payload = json.loads(row['payload_json']) if 'payload_json' in row else row['payload']
        original[(row['collection'], row['object_id'])] = payload
    proofs = []
    for row in proposal.model_dump()['records']:
        if row['collection'] != 'source_fragments':
            continue
        old = original[(row['collection'], row['object_id'])]
        new = row['payload']
        if old['source_id'] != frozen['manifest']['source_id']:
            raise ValueError('normalization receipt foreign source')
        source = frozen['after'][0]
        paragraph = source['paragraphs'][new['paragraph_key']]['text']
        quote = new['verbatim_excerpt']
        if not quote or paragraph.count(quote) != 1:
            raise ValueError('normalization receipt requires exact unique own-body quote')
        computed = {'source_sha256': source['body_sha256'],
                    'paragraph_text_sha256': hashlib.sha256(paragraph.encode()).hexdigest(),
                    'verbatim_excerpt_sha256': hashlib.sha256(quote.encode()).hexdigest(),
                    'anchor_state': 'source_version_bound'}
        if any(new.get(k) != v for k, v in computed.items()):
            raise ValueError('normalization receipt computed postimage differs')
        if {k:v for k,v in old.items() if k not in FIELDS} != {k:v for k,v in new.items() if k not in FIELDS}:
            raise ValueError('normalization receipt cannot hide substantive fragment change')
        proofs.append({'record_key': 'source_fragments/'+row['object_id'],
                       'model_fields': {k:old.get(k) for k in sorted(FIELDS)},
                       'program_fields': computed, 'exact_unique_quote_verified': True})
    result = {'schema_version': 'source_before_review_normalization.v1', 'records': proofs,
            'model_reasons_preserved': True,
            'explanation': 'Model reasons describe its RAW candidate (including null hashes). '
            'The program then deterministically binds exact source/paragraph/quote hashes BEFORE review. '
            'These verified hashes are program metadata, not model claims or content changes. '
            'Review the complete normalized postimage; do not treat a preserved raw null-hash reason '
            'as a different substantive proposal. No rejection is automatically approved.'}
    if frozen['manifest'].get('evidence_binding_repair'):
        from backend.pipeline import source_reconciliation as c
        before=c.index(frozen['before_records']);projected=c.index(proposal.records)
        actions={d.record_key:d.action for d in proposal.dispositions};readonly=[]
        for (col,oid),payload in original.items():
            key=c.key(col,oid)
            if key in projected:continue
            if actions.get(key)!='retain' or before.get(key)!=payload:
                raise ValueError('readonly operational projection changed a model record/verdict')
            readonly.append({'record_key':key,'payload_sha256':sha256_json(payload),
                'current_payload_byte_equal':True,'retain_reason_unchanged':True,'content_write':False})
        if readonly:result['exact_readonly_presentation_projection']=readonly
    return result


def add_receipt(frozen, response, packet):
    from backend.pipeline.source_reconciliation_runner import effective_proposal
    result = deepcopy(packet)
    result['program_before_review'] = receipt(frozen, response, effective_proposal(frozen, response))
    return result


def check_packet(frozen, response, packet, base):
    if packet == base:  # Exact legacy packets retain their original identity.
        return
    if packet != add_receipt(frozen, response, base):
        raise ValueError('review packet normalization proof drift')


def repaired_packet(root, frozen, proposal, old_packet, old_raw, correction):
    from backend.pipeline.source_reconciliation_runner import read
    direction = read(Path(root)/NAME)
    expected = {'schema_version': SCHEMA, 'authorized_by': 'owner',
                'action_root': str(Path(root).resolve()),
                'materials_sha256': sha256_json(frozen),
                'proposal_sha256': sha256_json(proposal.model_dump()),
                'original_final_packet_sha256': sha256_json(old_packet),
                'original_final_raw_sha256': old_raw['artifact_sha256'],
                'correction_raw_sha256': correction['artifact_sha256'],
                'additional_review_limit': 1, 'content_change_authorized': False}
    if any(direction.get(k) != v for k,v in expected.items()) or not direction.get('owner_direction'):
        raise ValueError('source review input repair direction drift')
    if not frozen['manifest'].get('claim_issue'):
        raise ValueError('normalization input repair requires exact Claim management action')
    result = add_receipt(frozen, correction['response'], old_packet)
    if not result['program_before_review']['records']:
        raise ValueError('input repair has no source normalization proof')
    result['input_repair'] = {'authorization': direction,
        'original_terminal_review': old_raw['response'],
        'another_correction_allowed': False, 'another_input_repair_allowed': False}
    return result
