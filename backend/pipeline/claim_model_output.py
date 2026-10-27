"""Compact model decisions, expanded against frozen inputs before normal gates.

No inferred edits or editorial decisions: omitted fields remain byte-for-byte
unchanged. Independent review explicitly approves all unchallenged records.
Historical policies keep their original schemas and interpretation.
"""
from copy import copy, deepcopy
import json
from typing import Literal

from pydantic import Field, create_model
from backend.pipeline import claim_management as cm

CONTRACT = 'claim_changes_v1'
SCOPED_CONTRACT = 'claim_changes_v2'
# v2 plus new records under the request's creation_permissions (#459: a Create
# under the full-disposition schema made GPT copy 352-532 retain entries by hand).
CREATING_CONTRACT = 'claim_changes_v3'


class Change(cm.source.Contract):
    record_key: str
    fields_json: str = Field(description='JSON object containing only fields actually changed; omit identity, provenance and derived hashes.')
    reason: str = Field(min_length=1)
    citations: list[cm.Citation] = Field(min_length=1)


class Changes(cm.source.Contract):
    changes: list[Change]
    findings: list[cm.JointFinding]
    finding_resolutions: list[cm.JointFindingResolution]


class Creation(cm.source.Contract):
    collection: str
    object_id: str = Field(description='New identity; must not exist in the frozen input.')
    fields_json: str = Field(description='JSON object with the complete new record, without identity, provenance or derived hashes.')
    reason: str = Field(min_length=1)
    citations: list[cm.Citation] = Field(min_length=1)


class CreatingChanges(Changes):
    creations: list[Creation]


class FindingsReview(cm.source.Contract):
    complete_review: Literal[True] = Field(description='Explicitly confirms the entire supplied scope and every required question were reviewed, and approves every record without a new or persistent/reopened finding. Does not approve publication or action completion.')
    findings: list[cm.JointFinding]
    prior_findings: list[cm.JointFindingResolution]


def enabled(materials):
    return materials['policy'].get('model_output_contract') in {CONTRACT, SCOPED_CONTRACT, CREATING_CONTRACT}


def scoped_adapter(adapter, packet):
    """Constrain identifiers before generation; keep all semantic checks after it.

    v1 saved requests keep their exact schema. v2 uses the SAME output shape,
    with enum domains compiled only from the frozen unit and actual candidate.
    No response is filtered, corrected, or accepted by this function.
    """
    contract = packet.get('policy', {}).get('model_output_contract')
    if contract not in {SCOPED_CONTRACT, CREATING_CONTRACT}:
        return adapter
    request = packet['unit_request']
    if request == {'__claim_request_ref__': 'request'}:
        request = packet['request']
    before = set(packet['before'])
    targets = set(request['target_keys'])
    writable = {cm.source.key(p['collection'], p['object_id']) for p in request['permissions']} & before
    reviewing = 'candidate' in packet
    writes = ({d['record_key'] for d in packet['candidate']['dispositions'] if d['action'] != 'retain'}
              if reviewing else writable)
    denominator = set(packet['denominator']) if reviewing else before

    def key_type(keys):
        return Literal[tuple(sorted(keys))] if keys else str

    def key_list(keys, description):
        return (list[key_type(keys)], Field(description=description, **({'max_length': 0} if not keys else {})))

    impact = {
        'affected_target_keys': key_list(targets, 'Only affected Claim keys from unit_request.target_keys. Never Evidence, Observation, relation, or readonly neighbour keys.'),
        'affected_write_keys': key_list(writes, 'Only affected actual non-retain candidate writes; for a proposal only authorized writes are possible. An unchanged authorized record is not an actual write.'),
    }
    finding = create_model('ScopedFinding', __base__=cm.JointFinding,
        target_key=(key_type(denominator), Field(description='The exact object with the objection; distinct from affected Claim targets.')), **impact)
    pending = set(packet.get('unresolved', {}))
    resolution = create_model('ScopedFindingResolution', __base__=cm.JointFindingResolution,
        finding_id=(key_type(pending), ...), **impact)
    if reviewing:
        from backend.pipeline.claim_evidence_review import BindingFindingsReview, enabled as binding_enabled
        base = BindingFindingsReview if binding_enabled(packet['policy']) else FindingsReview
        model = create_model('ScopedFindingsReview', __base__=base,
            findings=(list[finding], ...),
            prior_findings=(list[resolution], Field(**({'max_length': 0} if not pending else {}))))
    else:
        change = create_model('ScopedChange', __base__=Change, record_key=(key_type(writable), ...))
        fields = dict(changes=(list[change], Field(**({'max_length': 0} if not writable else {}))),
            findings=(list[finding], ...),
            finding_resolutions=(list[resolution], Field(**({'max_length': 0} if not pending else {}))))
        if contract == CREATING_CONTRACT:
            creatable = {p['collection'] for p in request.get('creation_permissions', [])}
            creation = create_model('ScopedCreation', __base__=Creation, collection=(key_type(creatable), ...))
            fields['creations'] = (list[creation], Field(**({'max_length': 0} if not creatable else {})))
        model = create_model('ScopedChanges', __base__=CreatingChanges if contract == CREATING_CONTRACT else Changes, **fields)
    schema_sha = cm.sha256_json(model.model_json_schema())
    if getattr(adapter, '_claim_scope_schema_sha256', None) == schema_sha:
        return adapter
    result = copy(adapter)
    result._response_model = model
    result._claim_scope_schema_sha256 = schema_sha
    result._claim_scope_base_config_sha256 = getattr(adapter, '_claim_scope_base_config_sha256', adapter.generation_config_sha256)
    result.generation_config_sha256 = cm.sha256_json({
        'base_config_sha256': result._claim_scope_base_config_sha256,
        'response_scope': SCOPED_CONTRACT, 'response_schema_sha256': schema_sha})
    return result


