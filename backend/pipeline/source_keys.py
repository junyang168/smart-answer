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
