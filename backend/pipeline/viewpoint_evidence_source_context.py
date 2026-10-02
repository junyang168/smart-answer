"""Physical, same-paragraph context for anaphoric CVP evidence excerpts.

This adds no EvidenceStep/Claim/Registry records or citation authority. It
retains the original quote and resolves its context from the registered source.
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
        raise ValueError("registered context source physical SHA drift")
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
    path, file_sha, projection = loaded or load_context_source(source)
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
        source_file_sha256=file_sha, source_body_sha256=projection.body_sha256,
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
    actual = compile_source_context(evidence, source, loaded)
    if actual != evidence.source_context:
        raise ValueError("packet context differs from registered physical paragraph")