def object_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('changed fields contain a duplicate JSON key')
            result[key] = value
        return result
    result = json.loads(text, object_pairs_hook=unique)
    if not isinstance(result, dict) or not result:
        raise ValueError('changed fields must be a nonempty JSON object')
    return result


def candidate(raw, materials):
    """Program expands implicit retains; normal permission/source checks follow."""
    if not enabled(materials):
        recovery = materials.get('candidate_identity_recovery')
        if not recovery:
            result, receipt = cm.normalize_candidate(raw, materials['sources'])
            return cm.normalize_legacy_restorations(result, receipt, materials)
        corrected, mappings = retained_candidate_projection(raw, materials['before'], materials['sources'])
        if recovery != {'rule':'retained_fragment_exact_citation_v1',
                'raw_response_sha256':cm.sha256_json(raw), 'before_sha256':cm.sha256_json(materials['before']),
                'corrections':mappings}:
            raise ValueError('candidate identity recovery proof drift')
        result, receipt = cm.normalize_candidate(corrected.model_dump(mode='json'), materials['sources'])
        receipt['raw_candidate_sha256'] = cm.Candidate.model_validate(cm.decode_candidate(raw)).digest
        receipt['identity_recovery'] = recovery
        return result, receipt
    if materials.get('candidate_identity_recovery'):
        raise ValueError('legacy identity recovery cannot upgrade the model output contract')
    creating = materials['policy']['model_output_contract'] == CREATING_CONTRACT
    wire = (CreatingChanges if creating else Changes).model_validate(raw)
    request = cm.Request.model_validate(materials['unit_request'])
    if request.operation != 'Update' or (request.creation_permissions and not creating) or materials['replacements']:
        raise ValueError('compact decisions support existing Update only')
    before = materials['before']
    dispositions = {k:cm.ObjectDisposition(record_key=k, action='retain',
        reason='Unchanged frozen input; proposer supplied no modification.') for k in sorted(before)}
    rows, seen = [], set()
    for change in wire.changes:
        key = change.record_key
        if key in seen or key not in before:
            raise ValueError('changed record repeated or absent from frozen input')
        seen.add(key)
        fields = object_json(change.fields_json)
        collection, oid = cm.source.split_key(key)
        identity = cm.KNOWLEDGE_COLLECTIONS.get(collection, (None, None))[1]
        if set(fields) & (cm.MECHANICAL_FIELDS | cm.source.PROVENANCE | cm.FRAGMENT_HASH_FIELDS | {identity}):
            raise ValueError('changed fields include program-owned identity, provenance or hashes')
        payload = deepcopy(before[key]['payload'])
        if all(payload.get(k) == v for k,v in fields.items()):
            raise ValueError('unchanged object must be omitted from changes')
        payload.update(fields)
        rows.append(cm.source.Row(collection=collection, object_id=oid, payload=payload))
        dispositions[key] = cm.ObjectDisposition(record_key=key, action='update', reason=change.reason,
                                                citations=change.citations)
    for creation in getattr(wire, 'creations', []):
        key = cm.source.key(creation.collection, creation.object_id)
        if key in seen or key in before or creation.collection not in cm.KNOWLEDGE_COLLECTIONS:
            raise ValueError('created record repeated, already present or of an unknown collection')
        seen.add(key)
        fields = object_json(creation.fields_json)
        identity = cm.KNOWLEDGE_COLLECTIONS[creation.collection][1]
        if set(fields) & (cm.MECHANICAL_FIELDS | cm.source.PROVENANCE | cm.FRAGMENT_HASH_FIELDS | {identity}):
            raise ValueError('created fields include program-owned identity, provenance or hashes')
        # Permission, own source and field grants are checked by validate_candidate.
        rows.append(cm.source.Row(collection=creation.collection, object_id=creation.object_id,
                                  payload={**fields, identity: creation.object_id}))
        dispositions[key] = cm.ObjectDisposition(record_key=key, action='create', reason=creation.reason,
                                                citations=creation.citations)
    expanded = cm.Candidate(records=rows, dispositions=list(dispositions.values()),
        operation='Update', findings=wire.findings, finding_resolutions=wire.finding_resolutions)
    result, receipt = cm.normalize_candidate(expanded.model_dump(mode='json'), materials['sources'])
    receipt.update(output_contract=materials['policy']['model_output_contract'], model_response_sha256=cm.sha256_json(raw),
                   frozen_before_sha256=cm.sha256_json(before), changed_keys=sorted(seen))
    return result, receipt


