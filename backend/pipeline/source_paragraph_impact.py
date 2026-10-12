"""Separate a changed paragraph's semantic graph from source epoch bookkeeping.

This compiler is pure: it does not approve content, change a model answer, or
write knowledge rows. The complete source readset remains the transaction and
invalidation denominator. Only unchanged paragraphs can enter the binding-only
plane; full CVP member/source context remains in the semantic plane.
"""
from collections import deque
from copy import deepcopy
import hashlib

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import source_reconciliation as c

VERSION = "wang_source_paragraph_impact_v1"


def partition(manifest, states, before, after):
    """Return exact semantic scope and an independently verifiable binding plan.

    No source-wide alias is a semantic root. Every changed paragraph fragment,
    direct own occurrence, and named reverse consumer IS a root/dependency.
    The source-wide inventory is still retained for invalidation and audits.
    """
    manifest = c.Manifest.model_validate(manifest)
    sid = manifest.source_id
    doc_key = c.key("source_documents", sid)
    doc = states[doc_key]
    old_paragraphs, new_paragraphs = before["paragraphs"], after["paragraphs"]
    if set(old_paragraphs) != set(new_paragraphs):
        raise ValueError("paragraph scope cannot insert/remove/reorder source paragraphs")
    changed = {k for k in old_paragraphs if old_paragraphs[k] != new_paragraphs[k]}
    if not changed or not changed <= set(manifest.authorized_paragraph_keys):
        raise ValueError("paragraph scope requires actual bounded source changes")
    for k in changed:
        if ({f: v for f, v in old_paragraphs[k].items() if f != "text"}
                != {f: v for f, v in new_paragraphs[k].items() if f != "text"}):
            raise ValueError("paragraph scope cannot change timing/coordinates/source-bearing fields")
    if (before["body_sha256"] != doc.get("source_body_sha256")
            or before["file"]["file_sha256"] != doc.get("source_file_sha256")
            or after["file"]["path"] != doc.get("source_path")):
        raise ValueError("paragraph scope source epoch/registered locator mismatch")
    permissions = {p.key: p for p in manifest.permissions}
    forward, reverse = c.dependency_index(states)
    own_fragments = {k for k, p in states.items()
                     if k.startswith("source_fragments/") and p.get("source_id") == sid}
    if not own_fragments:
        raise ValueError("paragraph scope requires complete own fragment inventory")
    roots, binding_rows = set(), {}
    for k in sorted(own_fragments):
        p = states[k]
        para = p.get("paragraph_key")
        quote = p.get("verbatim_excerpt")
        text = old_paragraphs.get(para, {}).get("text", "")
        # Unchanged text is not a new content approval or locator selection.
        # Preserve an already bound ambiguous quote's exact stored coordinates;
        # never choose a first occurrence or infer a new Evidence binding.
        if (not quote or not text or quote not in text
                or p.get("source_sha256") != before["body_sha256"]
                or p.get("paragraph_text_sha256") != hashlib.sha256(text.encode()).hexdigest()
                or p.get("verbatim_excerpt_sha256") != hashlib.sha256(quote.encode()).hexdigest()
                or p.get("anchor_state") != "source_version_bound"):
            raise ValueError("paragraph scope original fragment binding is incomplete/stale: " + k)
        if para in changed:
            roots.add(k)
        else:
            permission = permissions.get(k)
            if (permission is None or permission.create or permission.fields != ["source_sha256"]
                    or p.get("review_status") in {"human_hold", "withheld_unreviewed", "rejected"}):
                raise ValueError("binding-only fragment lacks exact permission or is held: " + k)
            new = deepcopy(p)
            new["source_sha256"] = after["body_sha256"]
            new["revision"] = p["revision"] + 1
            binding_rows[k] = new
    aliases = {sid, doc.get("transcript_id"), doc.get("canonical_source_id")} - {None, ""}
    # Anchors may reference local Evidence IDs rather than canonical fragment IDs.
    # They must be independently included, even when reverse JSON references
    # alone would not discover their dependency on the changed paragraph.
    for k, p in states.items():
        if not k.startswith("claims/"):
            continue
        for occurrence in p.get("occurrences", []):
            anchors = occurrence.get("anchors", [])
            if any(a.get("paragraph_key") in changed for a in anchors):
                if not aliases.intersection(c.strings(occurrence)):
                    continue  # The same S number in another sermon is not own.
                roots.add(k)
    if not roots:
        raise ValueError("changed paragraph has no indexed content root")
    affected = set(roots)
    queue = deque(sorted(roots))
    while queue:
        target = queue.popleft()
        for consumer in sorted(reverse[target] - affected):
            affected.add(consumer)
            if c.split_key(consumer)[0] in c.WRITABLE and not consumer.startswith("canonical_viewpoints/"):
                queue.append(consumer)
    views = {c.key("canonical_viewpoints", states[k]["viewpoint_id"])
             for k in affected if k.startswith("viewpoint_claim_links/")}
    for cv in views:
        if cv not in states:
            raise ValueError("paragraph scope missing current CVP")
        revision = c.key("viewpoint_revisions", states[cv]["current_revision_id"])
        if revision not in states:
            raise ValueError("paragraph scope missing current CVR")
        affected.update({cv, revision})
        affected.update(reverse[cv])  # ALL members/consumers, never top-k.
    context = affected | {doc_key}
    queue = deque(sorted(context))
    while queue:
        for target in sorted(forward[queue.popleft()] - context):
            context.add(target)
            queue.append(target)
    semantic = {
        "affected_keys": sorted(affected), "context_keys": sorted(context - affected),
        "unknown_keys": sorted(k for k in affected if c.split_key(k)[0] not in c.WRITABLE | c.IMPACT_ONLY),
        "reverse_edges": {k: sorted(reverse[k]) for k in sorted(affected)},
    }
    complete = c.inventory(states, sid)
    if not affected <= set(complete["affected_keys"]):
        raise ValueError("paragraph semantic scope is outside complete source impact")
    # Binding-only fragments can also be forward evidence of a semantic member.
    # In that case their FULL original payload stays in semantic context too.
    bindings = [{"record_key": k, "before_revision": states[k]["revision"],
                 "before_content_sha256": record_content_sha(states[k]),
                 "after_revision": p["revision"], "after_content_sha256": record_content_sha(p)}
                for k, p in sorted(binding_rows.items())]
    proof = {
        "schema_version": VERSION, "source_id": sid,
        "before_file": before["file"], "after_file": after["file"],
        "before_body_sha256": before["body_sha256"], "after_body_sha256": after["body_sha256"],
        "changed_paragraph_keys": sorted(changed), "semantic_roots": sorted(roots),
        "semantic_inventory": semantic, "complete_inventory_sha256": sha256_json(complete),
        "complete_current_records_sha256": sha256_json(states),
        "complete_affected_keys": complete["affected_keys"],
        "binding_only_rows": bindings,
        "unchanged_paragraphs_sha256": sha256_json({k: old_paragraphs[k] for k in sorted(set(old_paragraphs) - changed)}),
        "content_approval": False, "group_completion": False,
        "binding_rule": "Only source_sha256 and storage revision change; all quote, coordinate, ownership, qualification, review and payload fields remain exact.",
    }
    return {"proof": proof, "binding_postimages": c.rows(binding_rows)}


