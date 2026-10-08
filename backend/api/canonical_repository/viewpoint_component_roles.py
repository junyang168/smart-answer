"""One Claim may supply a conclusion and a distinct, evidenced reason.

Pure structural validation. Semantic role/evidence adequacy still requires the
normal reviewer and source audit; disjoint character ranges alone prove neither.
"""
from collections import defaultdict
from itertools import combinations

VERSION = "wang_cvp_component_roles_v1"
APPROVED = {"system_approved", "human_approved", "approved"}
ELIGIBLE = {"eligible", "eligible_candidate", "eligible_reviewed"}


def component_role_findings(links, claims, evidence_steps, fragments, *, claim_sha):
    """Check mixed conclusion/reason pairs in the complete affected graph.

    Existing same-role and cross-viewpoint uniqueness checks remain separate.
    No normalization, slicing, reassignment or automatic downgrade is performed.
    """
    groups = defaultdict(list)
    for link in links:
        if link.get("effective_state") == "active":
            groups[(link.get("claim_id"), link.get("viewpoint_id"))].append(link)
    findings = []
    for (cid, vid), rows in groups.items():
        for left, right in combinations(rows, 2):
            roles = {left.get("link_type"), right.get("link_type")}
            if "supports" not in roles or not roles & {"equivalent_full", "equivalent_component"}:
                continue
            prefix = f"{vid}/{cid}: conclusion/reason components"
            claim = claims.get(cid)
            if roles != {"equivalent_component", "supports"}:
                findings.append(prefix + " cannot overlap a whole-Claim membership")
                continue
            if not claim:
                findings.append(prefix + " missing Claim")
                continue
            ranges = []
            invalid = False
            for link in (left, right):
                loc = link.get("component_locator") or {}
                spans = loc.get("canonical_spans") or []
                statement = claim.get("statement") or ""
                if (link.get("review_status") not in APPROVED
                        or link.get("pinned_claim_revision") != claim.get("revision", 1)
                        or loc.get("claim_sha256") != claim_sha(claim)
                        or not spans or not link.get("evidence_bindings")):
                    invalid = True
                current = []
                text = []
                for span in spans:
                    start, end = span.get("start_char"), span.get("end_char")
                    exact = span.get("exact_text")
                    if (type(start) is not int or type(end) is not int
                            or not 0 <= start < end <= len(statement)
                            or statement[start:end] != exact):
                        invalid = True
                        continue
                    current.append((start, end))
                    text.append(exact)
                if "".join(text) != loc.get("statement_component"):
                    invalid = True
                if any(a < d and c < b for (a, b), (c, d) in combinations(current, 2)):
                    invalid = True
                ranges.append(current)
                for binding in link.get("evidence_bindings") or []:
                    eid, fid = binding.get("evidence_step_id"), binding.get("source_fragment_id")
                    step, fragment = evidence_steps.get(eid), fragments.get(fid)
                    if (not step or not fragment or eid not in (claim.get("evidence_step_ids") or [])
                            or step.get("support_eligibility") not in ELIGIBLE
                            or step.get("valid_for_identity_review") is False
                            or fragment.get("valid_for_identity_review") is False
                            or fragment.get("anchor_state") != "source_version_bound"
                            or not str(fragment.get("verbatim_excerpt") or "").strip()
                            or fid not in ([step.get("source_fragment_id")] + (step.get("source_fragment_ids") or []))):
                        invalid = True
            if left.get("validated_against_viewpoint_revision_id") != right.get("validated_against_viewpoint_revision_id"):
                invalid = True
            if invalid:
                findings.append(prefix + " require approved current pins, exact spans and eligible bound evidence")
            elif any(a < d and c < b for a, b in ranges[0] for c, d in ranges[1]):
                findings.append(prefix + " overlap")
    return findings


def connection_counts(links):
    active = [r for r in links if r.get("effective_state") == "active"]
    return {"linked_claims": len({r["claim_id"] for r in active}),
            "linked_connections": len(active),
            "member_claims": len({r["claim_id"] for r in active
                                  if r.get("link_type") in {"equivalent_full", "equivalent_component"}})}
