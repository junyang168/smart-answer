"""Publish one reviewed slide trial to the live data root, preserving exact artifacts.

Code deployment does not publish DATA_BASE_DIR files. This explicit data step
checks the reviewed model postimage, original selection and live predecessor.
Owner-specified title edits stay separate from model/reviewer answers.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from datetime import datetime, timezone

from backend.pipeline import chapter_exposition as exposition, chapter_material, chapter_slides as slides
from backend.pipeline.chapter_slide_claims import saved_context, check_review


def publish(trial: Path, live: Path, *, owner_corrections: Path | None = None) -> dict:
    packet = json.loads((trial / 'claims-context.json').read_text())
    passage, lecture = packet['passage'], packet['lecture']
    source = slides.record_path(trial / 'data', passage, lecture)
    target = slides.record_path(live, passage, lecture)
    original = trial / 'baseline' / target.name
    bundle = trial / 'publication'
    receipt_path = bundle / 'receipt.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if exposition.file_sha256(source) != receipt['source_sha256']:
            raise ValueError('Reviewed model slides changed after publication')
        saved_owner = bundle / 'owner-title-corrections.json'
        if bool(owner_corrections) != receipt['owner_title_edit_after_model_review']:
            raise ValueError('Publication owner correction differs')
        if owner_corrections and owner_corrections.read_bytes() != saved_owner.read_bytes():
            raise ValueError('Publication owner correction changed')
        if exposition.file_sha256(target) != receipt['published_sha256']:
            raise ValueError('Published slide data changed after publication')
        return receipt
    source_sha = exposition.file_sha256(source)
    record = json.loads(source.read_text())
    selection = exposition.record_path(trial / 'data', passage, lecture)
    current_selection = exposition.record_path(live, passage, lecture)
    if selection.read_bytes() != current_selection.read_bytes():
        raise ValueError('Live keep decisions changed')
    decisions = json.loads(selection.read_text())
    transcript = Path(decisions['transcript_file'])
    if not transcript.is_absolute():
        transcript = live / 'script_published' / transcript
    if exposition.file_sha256(transcript) != decisions['transcript_sha256']:
        raise ValueError('Actual transcript changed')
    if target.read_bytes() != original.read_bytes():
        raise ValueError('Live slides differ from trial predecessor')
    context = saved_context(record, decisions, exposition.file_sha256(selection))
    problems, _, checked = slides.check(record['raw_slides'], slides.kept_pieces(decisions), chapter_material.CHAPTERS[passage])
    if problems or checked != record['slides']:
        raise ValueError(f'Model postimage failed program checks: {problems}')
    review_path = slides.review_path(trial / 'data', passage, lecture, int(record.get('revision', 0)))
    review = json.loads(review_path.read_text())
    if review.get('slides_sha256') != source_sha or slides.review_blocks_publication(review):
        raise ValueError('Latest saved model slides have no matching nonblocking review')
    if context is not None:
        check_review(review, context, record['slides'])
    postimage = deepcopy(record)
    edits = None
    if owner_corrections is not None:
        edits = json.loads(owner_corrections.read_text())
        if edits.get('base_slides_sha256') != source_sha:
            raise ValueError('Owner title correction is for other slides')
        for edit in edits['corrections']:
            if edit.get('authority') != 'owner_exact_wording':
                raise ValueError('Title edit requires exact owner wording')
            own = next(s for s in postimage['slides'] if s['first'] == edit['first'])
            if own['title'] != edit['before'] or not edit['after'].strip():
                raise ValueError('Owner title correction preimage changed')
            own['title'] = edit['after']
        postimage['owner_title_corrections'] = edits
    # Keep original model/reviewer records intact: the reviewer did not assess
    # the owner's later title. Publication provenance says exactly that.
    postimage['publication'] = {
        'model_slides_sha256': source_sha,
        'model_review_sha256': exposition.file_sha256(review_path),
        'owner_title_edit_after_model_review': edits is not None,
        'published_at': datetime.now(timezone.utc).isoformat(),
    }
    bundle.mkdir(exist_ok=True)
    backup = bundle / 'previous-live'
    backup.mkdir(exist_ok=True)
    for existing in target.parent.glob(lecture + '.*'):
        saved = backup / existing.name
        if saved.exists() and saved.read_bytes() != existing.read_bytes():
            raise ValueError('Backup differs from current live artifact')
        if not saved.exists():
            shutil.copy2(existing, saved)
    shutil.copy2(source, bundle / 'reviewed-model-slides.json')
    if owner_corrections:
        shutil.copy2(owner_corrections, bundle / 'owner-title-corrections.json')
    # A directory receipt and full backup make the single live JSON swap
    # reversible. Review artifacts remain SHA-bound to their exact model draft.
    for artifact in source.parent.glob(lecture + '.*'):
        if artifact != source:
            shutil.copy2(artifact, target.parent / artifact.name)
    staged = target.with_suffix('.publishing.json')
    staged.write_text(json.dumps(postimage, ensure_ascii=False, indent=1))
    os.replace(staged, target)
    receipt = {'schema_version': 'chapter-slide-publication.v1', 'passage': passage,
               'lecture': lecture, 'source_sha256': source_sha,
               'previous_sha256': exposition.file_sha256(original),
               'published_sha256': exposition.file_sha256(target),
               'model_review_sha256': exposition.file_sha256(review_path),
               'owner_title_edit_after_model_review': edits is not None,
               'slides': len(postimage['slides']), 'remaining_findings': review['findings'],
               'backup': str(backup), 'published_file': str(target)}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def main():
    from backend.api.config import DATA_BASE_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trial', type=Path)
    parser.add_argument('--owner-corrections', type=Path)
    args = parser.parse_args()
    result = publish(args.trial, Path(DATA_BASE_PATH), owner_corrections=args.owner_corrections)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