def verify(plan, manifest, states, before, after):
    """Recompute from exact source/current rows; a stored boolean is no authority."""
    if plan != partition(manifest, states, before, after):
        raise ValueError("paragraph scope/binding full postimage proof drift")
    return plan


def semantic_preview(frozen, plan):
    """No-call review-scope preview, NOT an executable or approved package.

    All source bytes remain physical-bound; full own body and ALL member sources
    remain visible. The complete source inventory/roster stay in saved material
    and transaction authority rather than being repeated as unrelated content.
    This preview cannot be passed to the common runner until its typed review,
    transaction and completion domains are integrated and independently tested.
    """
    states = c.index(frozen["before_records"])
    m = c.Manifest.model_validate(frozen["manifest"])
    verify(plan, m, states, frozen["before"], frozen["after"][0])
    proof = plan["proof"]
    inv = proof["semantic_inventory"]
    selected = set(inv["affected_keys"] + inv["context_keys"])
    source_ids = {states[k]["source_id"] for k in selected if k.startswith("source_documents/")}
    claims = {c.split_key(k)[1] for k in inv["affected_keys"] if k.startswith("claims/")}
    views = {c.split_key(k)[1] for k in inv["affected_keys"] if k.startswith("canonical_viewpoints/")}
    manifest = m.model_dump()
    manifest["permissions"] = [p.model_dump() for p in m.permissions if p.key in selected
                               and p.key not in {r["record_key"] for r in proof["binding_only_rows"]}]
    manifest["groups"] = [g.model_dump() for g in m.groups
                          if claims.intersection(g.claim_ids) or views.intersection(g.viewpoint_ids)]
    manifest.pop("campaign_claim_ids")
    return {
        "schema_version": VERSION, "scope_preview_only": True, "content_write_authority": False,
        "manifest_projection": manifest,
        "full_manifest_sha256": sha256_json(frozen["manifest"]),
        "campaign_member_count": len(m.campaign_claim_ids), "campaign_group_count": len(m.groups),
        "before_source": frozen["before"], "current_sources": frozen["after"],
        "context_sources": [s for s in frozen.get("context_sources", []) if s["source_id"] in source_ids],
        "sentence_inventory": frozen["sentence_inventory"], "inventory": inv,
        "records": c.rows({k: states[k] for k in sorted(selected)}),
        "source_epoch_binding_proof": proof,
        "execution_boundary": "Not a Proposal, Review or completion authority. No original model decisions/dispositions are removed or changed.",
    }