def review(raw, materials, proposal):
    if not enabled(materials):
        return cm.Review.model_validate(raw)
    from backend.pipeline.claim_evidence_review import BindingFindingsReview, enabled as binding_enabled, validate_assessments
    binding = binding_enabled(materials['policy'])
    wire = (BindingFindingsReview if binding else FindingsReview).model_validate(raw)
    if binding:
        validate_assessments(wire, materials, proposal)
    request = cm.Request.model_validate(materials['unit_request'])
    denominator = cm.audit_keys(request, materials['before'], proposal)
    pending = cm.review_findings(request.action_id, materials['unresolved'], proposal)
    rejected = {f.target_key for f in wire.findings}
    for resolution in wire.prior_findings:
        if resolution.finding_id not in pending:
            raise ValueError('review refers to an unknown prior finding')
        if resolution.disposition != 'resolved':
            rejected.add(pending[resolution.finding_id]['target_key'])
    if rejected - set(denominator):
        raise ValueError('review finding outside complete frozen scope')
    assessments = {a.claim_key: a.reason for a in wire.binding_assessments} if binding else {}
    return cm.Review(proposal_sha256=proposal.digest, verdicts=[cm.source.Verdict(
        record_key=key, approved=key not in rejected,
        reason=('Reviewer reported an unresolved issue.' if key in rejected else
                assessments.get(key, 'Reviewer explicitly approved this unchallenged record in the complete-scope review.')),
        problems=['reported finding'] if key in rejected else []) for key in denominator],
        findings=wire.findings, prior_findings=wire.prior_findings,
        answered_questions=cm.operation_questions(materials['original_goal'], proposal))


def review_packet(packet):
    """Keep full before/source tables; remove generated retain enumeration."""
    if not enabled(packet):
        return packet
    packet = deepcopy(packet)
    packet['candidate']['dispositions'] = [d for d in packet['candidate']['dispositions'] if d['action'] != 'retain']
    packet.pop('candidate_sha256')
    packet['model_output_contract'] = packet['policy']['model_output_contract']
    packet['candidate_projection'] = ('Every before record not explicitly changed remains identical. '
        'candidate.records are complete postimages for actual changes. Review the ENTIRE denominator and all sources. '
        'complete_review=true explicitly approves all records without reported/persistent findings. '
        'The program binds the candidate hash and question coverage; do not echo hashes, verdict lists or question names.')
    return packet


