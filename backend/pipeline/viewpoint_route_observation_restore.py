"""Explicitly restore one pinned observation without model transcription."""
from copy import deepcopy

MARKER = 'route_observation_restore_contract'
FIELD = '$restore_observation_v1'


def contract(packet):
    return {'schema_version':'wang_route_observation_restore_v1',
        'route_packet_sha256':packet['packet_sha256'],
        'instruction':('To preserve an original observation after a draft changed it, return an update '
            'edit in observation_supports with key equal to its existing observation_support_id and '
            'fields_json containing only {"$restore_observation_v1":"<that same ID>"}. '
            'The program restores the exact pinned original content and evidence; do not copy them. '
            'This does not retire records or approve content. Keep all other valid edits and dispositions.')}


def with_restore_contract(packet):
    from backend.pipeline.viewpoint_route_correction_changes import MARKER as CORRECTION
    source=packet.get('route_evidence_packet',{})
    if CORRECTION in packet and source.get('existing_observation_supports'):
        return packet | {MARKER:contract(source)}
    return packet


def restore_observation(packet, key):
    from backend.api.canonical_repository.viewpoint_observation_support import (
        ProposedObservationSupport, observation_payload, observation_id,
    )
    matches=[o for o in packet.get('existing_observation_supports',[])
             if o['observation_support_id']==key]
    if len(matches)!=1:
        raise ValueError('Observation restore needs one exact existing support ID')
    old=matches[0]
    if (not any(v['viewpoint_id']==old['viewpoint_id']
                and v['viewpoint_revision_id']==old['validated_against_viewpoint_revision_id']
                for v in packet['approved_viewpoints'])
            or packet['source_revisions'].get(old['source_id'])!=old['source_revision_sha256']):
        raise ValueError('Observation restore cannot rebind changed CVP or source versions')
    item={k:deepcopy(old[k]) for k in ('source_id','observation_summary','supports','support_scope_note')}
    item.update(local_observation_key=key,
        viewpoint_revision_id=old['validated_against_viewpoint_revision_id'],evidence_bindings=[])
    claims={c['claim_id']:c for c in packet['claims']}
    for binding in old['evidence_bindings']:
        claim=claims.get(binding['claim_id'])
        if claim is None or any(claim[k]!=binding[k] for k in ('pinned_claim_revision','claim_revision_sha256')):
            raise ValueError('Observation restore cannot rebind changed or missing Claim versions')
        choices=sorted(c['claim_component_key'] for c in packet['claim_components']
                       if c['claim_id']==binding['claim_id'] and c['source_id']==old['source_id'])
        if not choices:
            raise ValueError('Observation restore lacks a current source-local Claim component')
        # Component aliases identify the same pinned owner; none is promoted
        # to membership. The compiled stored content must remain identical.
        item['evidence_bindings'].append({'claim_component_key':choices[0],
            'evidence_step_id':binding['evidence_step_id'],'source_fragment_id':binding['source_fragment_id']})
    parsed=ProposedObservationSupport.model_validate(item)
    payload=observation_payload(parsed,packet)
    if (observation_id(payload)!=key or any(payload[k]!=old[k] for k in payload)):
        raise ValueError('Observation restore differs from pinned original content or source version')
    return parsed.model_dump(mode='json')
