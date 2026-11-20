"""Install one reviewed PPT bundle without changing the listening plan or lecture media."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import tempfile

from backend.pipeline import (
    chapter_exposition, chapter_listening_cuts, chapter_listening_eval, chapter_listening_units,
    chapter_slide_eval, chapter_subunit_slides,
)
from backend.pipeline.chapter_slide_claims import digest


_PRODUCTION_DATA = Path('/opt/homebrew/var/www/church/web/data')
_DEFAULT_DEPLOY_ROOT = '/opt/homebrew/var/www/smart-answer-deploy'


def _check_production_release(data_root):
    if data_root.resolve() != _PRODUCTION_DATA.resolve():
        return
    active_file = Path(os.getenv('SMART_ANSWER_DEPLOY_ROOT', _DEFAULT_DEPLOY_ROOT)) / 'active-release'
    active = Path(active_file.read_text().strip()).resolve()
    if Path(__file__).resolve().parents[2] != active:
        raise ValueError('Run production content publication from the active deployed release')


def _without_subunit_slides(index, sub_id):
    return {**index, 'sections': [
        {**section, 'sermons': [{k: v for k, v in row.items() if k != 'slides'} for row in section['sermons']]}
        if section.get('sub_unit_id') == sub_id else section
        for section in index['sections']
    ]}


def _check_projection(data_root, passage, plan, sub_id, bundle):
    build = chapter_listening_units.build
    before = chapter_listening_cuts.protect(build(data_root, passage, plan=plan), data_root)
    after = chapter_listening_cuts.protect(
        build(data_root, passage, plan=plan, subunit_override=(sub_id, bundle)), data_root)
    target = [section for section in after['sections'] if section.get('sub_unit_id') == sub_id]
    if not target or any(not row.get('slides') for section in target for row in section['sermons']):
        raise ValueError('Sub-unit has no playable reviewed slides')
    if _without_subunit_slides(after, sub_id) != _without_subunit_slides(before, sub_id):
        raise ValueError('PPT publication would change lecture playback or another Sub-unit')
    displayed = {'sections': target}
    problems = (chapter_listening_eval.playback_alignment_problems(displayed)
                + chapter_listening_eval.diagram_index_problems(displayed))
    if problems:
        raise ValueError(f'Sub-unit playback alignment failed: {problems}')


def publish(data_root: Path, passage: str, sub_id: str, slides: Path, selection: Path,
            receipt: Path, *, apply=False):
    if apply:
        _check_production_release(data_root)
    plan_path = chapter_subunit_slides.plan_path(data_root, passage)
    before = plan_path.read_bytes()
    plan = json.loads(before)
    lecture, expected = chapter_subunit_slides.subunit_selection(plan, passage, sub_id)
    unit_passage, _, sub, groups = chapter_subunit_slides.find(plan, sub_id)
    entry = next(entry for entry in plan['inputs'] if entry['source_id'] == expected['source_id'])
    source_revision = chapter_listening_units.subunit_source_revision(unit_passage, sub, groups, entry)
    pointer = chapter_listening_units.published_subunit_path(plan_path.parent, sub_id)
    files = {'slides': slides, 'selection': selection, 'semantic-eval': receipt}

    # Snapshot the evaluated bundle. Later writes to an editor's staging
    # directory cannot alter the bytes verified by this invocation.
    with tempfile.TemporaryDirectory(prefix='listening-subunit-') as directory:
        bundle = Path(directory)
        frozen = {key: Path(shutil.copyfile(path, bundle / f'{lecture}.{key}.json'))
                  for key, path in files.items()}
        if json.loads(frozen['selection'].read_text()) != expected:
            raise ValueError('Reviewed selection differs from the published Sub-unit')
        if problems := chapter_slide_eval.semantic_eval_problems(
                frozen['slides'], frozen['selection'], frozen['semantic-eval']):
            raise ValueError(f'Sub-unit semantic Eval did not pass: {problems}')
        hashes = {key: chapter_exposition.file_sha256(path) for key, path in frozen.items()}
        bundle_id = digest(hashes)
        record = {'bundle': bundle_id, 'lecture': lecture, 'source_revision': source_revision, 'files': hashes}

        if not apply:
            _check_projection(data_root, passage, plan, sub_id, bundle)
            return {'status': 'ready', 'sub_unit': sub_id, **record}

        pointer.parent.mkdir(parents=True, exist_ok=True)
        with pointer.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if plan_path.read_bytes() != before:
                raise ValueError('Listening plan changed while publishing')
            _check_projection(data_root, passage, plan, sub_id, bundle)
            installed = chapter_listening_units.bundle_directory(plan_path.parent, bundle_id)
            installed.parent.mkdir(parents=True, exist_ok=True)
            if not installed.exists():
                with tempfile.TemporaryDirectory(prefix='.ppt-install-', dir=installed.parent) as scratch:
                    incoming = Path(scratch) / 'bundle'
                    shutil.copytree(bundle, incoming)
                    try:
                        os.rename(incoming, installed)
                    except FileExistsError:
                        pass
            if any(chapter_exposition.file_sha256(installed / path.name) != hashes[key]
                   for key, path in frozen.items()):
                raise ValueError('Installed bundle does not match the reviewed files')
            with tempfile.TemporaryDirectory(prefix='.ppt-switch-', dir=pointer.parent) as scratch:
                staged = Path(scratch) / pointer.name
                staged.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
                if plan_path.read_bytes() != before:
                    raise ValueError('Listening plan changed while publishing')
                _check_production_release(data_root)
                if not pointer.exists() or pointer.read_bytes() != staged.read_bytes():
                    os.replace(staged, pointer)
        return {'status': 'published', 'sub_unit': sub_id, **record}


def main():
    from backend.api.config import DATA_BASE_PATH

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('passage')
    parser.add_argument('sub_unit')
    parser.add_argument('slides', type=Path)
    parser.add_argument('selection', type=Path)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path(DATA_BASE_PATH))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(publish(args.data_dir, args.passage, args.sub_unit, args.slides,
                             args.selection, args.receipt, apply=args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