def retained_fragment_identity(raw, before):
    """Repair only a retained fragment key uniquely identified by its citation.

    No text matching heuristics, suffix guessing, new dispositions or semantic
    edits. The same complete source/paragraph/excerpt must identify ONE frozen
    fragment globally, and the substitution must restore exact-once coverage.
    """
    original = cm.Candidate.model_validate(cm.decode_candidate(raw))
    keys = [d.record_key for d in original.dispositions]
    if len(keys) != len(set(keys)):
        raise ValueError('identity repair refuses duplicate dispositions')
    required = set(before) | set(cm.source.index(original.records))
    missing, extra = required - set(keys), set(keys) - required
    if not missing or len(missing) != len(extra):
        raise ValueError('identity repair requires an exact missing/extra substitution')
    corrected = original.model_copy(deep=True)
    mappings = []
    used = set()
    for disposition in corrected.dispositions:
        key = disposition.record_key
        if key not in extra:
            continue
        if not key.startswith('source_fragments/') or disposition.action != 'retain' or len(disposition.citations) != 1:
            raise ValueError('identity repair supports only single-citation retained source fragments')
        citation = disposition.citations[0]
        if citation.source_epoch != 'current':
            raise ValueError('identity repair requires a current-source citation')
        matches = [k for k,v in before.items() if k.startswith('source_fragments/') and not v['retired']
            and v['payload'].get('source_id') == citation.source_id
            and str(v['payload'].get('paragraph_key')) == citation.paragraph_key
            and v['payload'].get('verbatim_excerpt') == citation.exact_quote]
        if len(matches) != 1 or matches[0] not in missing or matches[0] in used:
            raise ValueError('identity repair requires a unique frozen source/paragraph/excerpt match')
        # The erroneous key must not also name a record, finding, relation or
        # another semantic decision in the reply.
        other = original.model_dump(mode='json')
        other['dispositions'] = [d for d in other['dispositions'] if d['record_key'] != key]
        if key in json.dumps(other, ensure_ascii=False):
            raise ValueError('identity repair cannot change semantic references')
        target = matches[0]; used.add(target)
        mappings.append({'before':key, 'after':target, 'citation':citation.model_dump(mode='json')})
        disposition.record_key = target
    if used != missing or {d.record_key for d in corrected.dispositions} != required:
        raise ValueError('identity repair did not restore the complete exact-once denominator')
    return corrected, mappings


def retained_candidate_projection(raw, before, sources):
    """Restore copied annotations only; source wording and decisions stay fixed."""
    result, corrections = retained_fragment_identity(raw, before)
    from opencc import OpenCC
    simplified, traditional = OpenCC('t2s'), OpenCC('s2t')
    for disposition in result.dispositions:
        for index, citation in enumerate(disposition.citations):
            try:
                cm.validate_citations([citation], sources)
                continue
            except ValueError:
                if disposition.action != 'retain' or citation.source_epoch != 'current':
                    raise
            text = sources.get(citation.source_id, {}).get('paragraphs', {}).get(citation.paragraph_key, {}).get('text', '')
            quote = citation.exact_quote
            simp, trad = simplified.convert(quote), traditional.convert(quote)
            matches = [text[i:i+len(quote)] for i in range(len(text)-len(quote)+1)
                if simplified.convert(text[i:i+len(quote)]) == simp
                and traditional.convert(text[i:i+len(quote)]) == trad]
            if len(matches) != 1:
                raise ValueError('retained quote is not a unique bidirectional script-only source match')
            exact = matches[0]
            corrections.append({'record_key':disposition.record_key, 'field':f'citations[{index}].exact_quote',
                'before':quote, 'after':exact, 'rule':'exact_unique_bidirectional_script_v1'})
            citation.exact_quote = exact
            cm.validate_citations([citation], sources)
    return result, corrections
