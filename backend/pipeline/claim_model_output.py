"""Compact model decisions, expanded against frozen inputs before normal gates.

No inferred edits or editorial decisions: omitted fields remain byte-for-byte
unchanged. Independent review explicitly approves all unchallenged records.
Historical policies keep their original schemas and interpretation.
"""
from copy import deepcopy
import json
from typing import Literal

from pydantic import Field
from backend.pipeline import claim_management as cm

CONTRACT = 'claim_changes_v1'


class Change(cm.source.Contract):
    record_key: str
    fields_json: str = Field(description='JSON object containing only fields actually changed; omit identity, provenance and derived hashes.')
    reason: str = Field(min_length=1)
    citations: list[cm.Citation] = Field(min_length=1)


class Changes(cm.source.Contract):
    changes: list[Change]
    findings: list[cm.JointFinding]
    finding_resolutions: list[cm.JointFindingResolution]


class FindingsReview(cm.source.Contract):
    complete_review: Literal[True] = Field(description='Explicitly confirms the entire supplied scope and every required question were reviewed, and approves every record without a new or persistent/reopened finding. Does not approve publication or action completion.')
    findings: list[cm.JointFinding]
    prior_findings: list[cm.JointFindingResolution]


def enabled(materials):
    return materials['policy'].get('model_output_contract') == CONTRACT


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
    wire = Changes.model_validate(raw)
    request = cm.Request.model_validate(materials['unit_request'])
    if request.operation != 'Update' or request.creation_permissions or materials['replacements']:
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
    expanded = cm.Candidate(records=rows, dispositions=list(dispositions.values()),
        operation='Update', findings=wire.findings, finding_resolutions=wire.finding_resolutions)
    result, receipt = cm.normalize_candidate(expanded.model_dump(mode='json'), materials['sources'])
    receipt.update(output_contract=CONTRACT, model_response_sha256=cm.sha256_json(raw),
                   frozen_before_sha256=cm.sha256_json(before), changed_keys=sorted(seen))
    return result, receipt


def review(raw, materials, proposal):
    if not enabled(materials):
        return cm.Review.model_validate(raw)
    wire = FindingsReview.model_validate(raw)
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
    return cm.Review(proposal_sha256=proposal.digest, verdicts=[cm.source.Verdict(
        record_key=key, approved=key not in rejected,
        reason=('Reviewer reported an unresolved issue.' if key in rejected else
                'Reviewer explicitly approved this unchallenged record in the complete-scope review.'),
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
    packet['model_output_contract'] = CONTRACT
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
