"""Prepare source-bound listening media and project its original-time intervals."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from decimal import Decimal
from difflib import SequenceMatcher
import fcntl
from functools import lru_cache
import hashlib
from itertools import groupby
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory

from backend.pipeline.source_projection import SOFT_DELETION

SCHEMA = 'chapter-listening-cuts.v2'
TOLERANCE = .08  # Encoder padding and media duration rounding, in seconds.
SHORT_DELETION_SECONDS = 15


def manifest(data_root: Path, title: str) -> Path:
    return data_root / 'chapter_exposition' / 'listening-media' / f'{title}.json'


@lru_cache(maxsize=256)
def _file_sha(path: Path, size: int, mtime: int, inode: int) -> str:
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def file_sha(path: Path) -> str:
    stat = path.stat()
    return _file_sha(path, stat.st_size, stat.st_mtime_ns, stat.st_ino)


def _transcript(data_root, title):
    return data_root / 'script_published' / f'{title}.json'


def _source(data_root, title):
    from backend.pipeline.original_audio_index import media_file
    found = media_file(data_root, title)
    if not found:
        raise ValueError('Original recording unavailable')
    return found[0]


def _ranges(cuts, duration):
    ranges = []
    for cut in cuts:
        start, end = cut
        if not 0 <= start < end <= duration:
            raise ValueError('Invalid deletion interval')
        ranges.append((start, end))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def retained_clock(cuts, duration):
    clock, source, media = [], 0, 0
    for start, end in [*_ranges(cuts, duration), (duration, duration)]:
        if start > source:
            clock.append({'start': source, 'end': start, 'media_start': media})
            media += start - source
        source = end
    return clock


def _playback_marks(text):
    """Keep original editor ordinals when counting spoken content."""
    return [(ordinal, mark) for ordinal, mark in enumerate(SOFT_DELETION.finditer(text))
            if any(char.isalnum() for char in mark.group(1))]


def _above_threshold(interval):
    start, end = interval
    return (math.isfinite(start) and math.isfinite(end)
            and Decimal(str(end)) - Decimal(str(start)) > SHORT_DELETION_SECONDS)


def deletion_marks(transcript):
    document = json.loads(transcript.read_text())
    script = document['script'] if isinstance(document, dict) else document
    return {(str(row['index']), i): match.group(1)
            for row in script if 'index' in row and row.get('type') not in ('subtitle', 'comment')
            for i, match in _playback_marks(row.get('text') or '')}


def suggest(data_root: Path, title: str) -> dict:
    """Locate marked spans on cue ranges, keeping proposals separate from approval."""
    source, transcript = _source(data_root, title), _transcript(data_root, title)
    duration, _ = _duration(source)
    raw = data_root / 'script' / f'{title}.json'
    decisions = _candidate_decisions(transcript, raw, duration)
    return {'transcript_sha256': file_sha(transcript), 'audio_sha256': file_sha(source), 'decisions': decisions}


def _candidate_decisions(transcript, raw, duration):
    entries = json.loads(raw.read_text()).get('entries', []) if raw.is_file() else []
    cues = {entry['index']: entry for entry in entries
            if isinstance(entry.get('index'), int) and entry.get('start_ms') is not None
            and entry.get('end_ms') is not None}
    document = json.loads(transcript.read_text())
    script = document['script'] if isinstance(document, dict) else document
    decisions = []
    normalized = lambda text: ''.join(char.lower() for char in text if char.isalnum())
    for row in script:
        if row.get('type') in ('subtitle', 'comment'):
            continue
        text = row.get('text') or ''
        marks = _playback_marks(text)
        if not marks:
            continue
        first = row.get('start_index', row.get('index'))
        last = row.get('end_index', first)
        exact = isinstance(first, int) and isinstance(last, int) and first <= last and first in cues and last in cues
        begin = cues[first]['start_ms'] / 1000 if exact else 0
        end = min(duration, cues[last]['end_ms'] / 1000) if exact else duration
        if not 0 <= begin < end <= duration:
            exact, begin, end = False, 0, duration
        heard, anchors = [], []
        blocks = []
        if exact:
            for key in range(first, last + 1):
                if key not in cues:
                    continue
                cue = cues[key]
                words = normalized(cue.get('text') or '')
                heard.append(words)
                anchors.extend([(cue['start_ms'] / 1000, cue['end_ms'] / 1000)] * len(words))
            edited = normalized(SOFT_DELETION.sub(r'\1', text))
            blocks = [block for block in SequenceMatcher(None, edited, ''.join(heard),
                                                         autojunk=False).get_matching_blocks() if block.size >= 3]
        for i, mark in marks:
            start = len(normalized(SOFT_DELETION.sub(r'\1', text[:mark.start()])))
            stop = start + len(normalized(mark.group(1)))
            matched = [anchors[block.b + char - block.a]
                       for block in blocks
                       for char in range(max(start, block.a), min(stop, block.a + block.size))]
            if not matched:
                continue
            estimate = [max(begin, matched[0][0]), min(end, matched[-1][1])]
            if _above_threshold(estimate):
                decisions.append({'paragraph': row['index'], 'ordinal': i, 'decision': 'hold', 'interval': [begin, end],
                                  'text_sha256': hashlib.sha256(mark.group(1).encode()).hexdigest(),
                                  'candidate_interval': estimate})
    return decisions


def _decision_key(item):
    return str(item['paragraph']), item['ordinal']


def _decision_intervals(transcript, report):
    marks = deletion_marks(transcript)
    required = {tuple(key) for key in report.get('review_candidates', [])}
    seen, cuts, pending = set(), [], []
    for decision in report['decisions']:
        key = _decision_key(decision)
        if key in seen or key not in marks or decision['text_sha256'] != hashlib.sha256(marks[key].encode()).hexdigest():
            raise ValueError('Deletion decision does not match its marked paragraph')
        seen.add(key)
        result = decision['decision']
        if result == 'hold':
            if key not in required or not _above_threshold(decision['interval']):
                raise ValueError('Only a long candidate needs an audio hold')
            pending.append(decision['interval'])
            continue
        if not re.fullmatch(r'[a-f0-9]{64}', decision.get('evidence_sha256', '')):
            raise ValueError('A resolved deletion requires saved audio evidence')
        if result == 'skip':
            if not _above_threshold(decision['interval']):
                raise ValueError('A cut must remove more than 15 seconds of source audio')
            cuts.append(decision['interval'])
        elif result == 'keep':
            if _above_threshold(decision['interval']):
                raise ValueError('This verified deletion is longer than 15 seconds')
        elif result != 'no_audio':
            raise ValueError('Unknown deletion decision')
    if not required <= seen:
        raise ValueError('Unreviewed long soft deletion')
    return cuts, pending


def _verify_evidence(report, folder):
    seams = []
    for decision in report['decisions']:
        if decision['decision'] == 'hold':
            continue
        evidence = folder / decision['evidence_file']
        if file_sha(evidence) != decision['evidence_sha256']:
            raise ValueError('Audio boundary evidence changed')
        proof = json.loads(evidence.read_text())
        if (proof['audio_sha256'] != report['audio_sha256']
                or proof['published_transcript_sha256'] != report['transcript_sha256']
                or str(proof['source_paragraph_index']) != str(decision['paragraph'])
                or proof.get('deletion_ordinal', 0) != decision['ordinal']):
            raise ValueError('Audio boundary evidence belongs to another source or deletion')
        review = proof['review']
        if decision['decision'] in ('skip', 'keep'):
            reference = proof['removed_source_interval']
            safe = proof.get('safe_cut_intervals', [(at, at) for at in reference])
            if (review.get('left') != 'pass' or review.get('right') != 'pass'
                    or not all(abs(at - baseline) <= 1 and low <= at <= high
                               for at, baseline, (low, high) in zip(decision['interval'], reference, safe, strict=True))):
                raise ValueError('Both audio cuts must match the passing review')
            if decision['decision'] == 'skip':
                seams.append({'interval': decision['interval'],
                              'left': review.get('left_retained_ending', ''),
                              'right': review.get('right_retained_opening', '')})
        if decision['decision'] == 'no_audio' and review.get('no_audio') != 'pass':
            raise ValueError('Missing proof that the deletion had no corresponding audio')
    return seams


def _sync_windows(report, folder, duration):
    windows = []
    for item in report.get('audio_alignment', []):
        path = folder / item['file']
        if file_sha(path) != item['sha256']:
            raise ValueError('Edited paragraph timing evidence changed')
        alignment = json.loads(path.read_text())
        if any(alignment[key] != report[key] for key in ('transcript_sha256', 'audio_sha256')):
            raise ValueError('Edited paragraph timing belongs to another source')
        anchors = alignment['anchors']
        if (len(anchors) < 2 or any(not (math.isfinite(old) and 0 <= aligned <= duration) for old, aligned in anchors)
                or any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(anchors, anchors[1:]))):
            raise ValueError('Edited paragraph timing is not increasing')
        windows.append({'anchors': anchors, 'evidence_sha256': item['sha256']})
    windows.sort(key=lambda window: window['anchors'][0][0])
    if any(a['anchors'][-1][0] >= b['anchors'][0][0] for a, b in zip(windows, windows[1:])):
        raise ValueError('Overlapping edited paragraph timings')
    return windows


def _duration(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            check=True, capture_output=True)
    data = json.loads(result.stdout)
    duration = float(data['format']['duration'])
    streams = {stream['codec_type']: stream for stream in data['streams']}
    if not math.isfinite(duration) or duration <= 0 or 'audio' not in streams:
        raise ValueError('Invalid recording duration or missing audio')
    return duration, streams


def _encode(source, output, clock, video):
    graph, inputs = [], []
    # Split once and retain the original sample clock for all slices.
    if len(clock) > 1:
        for kind, verb in [('a', 'asplit'), *([('v', 'split')] if video else [])]:
            graph.append(f'[0:{kind}]{verb}={len(clock)}' + ''.join(f'[{kind}{i}]' for i in range(len(clock))))
    for i, interval in enumerate(clock):
        limits = f"start={interval['start']}:end={interval['end']}"
        audio = f'a{i}' if len(clock) > 1 else '0:a'
        graph.append(f'[{audio}]atrim={limits},asetpts=PTS-STARTPTS[ar{i}]')
        if video:
            visual = f'v{i}' if len(clock) > 1 else '0:v'
            graph.append(f'[{visual}]trim={limits},setpts=PTS-STARTPTS[vr{i}]')
            inputs.append(f'[vr{i}]')
        inputs.append(f'[ar{i}]')
    graph.append(''.join(inputs) + f'concat=n={len(clock)}:v={int(video)}:a=1' + ('[v][a]' if video else '[joined]'))
    if not video:
        graph.append('[joined]aformat=sample_fmts=s16p,asetnsamples=n=1152:p=1[a]')
    codecs = (['-map', '[v]', '-c:v', 'libx264', '-c:a', 'aac', '-movflags', '+faststart']
              if video else ['-c:a', 'libmp3lame', '-q:a', '2'])
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-filter_complex', ';'.join(graph),
                    '-map', '[a]', *codecs, str(output)], check=True)


def prepare(data_root: Path, title: str, decisions_path: Path, install_root: Path) -> dict:
    """Create the rendition in an isolated data root, committing its manifest last."""
    source, transcript = _source(data_root, title), _transcript(data_root, title)
    decisions = json.loads(decisions_path.read_text())
    transcript_sha, audio_sha = file_sha(transcript), file_sha(source)
    if (decisions['transcript_sha256'], decisions['audio_sha256']) != (transcript_sha, audio_sha):
        raise ValueError('Reviewed audio or transcript changed')
    duration, streams = _duration(source)
    raw = data_root / 'script' / f'{title}.json'
    recognition_sha = file_sha(raw) if raw.is_file() else None
    candidates = {_decision_key(candidate): candidate['candidate_interval']
                  for candidate in _candidate_decisions(transcript, raw, duration)}
    decisions['review_candidates'] = sorted(candidates)
    cuts, pending = _decision_intervals(transcript, decisions)
    for decision in decisions['decisions']:
        if decision['decision'] == 'hold':
            start, end = decision['interval']
            left, right = candidates[_decision_key(decision)]
            if not start <= left < right <= end:
                raise ValueError('Held interval does not cover the full candidate')
    seams = _verify_evidence(decisions, decisions_path.parent)
    sync_windows = _sync_windows(decisions, decisions_path.parent, duration)
    clock = retained_clock([*cuts, *pending], duration)
    video = 'video' in streams
    video_root = install_root.parent / 'video'
    video_root.mkdir(exist_ok=True, parents=True)
    value = {'schema_version': SCHEMA, 'title': title, 'source_duration': duration,
             'transcript_sha256': transcript_sha, 'audio_sha256': audio_sha, 'media_sha256': None,
             'recognition_sha256': recognition_sha, 'review_candidates': [list(key) for key in sorted(candidates)],
             'media': None, 'clock': clock, 'caption_seams': seams, 'sync_windows': sync_windows,
             'decisions': [{k: v for k, v in d.items() if k not in ('evidence_file', 'candidate_interval')}
                           for d in decisions['decisions']]}
    target = manifest(install_root, title)
    if target.exists():
        try:
            prior = _load(install_root, title, target)
        except (OSError, ValueError, KeyError, TypeError):
            prior = None
        if prior and all(prior.get(k) == v for k, v in value.items() if k not in ('media', 'media_sha256')):
            return prior
    with TemporaryDirectory(prefix='.listening-', dir=video_root) as tmp:
        rendition = Path(tmp) / ('cut.mp4' if video else 'cut.mp3')
        if clock and (cuts or pending):
            _encode(source, rendition, clock, video)
            rendered_seconds, rendered_streams = _duration(rendition)
            expected = clock[-1]['media_start'] + clock[-1]['end'] - clock[-1]['start']
            if abs(rendered_seconds - expected) > TOLERANCE:
                raise ValueError('Edited audio length differs from its playback clock')
            if video and ('video' not in rendered_streams or
                          abs(float(rendered_streams['audio']['duration']) -
                              float(rendered_streams['video']['duration'])) > TOLERANCE):
                raise ValueError('Edited audio and video lengths differ')
            sha = file_sha(rendition)
            value.update(media=f'listening-cut-{sha}{rendition.suffix}', media_sha256=sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        if (file_sha(transcript), file_sha(source)) != (transcript_sha, audio_sha):
            raise ValueError('Source changed while encoding')
        if value['media']:
            asset = video_root / value['media']
            if not asset.exists():
                os.replace(rendition, asset)
            elif file_sha(asset) != value['media_sha256']:
                raise ValueError('Existing rendition differs from its filename')
        with NamedTemporaryFile('w', dir=target.parent, delete=False) as new:
            stage = Path(new.name)
            json.dump(value, new, ensure_ascii=False, indent=2)
        try:
            os.replace(stage, target)
        finally:
            stage.unlink(missing_ok=True)
    return value


def _load(data_root, title, path):
    value = json.loads(path.read_text())
    if value['schema_version'] != SCHEMA or value['title'] != title:
        raise ValueError('Edited media manifest targets another recording')
    transcript, source = _transcript(data_root, title), _source(data_root, title)
    if (file_sha(transcript), file_sha(source)) != (value['transcript_sha256'], value['audio_sha256']):
        raise ValueError('Edited media source changed')
    raw = data_root / 'script' / f'{title}.json'
    if (file_sha(raw) if raw.is_file() else None) != value['recognition_sha256']:
        raise ValueError('Edited media recognition cues changed')
    cuts, pending = _decision_intervals(transcript, value)
    if [seam['interval'] for seam in value.get('caption_seams', [])] != cuts:
        raise ValueError('Caption seams disagree with approved cuts')
    if value['clock'] != retained_clock([*cuts, *pending], value['source_duration']):
        raise ValueError('Edited media clock disagrees with approved cuts')
    value['excluded'] = _ranges([*cuts, *pending], value['source_duration'])
    if not value['clock'] or not value['excluded']:
        if value['media'] is not None:
            raise ValueError('Unexpected edited audio')
        return value
    name = value['media']
    matched = re.fullmatch(r'listening-cut-([a-f0-9]{64})\.(mp3|mp4)', name)
    if not matched or matched[1] != value['media_sha256']:
        raise ValueError('Invalid edited media name')
    if file_sha(data_root.parent / 'video' / name) != value['media_sha256']:
        raise ValueError('Edited media changed')
    return value


def activate(data_root: Path, staged: Path, title: str, expected_manifest_sha: str | None) -> None:
    """Install a fully reviewed rendition and switch its manifest last."""
    proposed = manifest(staged, title)
    result = _load(staged, title, proposed)
    if any(decision['decision'] == 'hold' for decision in result['decisions']):
        raise ValueError('Audio boundaries still need review')
    active = manifest(data_root, title)
    if proposed.resolve() == active.resolve():
        raise ValueError('Activation requires a separate prepared rendition')
    active.parent.mkdir(parents=True, exist_ok=True)
    with active.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = file_sha(active) if active.exists() else None
        if current == file_sha(proposed):
            _load(data_root, title, active)
            return
        if current != expected_manifest_sha:
            raise ValueError('Active listening media changed')
        video_dir = data_root.parent / 'video'
        if name := result['media']:
            with TemporaryDirectory(prefix='.listening-install-', dir=video_dir) as tmp:
                asset = video_dir / name
                if not asset.exists():
                    incoming = Path(tmp) / name
                    shutil.copyfile(staged.parent / 'video' / name, incoming)
                    if file_sha(incoming) != result['media_sha256']:
                        raise ValueError('Edited audio changed during installation')
                    os.replace(incoming, asset)
        with NamedTemporaryFile(dir=active.parent, delete=False) as temporary:
            candidate = Path(temporary.name)
            temporary.write(proposed.read_bytes())
        try:
            if _load(data_root, title, candidate) != result:
                raise ValueError('Edited source changed during installation')
            os.replace(candidate, active)
        finally:
            candidate.unlink(missing_ok=True)


def _clipped_blocks(blocks, stretches, end_key):
    return [{**block, 'at': max(block['at'], span['start']),
             end_key: min(block[end_key], span['end'])}
            for span in stretches for block in blocks
            if block['at'] < span['end'] and span['start'] < block[end_key]]


def _split_captions(lines, seams):
    """Split a retained sentence across a splice using its verified audible words."""
    unresolved = False
    for seam in seams:
        start, end = seam['interval']
        left, right = seam['left'], seam['right']
        split = []
        for line in lines:
            if not line['at'] < start < end < line['end']:
                split.append(line)
                continue
            text = line['text']
            position = text.find(right) if right else -1
            punctuation = '，。？！；、,.?!; \n'
            ending = left.rstrip(punctuation)
            if ending and position < 0 and text.rstrip(punctuation).endswith(ending):
                split.append({**line, 'end': start})
            elif (position > 0 and text.count(right) == 1 and ending
                  and text[:position].rstrip(punctuation).endswith(ending)):
                split.extend([{**line, 'end': start, 'text': text[:position].rstrip()},
                              {**line, 'at': end, 'text': text[position:]}])
            else:
                unresolved = True
        lines = split
    return lines, unresolved


def _align_events(row, windows):
    """Apply verified source-clock corrections before intersecting playback cuts."""
    if not windows:
        return row
    from backend.pipeline.original_audio_index import _retimed

    lines = row.get('transcript', [])
    applicable = []
    for window in windows:
        anchors = window['anchors']
        # The absolute anchors address the saved caption clock. The legacy
        # reader can have a different clock even for the same audio asset.
        if not any(abs(line['at'] - anchors[0][0]) <= .01 for line in lines):
            continue
        if not any(abs(line['end'] - anchors[-1][0]) <= .01 for line in lines):
            continue
        applicable.append(([point[0] for point in anchors], anchors))
    if not applicable:
        return row

    def convert(seconds):
        for old_times, anchors in applicable:
            if old_times[0] <= seconds <= old_times[-1]:
                i = min(bisect_right(old_times, seconds), len(anchors) - 1)
                (a, first), (b, second) = anchors[i - 1:i + 1]
                return first + (seconds - a) * (second - first) / (b - a)
        return seconds

    aligned = _retimed(convert, row)
    # Editorial cutoffs concern the audible source selection. A corrected
    # sentence timestamp does not implicitly trim additional source speech.
    return {**aligned, 'stretches': row['stretches']}


def _project_row(row, edit):
    row = _align_events(row, edit.get('sync_windows', []))
    stretches = [{**stretch, 'start': start, 'end': end,
                  'media_start': clip['media_start'] + start - clip['start']}
                 for stretch in row['stretches'] for clip in edit['clock']
                 if (end := min(stretch['end'], clip['end'])) > (start := max(stretch['start'], clip['start']))]
    if not stretches:
        return None
    def inside(at):
        return any(span['start'] <= at < span['end'] for span in stretches)

    for span in stretches:
        if 'parts' in span:
            span['parts'] = [{**p, 'start': max(p['start'], span['start']), 'end': min(p['end'], span['end'])}
                             for p in span['parts'] if p['start'] < span['end'] and span['start'] < p['end']]
    projected_slides = []
    if 'slides' in row:
        for span in stretches:
            for slide in row['slides']:
                start = max(slide['at'], span['start'])
                end = min(slide['at'] + slide['seconds'], span['end'])
                if start >= end:
                    continue
                rendered = {**slide, 'at': start, 'seconds': end - start}
                for key in ('points', 'scriptures', 'inline_notes'):
                    if key in slide:
                        # Previously revealed points can carry across a cut if
                        # their own anchors are still playable.
                        rendered[key] = [p for p in slide[key] if inside(p['at']) and p['at'] < end]
                if 'diagram' in slide and not (span['start'] <= slide['diagram']['at'] < end):
                    rendered.pop('diagram')
                if slide['at'] != start and not inside(slide['at']) and not rendered.get('points'):
                    continue
                projected_slides.append(rendered)
    out = {**row, 'stretches': stretches, 'media_url': '/web/video/' + edit['media'],
           'excluded_audio': edit['excluded'], 'audio_revision': edit['audio_sha256'],
           'seconds': sum(s['end'] - s['start'] for s in stretches if not s.get('left_out')),
           'left_out_seconds': sum(s['end'] - s['start'] for s in stretches if s.get('left_out'))}
    if 'slides' in row:
        out['slides'] = projected_slides
    if 'transcript' in row:
        # A retained sentence that ends in a removed interval belongs only
        # to the clip containing its anchor. It must not repeat after a seam.
        lines, unresolved = _split_captions(row['transcript'], edit.get('caption_seams', []))
        out['transcript'] = [{**line, 'end': min(line['end'], span['end'])}
                             for span in stretches for line in lines
                             if span['start'] <= line['at'] < span['end']]
        if unresolved:
            out['caption_seam_needs_review'] = True
    if 'tables' in row:
        out['tables'] = _clipped_blocks(row['tables'], stretches, 'until')
    for key in ('judgements', 'readings', 'spoken', 'original_language_events'):
        if key in row:
            out[key] = [event for event in row[key] if inside(event['at'])]
    return out


def protect(index: dict, data_root: Path, held_sources=()) -> dict:
    """Apply opted-in, reviewed media after both the legacy and unit routes."""
    media, held, changed = {}, set(held_sources), bool(held_sources)
    def rows(items):
        nonlocal changed
        result = []
        for row in items:
            title = row.get('transcript_id')
            if not title or Path(title).name != title:
                result.append(row)
                continue
            path = manifest(data_root, title)
            if not path.exists():
                result.append(row)
                continue
            changed = True
            if title not in media:
                try:
                    media[title] = _load(data_root, title, path)
                except (OSError, ValueError, KeyError, TypeError):
                    media[title] = None
            edit = media[title]
            if edit is None:
                held.add(row['source_id'])
                continue
            if not edit['excluded']:
                result.append(row)
                continue
            if any(decision['decision'] == 'hold' for decision in edit.get('decisions', [])):
                held.add(row['source_id'])
            if edit['clock'] and (safe := _project_row(row, edit)):
                result.append(safe)
        return result

    top = rows(index.get('sermons', []))
    sections = [{**s, 'sermons': projected} for s in index.get('sections', [])
                if (projected := rows(s['sermons']))]
    if not changed:
        return index
    result = {**index, 'sermons': top, 'sections': sections}
    if held:
        result['soft_deletion_holds'] = sorted(held)
    for section in sections:
        if 'topics' in section:
            section['topics'] = [topic for topic in section['topics'] if any(
                row['source_id'] == topic.get('source_id') and any(
                    clip['start'] <= topic['at'] < clip['end'] for clip in row['stretches'])
                for row in section['sermons'])]
    result['resume_version'] = hashlib.sha256(json.dumps(
        [index.get('resume_version'), {title: edit['media_sha256'] if edit else None for title, edit in media.items()}],
        sort_keys=True).encode()).hexdigest()
    return result


def available_plan(data_root: Path, passage: str):
    """Remove managed recordings whose published media cannot be verified."""
    exposition = data_root / 'chapter_exposition'
    if not (exposition / 'listening-media').is_dir():
        return None, []
    path = exposition / passage / 'listening-units.json'
    plan = json.loads(path.read_text())
    unavailable = set()
    for entry in plan['inputs']:
        title = entry['lecture']
        edited = manifest(data_root, title)
        if not edited.exists():
            continue
        try:
            if _load(data_root, title, edited)['clock']:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            pass
        unavailable.add(entry['source_id'])
    if not unavailable:
        return None, []
    plan['inputs'] = [entry for entry in plan['inputs'] if entry['source_id'] not in unavailable]
    plan['non_argument_exclusions'] = [entry for entry in plan.get('non_argument_exclusions', [])
                                       if entry['source_id'] not in unavailable]
    available = {}
    for key, unit in plan['units'].items():
        groups = [group for group in unit['groups'] if group['source_id'] not in unavailable]
        if not groups:
            continue
        group_keys = {group['key'] for group in groups}
        subs = [{**sub, 'group_keys': [key for key in sub['group_keys'] if key in group_keys]}
                for sub in unit['sub_units'] if any(key in group_keys for key in sub['group_keys'])]
        playback = [row for row in unit['playback_order'] if row['group_key'] in group_keys]
        selected = {**unit, 'groups': groups, 'sub_units': subs, 'playback_order': playback}
        if unit.get('retained_clips') and len(playback) != len(unit['playback_order']):
            owners = {g: sub['id'] for sub in unit['sub_units'] for g in sub['group_keys']}
            identity = lambda row: (owners[row['group_key']], row['source_id'])
            remaining = [(f'{sid}:{idx}', sid, source) for idx, ((sid, source), _) in
                         enumerate(groupby(unit['playback_order'], identity), 1) if source not in unavailable]
            projected = [run for run, _ in groupby(playback, identity)]
            # Combining two runs with different editorial clip bounds is
            # ambiguous. Keep other Units available while this one is held.
            if len(remaining) != len(projected):
                continue
            selected['retained_clips'] = {f'{sid}:{idx}': unit['retained_clips'][old]
                                          for idx, (old, sid, _) in enumerate(remaining, 1)
                                          if old in unit['retained_clips']}
        available[key] = selected
    plan['units'] = available
    return plan, sorted(unavailable)


def playback_problems(index):
    """Read-only checks for the actual public payload, including legacy rows."""
    problems = []
    rows = [*index.get('sermons', []), *(r for s in index.get('sections', []) for r in s['sermons'])]
    for row in rows:
        if not row.get('audio_revision'):
            continue
        if row.get('caption_seam_needs_review'):
            problems.append('A retained sentence needs explicit alignment across an edited audio seam')
        if not row['media_url'].startswith('/web/video/listening-cut-'):
            problems.append('A recording with deletions served its original media')
        previous_end = 0
        for stretch in row['stretches']:
            start, end = stretch['start'], stretch['end']
            media_start = stretch.get('media_start')
            if not (start < end and media_start is not None and media_start >= previous_end - 1e-6):
                problems.append('Edited media intervals are unsorted or empty')
            if media_start is not None:
                previous_end = media_start + end - start
            if any(start < stop and low < end for low, stop in row['excluded_audio']):
                problems.append('Deleted audio overlaps a playable interval')
        if any(not any(s['start'] <= line['at'] < line['end'] <= s['end'] for s in row['stretches'])
               for line in row.get('transcript', [])):
            problems.append('A caption crosses an edited audio seam')
    return problems


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_data', type=Path)
    parser.add_argument('title')
    parser.add_argument('decisions', type=Path)
    parser.add_argument('output_data', type=Path, nargs='?')
    parser.add_argument('--suggest', action='store_true', help='Write unapproved candidate intervals to decisions')
    parser.add_argument('--activate', action='store_true', help='Install the fully reviewed rendition in source_data')
    parser.add_argument('--previous-manifest-sha', help='Required when replacing a different active rendition')
    args = parser.parse_args()
    if args.activate:
        if args.suggest or args.output_data is not None:
            parser.error('Activation takes source_data, title and the prepared data root')
        activate(args.source_data, args.decisions, args.title, args.previous_manifest_sha)
        raise SystemExit(0)
    if args.suggest:
        result = suggest(args.source_data, args.title)
        with args.decisions.open('x') as output:
            json.dump(result, output, indent=2, ensure_ascii=False)
        raise SystemExit(0)
    if args.output_data is None:
        parser.error('An isolated output data root is required')
    if args.source_data.resolve() == args.output_data.resolve():
        parser.error('Prepare edited media in an isolated data root')
    value = prepare(args.source_data, args.title, args.decisions, args.output_data)
    print(json.dumps({'media': value['media'], 'intervals': len(value['clock'])}))
