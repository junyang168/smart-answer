"""Physical, same-paragraph context for anaphoric CVP evidence excerpts.

This adds no EvidenceStep/Claim/Registry records or citation authority. It
retains the original quote and resolves its context from the registered source.
The paragraph artifact retains its registered physical-generation SHA, resolved
through an exact subtitle-only proof when necessary. The loader separately
returns the actual live file SHA for I/O/provenance gates. Editorial headings
therefore cannot change the paragraph-only model packet or its cache identity.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from backend.api.canonical_repository.viewpoint_resolution import EvidenceSourceContext
from backend.pipeline.knowledge_source import markdown_source_document
from backend.pipeline.source_projection import project_script


def load_context_source(source):
    path = Path(source["source_path"])
    if not path.is_absolute():
        raise ValueError("registered context source path is not absolute")
    raw = path.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    if file_sha != source.get("source_file_sha256"):
        from backend.pipeline.source_editorial_transition import verify_current_source_binding
        try:
            verified = verify_current_source_binding({"path": str(path),
                "file_sha256": source.get("source_file_sha256")})
        except (ValueError, OSError, KeyError) as exc:
            raise ValueError("registered context source physical SHA drift") from exc
        if verified["file_sha256"] != file_sha:
            raise ValueError("registered context source changed during editorial proof")
    if source.get("source_type") == "notes_manuscript":
        payload, loaded_raw, _ = markdown_source_document(source)
        if loaded_raw != raw:
            raise ValueError("context source changed while reading")
    else:
        payload = json.loads(raw)
        if isinstance(payload, list):
            payload = {"script": payload}
    projection = project_script(payload["script"])
    if projection.body_sha256 != source.get("source_body_sha256"):
        raise ValueError("registered context source body SHA drift")
    return path, file_sha, projection


def compile_source_context(evidence, source, loaded=None):
    if evidence.source_id != source.get("source_id"):
        raise ValueError("source context crossed source identity")
    if evidence.source_modality == "visual":
        return None  # SVG has its own typed, SHA-bound visual projection.
    if source.get("locator_space") != "spoken_body_v1":
        raise ValueError("source context requires explicit spoken-body locator space")
    key = str(evidence.paragraph_key or "")
    if not re.fullmatch(r"S[0-9]{4,}", key):
        raise ValueError("source context requires an exact body paragraph key")
    path, _live_file_sha, projection = loaded or load_context_source(source)
    index = int(key[1:]) - 1
    if not 0 <= index < len(projection.spoken_rows):
        raise ValueError("context paragraph is outside registered source")
    text = str(projection.spoken_rows[index].get("text") or "")
    quote = evidence.verbatim_excerpt
    if not quote or quote not in text:
        raise ValueError("context excerpt must occur in its own live paragraph")
    # A speaker may repeat the same sentence in one paragraph. These offsets
    # locate an exact copy for context validation, not a new source occurrence
    # or citation authority. Select the first copy deterministically; retain
    # the entire paragraph and all original evidence/fragment identities.
    start = text.index(quote)
    return EvidenceSourceContext(source_id=evidence.source_id, source_path=str(path),
        source_file_sha256=source["source_file_sha256"], source_body_sha256=projection.body_sha256,
        paragraph_key=key, paragraph_text=text,
        paragraph_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        excerpt_start_char=start, excerpt_end_char=start + len(quote))


def attach_source_context(claim, source, loaded=None):
    evidence = []
    for item in claim.evidence:
        context = compile_source_context(item, source, loaded)
        # Revalidate, rather than relying on model_copy's unvalidated update.
        evidence.append(type(item).model_validate(item.model_dump(mode="json") |
                                                ({"source_context": context.model_dump(mode="json")} if context else {})))
    return type(claim).model_validate(claim.model_dump(mode="json") |
                                    {"evidence": [e.model_dump(mode="json") for e in evidence]})


def verify_source_context(evidence, source, loaded=None):
    # Keep live physical provenance outside the stable paragraph artifact.
    loaded = loaded or (None if evidence.source_modality == "visual" else load_context_source(source))
    actual = compile_source_context(evidence, source, loaded)
    saved = evidence.source_context
    if actual == saved:
        return
    if actual is not None and saved is not None:
        # A saved paragraph packet remains exactly the reviewed packet. Only
        # its historical physical provenance may traverse subtitle insertion;
        # no paragraph, excerpt, source identity or offset may be substituted.
        from backend.pipeline.source_editorial_transition import verify_current_source_binding
        old = saved.model_dump(mode="json")
        new = actual.model_dump(mode="json")
        if {k:v for k,v in old.items() if k != 'source_file_sha256'} == {
                k:v for k,v in new.items() if k != 'source_file_sha256'}:
            bound = verify_current_source_binding({"path": saved.source_path,
                "file_sha256": saved.source_file_sha256})
            if loaded is not None and bound["file_sha256"] == loaded[1]:
                return
    raise ValueError("packet context differs from registered physical paragraph")
