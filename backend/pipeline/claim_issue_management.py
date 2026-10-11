"""One Claim/Evidence issue boundary, using the existing SQL/source compiler.

Detection is not repair approval. Model origins and raw refusals stay intact.
The caller supplies a physically bound owner policy, never inferred permission.
There is no database, snapshot engine, retry loop or production CLI here.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha

VERSION = "wang_claim_issue_management_v1"
CLAIM_FIELDS = frozenset({"statement", "claim_type", "attribution", "discourse_mode", "scripture_refs", "occurrences"})
EVIDENCE_FIELDS = frozenset({"statement", "source_fragment_id", "source_fragment_ids"})
ELIGIBILITY_FIELDS = frozenset({"support_eligibility", "valid_for_identity_review"})
LINK_FIELDS = frozenset({"pinned_claim_revision", "component_locator", "evidence_bindings", "link_type"})
HOLDS = c.HUMAN_HOLDS | {"human_hold", "withheld_unreviewed", "rejected"}
# These are protected review states, not proof a human personally requested a
# hold. Provenance must be inspected before attributing any restriction to owner.


class Issue(c.Contract):
    claim_id: str
    claim_revision: int = Field(ge=1)
    claim_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: str
    source_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spans: list[tuple[int, int, str]] = Field(min_length=1)
    origin: Literal["admission", "gpt_proposal", "opus_initial", "opus_final", "deferred", "gemini_audit", "owner"]
    report: c.FileBinding
    reason: str = Field(min_length=1)
    correction_used: bool = False
    terminal_refusal: bool = False

    @property
    def issue_id(self):
        # Renaming a reason/cause/finder or changing root/policy cannot buy calls.
        return "CI-" + sha256_json({k: getattr(self, k) for k in (
            "claim_id", "claim_revision", "claim_sha256", "source_id",
            "source_file_sha256", "evidence_sha256", "spans")})


class Policy(c.Contract):
    schema_version: Literal["wang_claim_issue_management_v1"] = VERSION
    authorization_id: str = Field(min_length=1)
    authorization_quote: str = Field(min_length=1)
    human_content_approval: Literal[False] = False
    claim_fields: list[str]
    evidence_fields: list[str]
    # Explicit versioned opt-in. Old policies/materials retain their exact scope.
    evidence_eligibility_review: bool = False
    link_fields: list[str]
    # CVP body/identity/retirement is never implied by Claim permission.
    cvp_permissions: list[c.Permission] = Field(default_factory=list)


class Citation(c.Contract):
    source_id: str
    paragraph_key: str
    exact_quote: str = Field(min_length=1)


class Assessment(c.Contract):
    issue_id: str
    cause: Literal["already_sufficient", "evidence_binding", "claim_statement", "cvp_link", "source_unresolved"]
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class Impact(c.Contract):
    record_key: str
    action: Literal["retain", "rebind", "update", "needs_direction"]
    reason: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class Proposal(c.Proposal):
    claim_assessments: list[Assessment] = Field(min_length=1)
    cvp_impacts: list[Impact]


from backend.pipeline.deferred_claim_repair import JsonProposal


class JsonIssueProposal(JsonProposal):
    claim_assessments: list[Assessment] = Field(min_length=1)
    cvp_impacts: list[Impact]


class IssuesDetected(ValueError):
    def __init__(self, findings, *, origin, report, correction_used=False, terminal_refusal=False):
        super().__init__("Claim management issue; no CVP commit before disposition")
        self.findings, self.origin, self.report = findings, origin, report
        self.correction_used, self.terminal_refusal = correction_used, terminal_refusal


def prior_management_action(evidence, findings):
    action=evidence.get('action',{})
    materials=action.get('materials',{})
    context=materials.get('claim_issue_context') or materials.get('claim_management_resolution')
    if context and {f['claim_id'] for f in findings}&{i['claim_id'] for i in context['issues']}:
        return context.get('management_action_id') or action['action_id']
    return None


def make_issue(claim, evidence, source, *, spans, origin, report, reason,
               correction_used=False, terminal_refusal=False, snapshots=None, fragments=()):
    statement = claim["statement"]
    if len(spans) != len(set(tuple(s) for s in spans)) or any(
            lo < 0 or hi <= lo or statement[lo:hi] != text for lo, hi, text in spans):
        raise ValueError("issue span is not an exact current Claim component")
    c.bound_bytes(report, snapshots)
    return Issue(claim_id=claim["claim_id"], claim_revision=claim["revision"],
        claim_sha256=semantic_record_sha(claim), source_id=source["source_id"],
        source_file_sha256=source["source_file_sha256"],
        evidence_sha256=sha256_json({"evidence":evidence,"fragments":sorted(fragments,key=lambda f:f['fragment_id'])}),
        spans=sorted(spans), origin=origin, report=report, reason=reason,
        correction_used=correction_used, terminal_refusal=terminal_refusal)


def review_findings(proposal, review):
    """Typed codes only: no matching prose, no relabelling link-only findings."""
    values = []
    by_claim = {r.claim_id: r for r in proposal.claim_decisions}
    if review is None:
        for row in proposal.claim_decisions:
            for component in row.components:
                if component.disposition == 'deferred':
                    values.append({'claim_id':row.claim_id,
                        'spans':[(s.start_char,s.end_char,s.exact_text) for s in component.spans],
                        'reason':component.reason})
        return values
    for row in review.change_reviews:
        if getattr(row, "coverage_requirement", None) not in {"evidence_insufficient", "attribution_unresolved", "source_conflict"}:
            continue
        component = by_claim[row.claim_id].components[row.component_index]
        values.append({"claim_id": row.claim_id,
            "spans": [(s.start_char, s.end_char, s.exact_text) for s in component.spans],
            "reason": row.coverage_reason})
    return values


def audit_claim_findings(packet, report):
    """Only GPT-upheld layer-3 Claim findings, never rejected/link-only cases."""
    upheld={a['item_id'] for a in report['actions'] if a['verdict']=='audit_objection_upheld'}
    return [{'claim_id':i['claim']['claim_id'],
             'spans':[(0,len(i['claim']['statement']),i['claim']['statement'])],
             'reason':json.dumps(next(a for a in report['actions'] if a['item_id']==i['item_id']),ensure_ascii=False)}
            for i in packet['items'] if i['item_id'] in upheld and i['item_id'].startswith('3:')]


def declined_supports_findings(targets, review):
    """A declined link proposal requests Claim/source classification, not repair approval.

    No reason-text matching or invented typed verdict: the original full verdict
    is diagnostic material. Claim management may conclude cvp_link instead of
    proposing any source change. Its existing policy and independent review are
    still required; the declined link never receives another review budget.
    """
    claims = {}
    links = set()
    for target in targets:
        claim = target['claim']; cid = claim['claim_id']
        link=target['before_link']; lid=link['viewpoint_claim_link_id']
        if (link['claim_id']!=cid or lid in links or not claim['statement']
                or link.get('effective_state','active')!='active'
                or link['link_type'] not in {'equivalent_full','equivalent_component'}):
            raise ValueError('declined supports foreign/duplicate/inactive scope')
        links.add(lid)
        if cid in claims and claims[cid] != claim:
            raise ValueError('declined supports conflicting Claim generations')
        claims[cid] = claim
    verdicts = review.verdicts
    if (not claims or len(verdicts) != len(claims)
            or {v.claim_id for v in verdicts} != set(claims)):
        raise ValueError('declined supports review population mismatch')
    return [{'claim_id':v.claim_id,
             'spans':[(0,len(claims[v.claim_id]['statement']),claims[v.claim_id]['statement'])],
             'reason':json.dumps({'diagnostic_origin':'declined_supports_postimage',
                 'content_approval':False,'verdict':v.model_dump(mode='json'),
                 'connection_ids':[t['before_link']['viewpoint_claim_link_id'] for t in targets
                                   if t['claim']['claim_id']==v.claim_id]},ensure_ascii=False)}
            for v in verdicts if not v.approved and v.problems]


def declined_supports_dependency(issue, value, claim_state):
    """Admit only a current, already-scoped dependency for source diagnosis.

    Authenticate the actual declined transport rather than trusting finder prose.
    This does not add it to the frozen group or approve any content change.
    """
    from pathlib import Path
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,read_saved_model_response,subscription_client
    from backend.pipeline.viewpoint_audit_link_repair import link_review_adapter,PROMPT
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review
    report=Path(issue.report.path); action_root=report.parent
    if (issue.origin not in {'opus_initial','gpt_proposal'}
            or report.name not in {'raw-opus.json','raw-gpt.json'}
            or value.get('execution_roots',{}).get(action_root.name)!=str(action_root)
            or claim_state['retired'] or claim_state['payload'].get('review_status') in HOLDS):
        raise ValueError('declined supports dependency lacks original bound action')
    packet=read_artifact(action_root/'packet.json')
    if packet.get('schema_version')!='wang_audit_link_repair_packet_v1':
        raise ValueError('declined supports dependency wrong packet')
    targets=packet['targets']; ids={t['claim']['claim_id'] for t in targets}
    provider,model=('claude','claude-opus-5-5') if report.name=='raw-opus.json' else ('codex','gpt-6.1-sol')
    expected_origin='opus_initial' if provider=='claude' else 'gpt_proposal'
    if issue.origin!=expected_origin:raise ValueError('declined supports dependency reviewer origin drift')
    adapter=link_review_adapter(subscription_client(provider,model,'high'),PROMPT,
        contextual_semantics=True,multiple_targets_per_claim=len(ids)!=len(targets))
    review=Review.model_validate(read_saved_model_response(adapter,packet,report))
    findings=declined_supports_findings(targets,review)
    matching=[f for f in findings if f['claim_id']==issue.claim_id]
    current=[t['claim'] for t in targets if t['claim']['claim_id']==issue.claim_id]
    admitted={(p['collection'],p['object_id']):p['pin'] for p in value['reviewed']['evidence']['read_pins']}
    if (len(matching)!=1 or matching[0]['reason']!=issue.reason
            or matching[0]['spans']!=[tuple(s) for s in issue.spans]
            or not current or any(c!=claim_state['payload'] for c in current)
            or admitted.get(('claims',issue.claim_id))!=[claim_state['revision'],claim_state['content_sha256'],False]
            or issue.claim_revision!=claim_state['payload']['revision']
            or issue.claim_sha256!=semantic_record_sha(claim_state['payload'])):
        raise ValueError('declined supports dependency current scope/pin/finding drift')


def eligibility_review_candidate(evidence, states, sid):
    """A classification may be reviewed; this is NOT content approval."""
    if (evidence.get('support_eligibility') != 'context_only'
            or evidence.get('review_status') in HOLDS
            or evidence.get('speaker') != 'professor' or evidence.get('stance') != 'asserted'
            or not evidence.get('discourse_role')):
        return False
    fragments = bound_fragments([evidence], lambda fid: states.get(c.key('source_fragments', fid), {}))
    return bool(fragments) and all(
        f.get('source_id') == sid and f.get('review_status') not in HOLDS
        and f.get('verbatim_excerpt') for f in fragments)


def eligibility_profile(packet):
    context = packet.get('claim_issue_context', {})
    binding = context.get('policy')
    return bool(binding and read_policy(binding).evidence_eligibility_review)


def policy_permissions(policy, states, sid, claim_ids, *, retained_occurrences=(), current_anchor_scope=False):
    if (not set(policy.claim_fields) <= CLAIM_FIELDS or not set(policy.evidence_fields) <= EVIDENCE_FIELDS
            or not set(policy.link_fields) <= LINK_FIELDS):
        raise ValueError("Claim issue policy has unsupported fields/hold/source expansion")
    inv = c.claim_inventory(states, sid, claim_ids, retained_occurrences=retained_occurrences)
    affected = set(inv["affected_keys"])
    result = []
    for k in sorted(affected):
        col, oid = c.split_key(k)
        fields = policy.claim_fields if col == "claims" and oid in claim_ids else (
            policy.evidence_fields if col == "evidence_steps" else policy.link_fields if col == "viewpoint_claim_links" else [])
        if col=='evidence_steps' and policy.evidence_eligibility_review and eligibility_review_candidate(states[k],states,sid):
            result.append(c.Permission(collection=col,object_id=oid,fields=sorted(ELIGIBILITY_FIELDS)))
            continue
        if fields and states[k].get("review_status") not in HOLDS:
            if col == "evidence_steps" and (states[k].get("valid_for_identity_review") is False
                    or states[k].get("support_eligibility") not in {"eligible", "eligible_candidate", "eligible_with_label"}):
                if policy.evidence_eligibility_review and eligibility_review_candidate(states[k], states, sid):
                    # Qualification only: do not rewrite speech/ownership to buy eligibility.
                    result.append(c.Permission(collection=col, object_id=oid, fields=sorted(ELIGIBILITY_FIELDS)))
                continue
            if col == 'claims' and not current_anchor_scope and any(r['claim_id'] == oid for r in retained_occurrences):
                fields = set(fields) - {'occurrences'}
            result.append(c.Permission(collection=col, object_id=oid, fields=sorted(set(fields))))
    if policy.evidence_fields:
        result.append(c.Permission(collection="source_fragments", object_id="FR-claim-issue-allocation-slot",
            create=True, parent_id=sid, fields=sorted(c.KNOWLEDGE_COLLECTIONS["source_fragments"][0].model_fields)))
    for permission in policy.cvp_permissions:
        if permission.collection not in {"canonical_viewpoints", "viewpoint_revisions", "viewpoint_relations"}:
            raise ValueError("CVP permission crosses management boundary")
        target = c.key("canonical_viewpoints", permission.parent_id) if permission.create else permission.key
        if target not in affected:
            raise ValueError("CVP permission is not in the exact affected set")
        if "effective_state" in permission.fields or "identity_status" in permission.fields:
            raise ValueError("CVP retirement/identity needs a separate exact direction")
        result.append(permission)
    return result


def validate_authority(manifest, states, snapshots=None):
    context = json.loads(c.bound_bytes(manifest.claim_issue, snapshots))
    if context.get("schema_version") != VERSION:
        raise ValueError("unknown Claim issue context")
    policy = read_policy(context["policy"], snapshots)
    issues = [Issue.model_validate(r) for r in context["issues"]]
    if context.get('issue_identities') != issue_identities(issues):
        raise ValueError('program Claim issue routing identities drift')
    ids = sorted({i.claim_id for i in issues})
    if context.get("claim_ids") != ids or len({i.issue_id for i in issues}) != len(issues):
        raise ValueError("Claim issue denominator duplicate/drift")
    if (manifest.deferred_repair or manifest.prior_updates or len(manifest.current_sources) != 1
            or manifest.current_sources[0] != manifest.before_source):
        raise ValueError("Claim issue cannot change/migrate source or reuse deferred budget")
    for issue in issues:
        claim = states[c.key("claims", issue.claim_id)]
        source = states[c.key("source_documents", issue.source_id)]
        evidence = [states[c.key("evidence_steps", eid)] for eid in sorted(claim["evidence_step_ids"])]
        fragments = bound_fragments(evidence, lambda fid:states[c.key('source_fragments',fid)])
        rebuilt = make_issue(claim, evidence, source, spans=issue.spans, origin=issue.origin,
            report=issue.report, reason=issue.reason, correction_used=issue.correction_used,
            terminal_refusal=issue.terminal_refusal, snapshots=snapshots, fragments=fragments)
        if issue.source_id != manifest.source_id or issue != rebuilt or claim.get("review_status") in HOLDS:
            raise ValueError("issue current Claim/source/evidence identity/hold drift")
    auth = json.loads(c.bound_bytes(manifest.authorization, snapshots))
    if (auth.get("claim_issue") != manifest.claim_issue.model_dump() or auth.get("policy") != context["policy"]
            or auth.get("authorization_id") != policy.authorization_id):
        raise ValueError("issue policy not bound to actual owner authority")
    profile=context.get('current_occurrence_anchor_scope')
    if profile not in (None, 'current_source_anchors_historical_exact_v1'):
        raise ValueError('unknown retained occurrence anchor scope')
    expected=policy_permissions(policy, states, manifest.source_id, ids,
        retained_occurrences=context.get('retained_occurrence_context', []), current_anchor_scope=bool(profile))
    if context.get('scope_amendment'):
        from backend.pipeline.claim_issue_rescope import permissions
        direction=json.loads(c.bound_bytes(context['scope_amendment'],snapshots))
        from backend.pipeline.claim_evidence_completion import evidence_permission_ids
        expected=[p for p in expected if p.collection!='evidence_steps' or p.object_id in evidence_permission_ids(direction)]
        from backend.pipeline.claim_evidence_completion import direction_for, merge_permissions
        extra=permissions(context,states,snapshots)
        expected=merge_permissions(expected,extra) if direction_for(context,snapshots) else expected+extra
    if manifest.permissions != expected:
        raise ValueError("Claim/CVP issue permission set exceeds separate domain authority")
    return context


def read_policy(binding, snapshots=None):
    value = json.loads(c.bound_bytes(binding, snapshots))
    if "artifact_sha256" in value:
        stated = value.pop("artifact_sha256")
        if stated != sha256_json(value):
            raise ValueError("owner policy seal drift")
    return Policy.model_validate(value)


def intake(store, run_id, findings, *, origin, report, correction_used=False, terminal_refusal=False):
    """All finders share exact current identity and the existing SQL run table."""
    records = store.read_cvp_context(sorted({f["claim_id"] for f in findings}))
    issues = []
    for finding in findings:
        claim = records["claims", finding["claim_id"]]["payload"]
        evidence = [records["evidence_steps", eid]["payload"] for eid in sorted(claim["evidence_step_ids"])]
        fragments = bound_fragments(evidence, lambda fid:records['source_fragments',fid]['payload'])
        source_ids = {records["source_fragments", fid]["payload"]["source_id"] for e in evidence
                      for fid in set(e.get("source_fragment_ids", [])) | ({e["source_fragment_id"]} if e.get("source_fragment_id") else set())}
        if len(source_ids) != 1:
            raise ValueError("Claim issue has no unique own source; engineering/source owner must supply proof")
        source = records["source_documents", next(iter(source_ids))]["payload"]
        issue = make_issue(claim, evidence, source, spans=finding["spans"], origin=origin,
            report=report, reason=finding["reason"], correction_used=correction_used,
            terminal_refusal=terminal_refusal, fragments=fragments)
        store.register_claim_issue(run_id, issue.model_dump(mode="json"))
        issues.append(issue)
    return issues


def bound_fragments(evidence, read):
    ids={fid for e in evidence for fid in set(e.get('source_fragment_ids',[])) |
         ({e['source_fragment_id']} if e.get('source_fragment_id') else set())}
    return [read(fid) for fid in sorted(ids)]


def issue_identities(issues):
    # Opaque routing IDs are supplied, never calculated from hashes by a model.
    return [{'issue_id':i.issue_id,'claim_id':i.claim_id,'spans':[list(s) for s in i.spans]} for i in issues]


def manifest_for_issues(store, run_id, issues, policy_binding, root):
    """Program scopes a pre-authorized policy; no human content approval forged."""
    from backend.pipeline.source_reconciliation_runner import write, binding
    issues = [Issue.model_validate(i) if isinstance(i, dict) else i for i in issues]
    if not issues or len({i.source_id for i in issues}) != 1:
        raise ValueError("issue repair uses one own source at a time, not a mixed-source extraction")
    saved = store.get_cvp_group(run_id)
    if not saved:
        raise ValueError("issue repair changed original group membership")
    for i in issues:
        if i.claim_id not in saved['claim_ids']:
            from backend.pipeline.claim_owner_revision import dependency_issue
            dependency_issue(i,saved,store.get_record_state('claims',i.claim_id))
    policy = read_policy(policy_binding)
    sid = issues[0].source_id
    records = store.read_affected_context([("claims", i.claim_id) for i in issues])
    states = {c.key(col, oid): r["payload"] for (col, oid), r in records.items() if not r.get("retired")}
    doc = states[c.key("source_documents", sid)]
    source = c.FileBinding(path=doc["source_path"], file_sha256=doc["source_file_sha256"])
    c.source_snapshot(source, source=doc)  # physical/body qualification before any model
    ids = sorted({i.claim_id for i in issues})
    retained = []
    aliases = {sid, doc.get('transcript_id'), doc.get('canonical_source_id')} - {None, ''}
    for cid in ids:
        if any(not aliases.intersection(set(c.strings(o))) for o in states[c.key('claims', cid)]['occurrences']):
            from backend.pipeline.claim_retained_occurrence_context import build
            receipt = build(store, cid, sid)
            pin = receipt['historical_source_pin']
            row = store.get_record_state(pin['collection'], pin['object_id'])
            states[c.key(pin['collection'], pin['object_id'])] = row['payload']
            retained.append(receipt)
    permissions = policy_permissions(policy, states, sid, ids, retained_occurrences=retained,
                                     current_anchor_scope=True)
    definition = store.get_cvp_campaign(saved["campaign_sha256"])
    progress = {r["group_key"]:r for r in store.cvp_campaign_progress(saved["campaign_sha256"])}
    groups = [c.Group(group_key=g["group_key"], claim_ids=g["claim_ids"],
                     revisit_required=p.get("revisit_required", False),
                     independent_audit_complete=p.get("independent_audit_complete", True))
              for g in definition["groups"]
              for p in [progress.get(g["group_key"], {})]]
    write(root/"membership.json", {"groups":[{"group_key":g.group_key,"claim_ids":g.claim_ids} for g in groups]})
    context = {"schema_version":VERSION, "claim_ids":ids, "issues":[i.model_dump(mode="json") for i in issues],
               "issue_identities":issue_identities(issues),
               "anchor_binding_validation_profile":"declared_local_own_containing_v1",
               "policy":policy_binding, "original_run_id":run_id}
    if retained:
        context['retained_occurrence_context'] = retained
        context['current_occurrence_anchor_scope'] = 'current_source_anchors_historical_exact_v1'
    write(root/"issue-context.json", context)
    issue_binding = binding(root/"issue-context.json")
    write(root/"scoped-authorization.json", {"source_id":sid, "authorization_id":policy.authorization_id,
        "authorization_quote":policy.authorization_quote, "human_content_approval":False,
        "policy":policy_binding,"claim_issue":issue_binding,"permissions":[p.model_dump() for p in permissions]})
    return c.Manifest(source_id=sid, before_source=source, current_sources=[source],
        # Source is unchanged; full body remains model context, but this is not
        # a new sentence-extraction campaign over unrelated sermon paragraphs.
        authorization=binding(root/"scoped-authorization.json"), authorized_paragraph_keys=[],
        permissions=permissions, groups=groups, campaign_claim_ids=[cid for g in groups for cid in g.claim_ids],
        accepted_membership=binding(root/"membership.json"), created_at="program-scoped-existing-owner-policy",
        claim_issue=issue_binding)


def handle_issues(store, run_id, detected, *, root, policy_binding=None, ports=None, prior_management_action=None):
    """Persist discovery before permission/terminal checks; never ask for 'continue'."""
    issues = intake(store, run_id, detected.findings, origin=detected.origin, report=detected.report,
                    correction_used=detected.correction_used, terminal_refusal=detected.terminal_refusal)
    existing = [store.get_claim_issue(i.issue_id) for i in issues]
    if prior_management_action:
        raise ValueError('Claim issue after joint management repair '+prior_management_action+
                         '; original facts retained, no automatic second repair cycle')
    if any(r["terminal_refusal"] or r["correction_used"] for r in existing):
        raise ValueError("existing same-issue terminal/correction budget requires exact owner decision")
    if policy_binding is None:
        raise ValueError("Claim issue recorded; owner must approve bounded Claim/Evidence and CVP fields once")
    from pathlib import Path
    from backend.pipeline.cvp_sql_actions import execute_source_action
    from backend.pipeline.source_reconciliation_runner import read, write, body
    source_sets = {}
    for issue in issues:
        source_sets.setdefault(issue.source_id, []).append(issue)
    # Each own-source action is exact-once. A partially completed multi-source
    # intake does not discard earlier commits or reset later calls.
    results = []
    for sid, selected in sorted(source_sets.items()):
        identity = sha256_json(sorted(i.issue_id for i in selected))
        work = Path(root)/identity
        manifest_path = work/"manifest.json"
        if manifest_path.exists():
            manifest = c.Manifest.model_validate(body(read(manifest_path)))
            context = json.loads(c.bound_bytes(manifest.claim_issue))
            if context["policy"] != policy_binding:
                raise ValueError("original issue policy must resume, not reset")
        else:
            manifest = manifest_for_issues(store, run_id, selected, policy_binding, work)
            write(manifest_path, manifest.model_dump(mode="json"))
        results.append(execute_source_action(store, run_id, manifest.model_dump(mode="json"), root=work/"action", ports=ports))
    if results and all(r.get('status')=='already_applied' for r in results):
        raise ValueError('same adjudicated Claim issue remains unresolved after its committed repair; no repair loop')
    return results


def normalize_proposal(frozen, response):
    """Reuse only declared deterministic shape normalization BEFORE review."""
    from backend.pipeline.deferred_claim_repair import decode_proposal, normalize_proposal as normalize
    fields = {k: response[k] for k in ("claim_assessments", "cvp_impacts")}
    base = {k: v for k, v in response.items() if k not in fields}
    normalized = normalize(frozen, decode_proposal(base))
    return Proposal.model_validate({**normalized.model_dump(), **fields})


def validate_resolution(frozen, proposal, post, *, review_only=False):
    proposal = Proposal.model_validate(proposal.model_dump())
    before = c.index(frozen["before_records"])
    context = frozen["claim_issue_context"]
    issues = {Issue.model_validate(r).issue_id: Issue.model_validate(r) for r in context["issues"]}
    policy = read_policy(context['policy'], frozen.get('physical_snapshots'))
    actual = [a.issue_id for a in proposal.claim_assessments]
    if len(actual) != len(set(actual)) or set(actual) != set(issues):
        raise ValueError("every exact Claim issue requires one assessment")
    paragraphs = {frozen["manifest"]["source_id"]: frozen["after"][0]["paragraphs"],
                  **{s["source_id"]: s["paragraphs"] for s in frozen["context_sources"]}}
    def citations(values, *, own=None):
        for value in values:
            text = paragraphs.get(value.source_id, {}).get(value.paragraph_key, {}).get("text", "")
            if (own and value.source_id != own) or text.count(value.exact_quote) != 1:
                raise ValueError("assessment quote not exact own approved body; editorial heading is not evidence")
    for a in proposal.claim_assessments:
        issue = issues[a.issue_id]
        citations(a.citations, own=issue.source_id)
        if a.cause == "source_unresolved":
            raise ValueError("source/attribution uncertainty needs content owner")
        k = c.key("claims", issue.claim_id)
        changed_statement = any(before[k].get(f) != post[k].get(f) for f in ("statement", "claim_type", "attribution", "discourse_mode", "scripture_refs"))
        if changed_statement and a.cause != "claim_statement":
            raise ValueError("Evidence/link repair cannot rewrite the Claim meaning")
        if a.cause in {"already_sufficient", "cvp_link"} and post[k] != before[k]:
            raise ValueError("correct Claim must be retained")
        if a.cause in {'already_sufficient','cvp_link'}:
            for eid in before[k]['evidence_step_ids']:
                ek=c.key('evidence_steps',eid)
                if post[ek]!=before[ek]:
                    raise ValueError('correct Claim/Evidence must be retained; link diagnosis is not Evidence permission')
        if post[k].get('evidence_step_ids') != before[k].get('evidence_step_ids'):
            raise ValueError('Claim issue cannot replace the existing Evidence ownership buckets')
        if a.cause in {'evidence_binding','claim_statement'}:
            evidence=[post[c.key('evidence_steps',eid)] for eid in post[k]['evidence_step_ids']]
            usable=[e for e in evidence if e.get('valid_for_identity_review') is not False
                    and e.get('support_eligibility') in {'eligible','eligible_candidate','eligible_with_label'}
                    and e.get('review_status') not in HOLDS]
            fragments=bound_fragments(usable,lambda fid:post[c.key('source_fragments',fid)])
            bound_citations=[any(f.get('source_id')==cite.source_id and f.get('paragraph_key')==cite.paragraph_key
                                and cite.exact_quote in f.get('verbatim_excerpt','') for f in fragments)
                             for cite in a.citations]
            # A statement diagnosis may cite additional own-body context to
            # explain why the OLD wording was too strong. Those citations are
            # not new Evidence: all were authenticated above, and the actual
            # after-state must still have bound supporting citations plus the
            # independent complete-postimage review. A binding repair, in
            # contrast, must materialize every claimed new Evidence citation.
            qualification_only = [e for e in evidence
                if post[c.key('evidence_steps', e['evidence_step_id'])] != before[c.key('evidence_steps', e['evidence_step_id'])]]
            qualification_only = (qualification_only if policy.evidence_eligibility_review and post[k] == before[k]
                and qualification_only and all(
                    {f:v for f,v in c._semantic(e).items() if f not in ELIGIBILITY_FIELDS}
                    == {f:v for f,v in c._semantic(before[c.key('evidence_steps', e['evidence_step_id'])]).items()
                        if f not in ELIGIBILITY_FIELDS}
                    for e in qualification_only)
                and all(post[fk] == before.get(fk) for fk in post if fk.startswith('source_fragments/')) else [])
            # Qualification-only review does not propose any new quote binding.
            # Additional full-own-body diagnostic citations stay diagnostic;
            # each requalified Evidence must still have an actual bound cite.
            if a.cause == 'evidence_binding' and qualification_only:
                represented = all(any(
                    f.get('source_id') == cite.source_id and f.get('paragraph_key') == cite.paragraph_key
                    and cite.exact_quote in f.get('verbatim_excerpt', '')
                    for f in bound_fragments([e], lambda fid: post[c.key('source_fragments', fid)])
                    for cite in a.citations) for e in qualification_only)
            else:
                represented=all(bound_citations) if a.cause=='evidence_binding' else any(bound_citations)
            if not represented and a.cause=='evidence_binding' and context.get('complete_input_repair'):
                from backend.pipeline.claim_citation_purpose_review import items
                purposes=items(frozen,proposal,post)
                represented=any(bound_citations) and all(bound or any(
                    p['record_key']==f'claim_citation_purpose/{a.issue_id}/{index}' for p in purposes)
                    for index,bound in enumerate(bound_citations))
            if not represented:
                raise ValueError('Evidence diagnosis must be represented by actual eligible bound Evidence')
            if a.cause=='evidence_binding' and post[k]==before[k] and all(
                    post[c.key('evidence_steps',eid)]==before[c.key('evidence_steps',eid)]
                    for eid in before[k]['evidence_step_ids']):
                raise ValueError('Evidence binding repair must change an actual binding; otherwise classify already sufficient')
    required = {k for k in set(frozen["inventory"]["affected_keys"])|{row.key for row in proposal.records} if c.split_key(k)[0] in {
        "viewpoint_claim_links", "canonical_viewpoints", "viewpoint_revisions", "viewpoint_relations"}}
    actual = [i.record_key for i in proposal.cvp_impacts]
    extra=set(actual)-required
    retained_identity_extras=(frozen.get('owner_claim_completion') and required<=set(actual)
        and all(c.split_key(k)[0] in {'viewpoint_identity_candidates','viewpoint_identity_decisions'}
            and k in before and post[k]==before[k]
            and next(d.action for d in proposal.dispositions if d.record_key==k)=='impact_only'
            and next(i.action for i in proposal.cvp_impacts if i.record_key==k)=='retain'
            for k in extra))
    retained_context_extras=(context.get('complete_input_repair') and required<=set(actual)
        and all(k in frozen['inventory']['affected_keys'] and k in before and post[k]==before[k]
            and c.split_key(k)[0] in {'viewpoint_identity_candidates','viewpoint_identity_decisions',
                                     'claim_relations','knowledge_relations'}
            and before[k].get('review_status') not in HOLDS
            and next(d.action for d in proposal.dispositions if d.record_key==k) in {'retain','impact_only'}
            and next(i.action for i in proposal.cvp_impacts if i.record_key==k)=='retain'
            for k in extra))
    retained_extras=retained_identity_extras or retained_context_extras
    if len(actual) != len(set(actual)) or (set(actual) != required and not retained_extras):
        raise ValueError("complete CVP impact denominator including retained members required")
    dispositions = {d.record_key: d.action for d in proposal.dispositions}
    for impact in proposal.cvp_impacts:
        citations(impact.citations)
        if retained_extras and impact.record_key in extra:
            # The additional rows describe immutable discovery/decision history,
            # not fresh CVP membership approval. Their ordinary impact-only
            # disposition and exact retained payload were checked above; keep
            # the model's rows/reasons visible without widening the write set.
            continue
        if (review_only and impact.action == 'needs_direction'
                and dispositions[impact.record_key] == 'unresolved'
                and post[impact.record_key] == before[impact.record_key]):
            continue
        expected = "retain" if impact.action == "retain" else "update"
        if impact.action == "needs_direction" or dispositions[impact.record_key] != expected:
            raise ValueError("CVP impact unresolved or differs from exact after-state")
    if proposal.unadmitted_candidates:
        raise ValueError("Claim issue cannot add/split original campaign judgments")
    own_evidence={eid for issue in issues.values() for eid in post[c.key('claims',issue.claim_id)]['evidence_step_ids']}
    from backend.pipeline.claim_evidence_completion import direction_for, validate_postimages
    completion=direction_for(context,frozen.get('physical_snapshots'))
    if completion:
        validate_postimages(frozen,proposal,post)
        own_evidence.update(completion['evidence_ids'])
    used_fragments={f['fragment_id'] for f in bound_fragments([post[c.key('evidence_steps',eid)] for eid in own_evidence],
                                                           lambda fid:post[c.key('source_fragments',fid)])}
    if any(c.split_key(k)[0]=='source_fragments' and c.split_key(k)[1] not in used_fragments for k in post.keys()-before.keys()):
        raise ValueError('new quote fragment must be bound to actual own Claim Evidence, not orphan source extraction')
    for k, old in before.items():
        if post[k] == old:
            continue
        col, _ = c.split_key(k)
        if old.get("review_status") in HOLDS or col == "source_documents":
            raise ValueError("Claim issue cannot clear hold or change original source")
        if col == "evidence_steps":
            if completion and c.split_key(k)[1] in completion['evidence_ids']:
                continue  # Exact new binding/qualification was verified above; independently reviewed, not auto-approved.
            eligibility_changed = any(old.get(f) != post[k].get(f) for f in ELIGIBILITY_FIELDS)
            if eligibility_changed:
                if (not policy.evidence_eligibility_review
                        or not eligibility_review_candidate(old, before, frozen['manifest']['source_id'])
                        or post[k].get('support_eligibility') != 'eligible_with_label'
                        or post[k].get('valid_for_identity_review') is False
                        or {f:v for f,v in c._semantic(old).items() if f not in ELIGIBILITY_FIELDS}
                           != {f:v for f,v in c._semantic(post[k]).items() if f not in ELIGIBILITY_FIELDS}):
                    raise ValueError('Evidence eligibility review cannot clear hold, alter attribution/body or bypass policy')
                eid = c.split_key(k)[1]
                owners = [a for a in proposal.claim_assessments
                          if eid in before[c.key('claims', issues[a.issue_id].claim_id)]['evidence_step_ids']]
                fragments = bound_fragments([old], lambda fid: before[c.key('source_fragments', fid)])
                if not owners or any(a.cause != 'evidence_binding' or not any(
                        cite.source_id == fragment['source_id'] and cite.paragraph_key == fragment['paragraph_key']
                        and cite.exact_quote in fragment['verbatim_excerpt']
                        for cite in a.citations for fragment in fragments) for a in owners):
                    raise ValueError('Evidence eligibility review requires exact own bound quote and binding assessment')
                # All shared owners must have their own exact issue/assessment.
                if set(old.get('produced_claim_ids', [])) - {issues[a.issue_id].claim_id for a in owners}:
                    raise ValueError('Evidence eligibility review lacks shared Claim assessments')
                continue
            old_ids=set(old.get('source_fragment_ids',[]))|({old['source_fragment_id']} if old.get('source_fragment_id') else set())
            new_ids=set(post[k].get('source_fragment_ids',[]))|({post[k]['source_fragment_id']} if post[k].get('source_fragment_id') else set())
            if (old.get("valid_for_identity_review") is False
                    or old.get("support_eligibility") not in {"eligible", "eligible_candidate", "eligible_with_label"}
                    or not old_ids <= new_ids):
                raise ValueError("Evidence repair cannot promote held evidence or discard quotes")
        if col == "viewpoint_claim_links":
            new = post[k]
            if context.get('scope_amendment'):
                from backend.pipeline.claim_issue_rescope import permissions
                exact={p.key:p for p in permissions(context,before,frozen.get('physical_snapshots'))}
                if k in exact and set(exact[k].fields)=={'effective_state'}:
                    if (new.get('effective_state')!='retired' or
                            {f:v for f,v in c._semantic(old).items() if f!='effective_state'} !=
                            {f:v for f,v in c._semantic(new).items() if f!='effective_state'}):
                        raise ValueError('exact link withdrawal must retain role/endpoints/body')
                    continue
            if any(old.get(f) != new.get(f) for f in ("claim_id", "viewpoint_id", "effective_state")):
                raise ValueError("Claim issue cannot retarget/retire a link")
    return post
