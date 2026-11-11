"""Repair consumer references to context-only fragments during relocation."""

from backend.api.canonical_repository.knowledge_models import SourceFragmentRecord, evidence_fragment_ids
from backend.pipeline import claim_management as cm, evidence_refresh


COLLECTIONS = {"evidence_steps", "observations", "questions"}
SHORT_PROMPTS = {"有沒有", "有没有", "有沒有看到", "有没有看到", "怎麼樣", "怎么样"}


def project(records, changes, new, source_id, calibrations):
    """Keep valid siblings or bind explicitly selected current source slices."""
    from backend.pipeline.transcript_evidence_apply import context_only, segment_fields

    def context_quote(quote):
        return context_only(quote) or quote.strip().rstrip("？?。！!") in SHORT_PROMPTS

    updates, created, missing = {}, {}, []
    paragraph_order = {key: i for i, key in enumerate(new["paragraphs"])}
    sources = {source_id: new}
    for key, state in records.items():
        if state["retired"] or key.split("/")[0] not in COLLECTIONS:
            continue
        payload = changes.get(key, state["payload"])
        ids = list(dict.fromkeys([*(payload.get("source_fragment_ids") or []),
                                  *evidence_fragment_ids(payload)]))
        fragments = {
            fid: changes.get("source_fragments/" + fid)
            or records.get("source_fragments/" + fid, {}).get("payload", {})
            for fid in ids
        }
        context = [fid for fid, fragment in fragments.items()
                   if fragment.get("source_id") == source_id and fragment.get("anchor_state") == "context_only"]
        if not context:
            continue
        context = [fid for fid in ids if fid in context or (
            fragments[fid].get("source_id") == source_id
            and context_quote(fragments[fid].get("verbatim_excerpt") or "")
        )]
        remaining = [fid for fid in ids if fid not in context]
        local_creations = {}
        rule = calibrations.get(key)
        live = [fid for fid in remaining
                if fragments[fid].get("anchor_state") == "source_version_bound"
                and not context_quote(fragments[fid].get("verbatim_excerpt") or "")]
        if not live:
            if not rule:
                missing.append({"record_key": key, "reason": "consumer_requires_substantive_source",
                                "context_fragment_ids": context})
                continue
            if (rule["before_pin"] != [state["revision"], state["content_sha256"]]
                    or rule["target_source_sha256"] != new["body_sha256"]):
                raise ValueError("Consumer calibration drift: " + key)
            anchors = [paragraph_order[fragments[fid]["paragraph_key"]] for fid in context]
            remaining = []
            for target in rule["segments"]:
                pk, quote = target["paragraph_key"], target["verbatim_excerpt"]
                paragraph = new["paragraphs"][pk]
                if (min(abs(paragraph_order[pk] - anchor) for anchor in anchors) > 2
                        or not quote.strip() or context_quote(quote) or "~~" in quote
                        or paragraph["text"].count(quote) != 1):
                    raise ValueError("Consumer calibration must select substantive nearby live text: " + key)
                fid = "TMR-" + cm.sha256_json([key, new["body_sha256"], pk, quote])[:48]
                source_key = "source_fragments/" + fid
                parent = min(context, key=lambda old: abs(
                    paragraph_order[fragments[old]["paragraph_key"]] - paragraph_order[pk]))
                fragment = SourceFragmentRecord.model_validate({
                    "fragment_id": fid, "source_id": source_id, "split_from_fragment_id": parent,
                    **segment_fields({"paragraph_key": pk, "verbatim_excerpt": quote,
                                      "source_segment_index": paragraph["index"],
                                      "media_time": paragraph.get("start_time"),
                                      "media_end_time": paragraph.get("end_time")}, new),
                }).model_dump(mode="json")
                if source_key in records:
                    raise ValueError("Consumer calibration fragment already exists: " + source_key)
                local_creations[source_key] = fragment
                remaining.append(fid)
        # Retain the original substantive bindings and their citation order.
        result = {**payload, "source_fragment_id": None, "source_fragment_ids": remaining}
        if key.startswith("evidence_steps/"):
            result.update(context_fragment_ids=sorted(set(payload.get("context_fragment_ids", [])) | set(context)),
                          support_eligibility="withheld_unreviewed", valid_for_identity_review=False)
        source_rows = {"source_fragments/" + fid: {
            "payload": local_creations.get("source_fragments/" + fid) or fragments.get(fid, {}),
            "retired": records.get("source_fragments/" + fid, {}).get("retired", False),
        } for fid in remaining}
        try:
            evidence_refresh.material({**result, "evidence_step_id": key, "statement": ""}, source_rows, sources)
        except (KeyError, ValueError):
            missing.append({"record_key": key, "reason": "consumer_binding_fails_flash_validation",
                            "context_fragment_ids": context})
            continue
        created.update(local_creations)
        updates[key] = result
    return updates, created, missing
