"""One name for a source, used by everything that writes or reads the ledger.

A single sermon answers to four different strings in this codebase:

    2016 NYSC 專題：馬太福音釋經（四）3   the catalog's transcript_id
    2016_NYSC_3                          the filename slug
    SRC-2016_NYSC_3-3d012c24a542         the knowledge package's source_id
    DETAILED-2016_NYSC_3-3d012c24a542    the change set's package id

and a notes manuscript answers to three more, including the synthetic
`notes_manuscript:16_章_-_彌賽亞，捨己`. Every one of them is derived from the
same source and none is interchangeable with the others.

Three separate bugs were caused by picking the wrong one at three different
write sites -- ingest filing runs under `SRC-…`, notes ingest filing under the
synthetic transcript id, notes extraction filing under it again -- and each
looked the same from the outside: the run happened, the ledger recorded it, and
the row it belonged to still read "never run". Nothing errored, because a key
that matches nothing is indistinguishable from work nobody did.

So the choice is made once, here, and the ledger normalizes on write rather
than trusting each runner to pass the right string. The key is what the overview
lists a row under: the catalog's `transcript_id` for a sermon, the project
directory for a notes manuscript.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


#: How a notes manuscript's synthetic id is spelled where it has no transcript.
NOTES_PREFIX = "notes_manuscript:"


def normalize_source_key(value: Any) -> str:
    """The row key for a source, given any of the names it goes by.

    Only the notes prefix is stripped. A slug or a `SRC-…` id cannot be turned
    back into a transcript id without the catalog, so they are returned as they
    came: a wrong key that is visibly wrong beats one quietly rewritten into a
    different source's key.
    """

    text = str(value or "").strip()
    if text.startswith(NOTES_PREFIX):
        return text[len(NOTES_PREFIX):].strip()
    return text


def document_row_key(document: Mapping[str, Any]) -> str:
    """The row key for a `source_documents` record.

    A notes manuscript is listed under its project directory, which is
    `project_id` where the record carries one and the tail of the synthetic
    transcript id where it does not -- packages written before provenance was
    restored have the latter and no `project_id` at all.
    """

    project_id = str(document.get("project_id") or "").strip()
    transcript_id = str(document.get("transcript_id") or "").strip()
    source_id = str(document.get("source_id") or "").strip()
    is_notes = (
        document.get("source_type") == "notes_manuscript"
        or transcript_id.startswith(NOTES_PREFIX)
        or source_id.startswith(NOTES_PREFIX)
    )
    if is_notes:
        return project_id or normalize_source_key(transcript_id or source_id)
    return transcript_id or source_id


def source_path_key(source_descriptor: Mapping[str, Any]) -> Optional[str]:
    """The row key implied by a source manifest entry, if it states one."""

    for field in ("transcript_id", "source_id"):
        value = source_descriptor.get(field)
        if value:
            return normalize_source_key(value)
    return None


#: Where notes projects live. A manuscript there is identified by its project
#: directory, not by its filename.
NOTES_DIRNAME = "notes_to_surmon"


def key_from_source_path(path: Any) -> str:
    """The row key implied by the file a run read.

    A published transcript is stored as `script_published/<transcript_id>.json`,
    so its stem is the key. A notes manuscript is
    `notes_to_surmon/<project>/final.md`, where the stem is `final` -- the same
    for every project. Taking the stem there filed five separate reviews under a
    source called "final", which showed as a sermon with an adjudication but no
    review: impossible on its face, and the only reason it was visible at all.
    """

    from pathlib import Path as _Path

    candidate = _Path(str(path))
    parts = candidate.parts
    if NOTES_DIRNAME in parts:
        index = parts.index(NOTES_DIRNAME)
        if index + 1 < len(parts):
            return parts[index + 1]
    return candidate.stem


def package_row_key(package: Mapping[str, Any]) -> str:
    """The subject a single-source package's runs should be filed under.

    Every stage has to name the source the same way or the overview scatters
    one source's work across several rows. Extraction files under the
    transcript id; the stages after it read `source_documents` and were filing
    under `source_id`, which for a sermon is `SRC-2016_NYSC_3-3d012c24a542`
    while extraction said `2016 NYSC 專題：馬太福音釋經（四）3`. A 母本 hides the
    bug: both fields hold the same string there, so the first two sources run
    through the chain looked correct and every sermon would not have.

    Returns "" for a package covering several sources, which has no single
    subject and is filed as a batch.
    """

    documents = package.get("source_documents") or []
    if len(documents) != 1:
        return ""
    return document_row_key(documents[0])
