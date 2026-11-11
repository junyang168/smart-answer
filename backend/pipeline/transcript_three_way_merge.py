"""Carry existing editorial changes onto a new transcript without overwriting either.

The baseline must be the exact pre-edit ancestor of the existing transcript.
Character deltas and soft-deletion intents are accounted for separately. Unique
literal context can merge automatically; overlapping rewrites remain explicit.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from copy import deepcopy
import difflib
from html import escape
import json
from time import perf_counter
from pathlib import Path

from backend.pipeline import transcript_claim_migration as m
from backend.pipeline.source_projection import SOFT_DELETION, project_script
from backend.pipeline.transcript_evidence_migration import positions, literal_layout
from backend.pipeline.transcript_locator_migration import run_lock

VERSION = 'wang_transcript_three_way_merge_v1'


def rows(document):
    value = document.get('script') if isinstance(document, dict) else document
    if not isinstance(value, list):
        raise ValueError('expected a script array or document with script')
    ids = [str(row['index']) for row in value]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate paragraph identity')
    return value


class Text:
    def __init__(self, document):
        self.rows = rows(document)
        expanded = deepcopy(self.rows)
        for row in expanded:
            row['text'] = SOFT_DELETION.sub(lambda match: match.group(1), row.get('text', ''))
        spoken = {str(row['index']) for row in project_script(expanded).spoken_rows}
        self.parts = []; self.starts = []; self.deletions = []; self.text = ''
        for row in self.rows:
            if str(row['index']) not in spoken:
                continue
            raw = row.get('text', '')
            plain = ''; flags = []; cursor = 0
            for match in SOFT_DELETION.finditer(raw):
                prefix = raw[cursor:match.start()]; content = match.group(1)
                plain += prefix + content
                flags.extend([False] * len(prefix) + [True] * len(content))
                cursor = match.end()
            suffix = raw[cursor:]; plain += suffix; flags.extend([False] * len(suffix))
            if '~~' in plain:
                raise ValueError('unbalanced or nested soft deletion: ' + str(row['index']))
            start = len(self.text)
            self.starts.append(start)
            self.parts.append({'id': str(row['index']), 'start': start, 'end': start + len(plain),
                               'text': plain, 'flags': flags})
            self.text += plain
            self.deletions.extend(flags)

    def spans(self, start, end):
        result = []
        for part in self.parts:
            a, b = max(start, part['start']), min(end, part['end'])
            if a < b or (start == end and part['start'] <= start <= part['end']):
                result.append({'id': part['id'], 'start': a - part['start'], 'end': b - part['start'],
                               'text': self.text[a:b]})
        return result


def deletion_ranges(flags):
    start = None
    for i, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            yield start, i
            start = None


class Alignment:
    """One source-ordered alignment per pair, plus indexed unique context lookup."""
    def __init__(self, old, new):
        self.old = old; self.new = new; self.cache = {}
        self.blocks = None
        self.starts = None
        self.normal_old, self.old_offsets = literal_layout(old)
        self.normal_new, self.new_offsets = literal_layout(new)
        self.normal_blocks = None

    def hits(self, needle):
        if needle not in self.cache:
            self.cache[needle] = positions(self.new, needle)
        return self.cache[needle]

    def candidate(self, a, b):
        if self.blocks is None:
            self.blocks = difflib.SequenceMatcher(None, self.old, self.new, autojunk=False).get_matching_blocks()
            self.starts = [block.a for block in self.blocks]
        if not self.blocks or not self.old:
            return None
        def point(i):
            k = bisect_right(self.starts, i) - 1
            block = self.blocks[k]
            return block.b + i - block.a if block.a <= i < block.a + block.size else None
        left = point(a) if a < len(self.old) else len(self.new)
        right = point(b - 1) + 1 if b > a and point(b - 1) is not None else left
        return [left, right] if left is not None and right is not None and left <= right else None

    def locate(self, a, b, replacement):
        if self.old == self.new:
            return 'transfer', [a, b], 'identical_complete_spoken_text'
        # Exact contexts, never a similarity percentage or arbitrary first match.
        # A nearby substantive rewrite cannot silently count as the same words.
        for flank in (24, 48, 96):
            lo, hi = max(0, a - flank), min(len(self.old), b + flank)
            left, right = self.old[lo:a], self.old[b:hi]
            original = self.old[lo:hi]
            if len(left + right) < 16 or len(positions(self.old, original)) != 1:
                continue
            unchanged = self.hits(original)
            changed = self.hits(left + replacement + right)
            if len(unchanged) == 1:
                start = unchanged[0] + len(left)
                return 'transfer', [start, start + b - a], 'unique_literal_context'
            if len(changed) == 1:
                start = changed[0] + len(left)
                return 'already_present', [start, start + len(replacement)], 'new_transcript_already_contains_edit'
        na, nb = bisect_left(self.old_offsets, a), bisect_left(self.old_offsets, b)
        wanted = literal_layout(replacement)[0]
        if na < nb:
            for flank in (6, 8, 16, 24, 48):
                lo, hi = max(0, na - flank), min(len(self.normal_old), nb + flank)
                left, right = self.normal_old[lo:na], self.normal_old[nb:hi]
                original = self.normal_old[lo:hi]
                if len(left + right) < 12 or len(positions(self.normal_old, original)) != 1:
                    continue
                for mode, window, size in [('transfer', original, nb - na),
                        ('already_present', left + wanted + right, len(wanted))]:
                    if not size:continue
                    key = ('normalized', window)
                    if key not in self.cache:self.cache[key] = positions(self.normal_new, window)
                    hits = self.cache[key]
                    if len(hits) == 1:
                        ns = hits[0] + len(left); ne = ns + size
                        return mode, [self.new_offsets[ns], self.new_offsets[ne - 1] + 1], 'unique_layout_normalized_context'
        if na < nb:
            if self.normal_blocks is None:
                self.normal_blocks = difflib.SequenceMatcher(None, self.normal_old, self.normal_new,
                                                             autojunk=False).get_matching_blocks()
                self.normal_points = {i: block.b + i - block.a for block in self.normal_blocks
                                      for i in range(block.a, block.a + block.size)}
            before = self.normal_points.get(na - 1, -1 if na == 0 else None)
            after = self.normal_points.get(nb, len(self.normal_new) if nb == len(self.normal_old) else None)
            if before is not None and after is not None and before < after:
                ns, ne = before + 1, after
                left = []; right = []
                for block in self.normal_blocks:
                    end = min(block.a + block.size, na)
                    begin = max(block.a, nb)
                    if end - block.a >= 12 and na - end <= 80:
                        left.append((end - 12, end, block.b + end - block.a))
                    if block.a + block.size - begin >= 12 and begin - nb <= 80:
                        right.append((begin, begin + 12, block.b + begin - block.a))
                def unique(anchor):
                    x, y, _ = anchor; text = self.normal_old[x:y]
                    return len(positions(self.normal_old, text)) == len(positions(self.normal_new, text)) == 1
                bracketed = (any(pos <= ns and unique(v) for v in left for pos in [v[2]])
                             and any(pos >= ne and unique(v) for v in right for pos in [v[2]]))
                actual = self.normal_new[ns:ne]
                if bracketed and actual and actual in {self.normal_old[na:nb], wanted}:
                    mode = 'already_present' if actual == wanted else 'transfer'
                    return mode, [self.new_offsets[ns], self.new_offsets[ne - 1] + 1], 'ordered_unique_anchors_and_exact_edit_boundaries'
        return 'conflict', self.candidate(a, b), 'overlap_or_context_not_unique'


def serialize(chars):
    result = []; deleted = False
    for ch, flag in [*chars, ('', False)]:
        if flag != deleted:
            result.append('~~'); deleted = flag
        result.append(ch)
    return ''.join(result)


def merge(baseline, existing, incoming, *, resolutions=None):
    base, current, new = Text(baseline), Text(existing), Text(incoming)
    alignment = Alignment(base.text, new.text)
    current_alignment = alignment if current.text == base.text else (Alignment(current.text, new.text) if any(current.deletions) else None)
    changes = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, base.text, current.text, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        kind = 'soft_delete' if tag == 'delete' else 'text'
        changes.append({'kind': kind, 'baseline_span': [a, b], 'existing_span': [c, d],
                        'old': base.text[a:b], 'replacement': current.text[c:d]})
    for a, b in deletion_ranges(current.deletions):
        changes.append({'kind': 'soft_delete', 'existing_span': [a, b],
                        'old': current.text[a:b], 'replacement': current.text[a:b]})
    decisions = (resolutions or {}).get('changes', {})
    used = set(); operations = []; report = []
    for hunk in changes:
        hid = 'TM-' + m.sha(json.dumps(hunk, ensure_ascii=False, sort_keys=True).encode())[:24]
        hunk = {**hunk, 'id': hid}
        source = alignment if 'baseline_span' in hunk else current_alignment
        a, b = hunk.get('baseline_span', hunk['existing_span'])
        desired = hunk['replacement'] if hunk['kind'] == 'text' else source.old[a:b]
        status, target, reason = source.locate(a, b, desired)
        decision = decisions.get(hid)
        copied_replacement = hunk['replacement']
        if decision:
            used.add(hid)
            if not decision.get('reason') or not decision.get('basis'):
                raise ValueError('resolution needs its basis and reason')
            if decision['decision'] == 'keep_incoming':
                report.append({**hunk, 'status': 'explicit_keep_incoming', 'resolution': decision})
                continue
            if decision['decision'] != 'transfer':
                raise ValueError('unsupported resolution')
            target = decision['target_span']
            if (len(target) != 2 or any(type(v) is not int for v in target)
                    or not 0 <= target[0] <= target[1] <= len(new.text)
                    or new.text[target[0]:target[1]] != decision['expected_incoming']):
                raise ValueError('resolution does not match incoming literal slice')
            if hunk['kind'] == 'text' and 'existing_span' in decision:
                c, d = decision['existing_span']
                hc, hd = hunk['existing_span']
                if (type(c) is not int or type(d) is not int
                        or not 0 <= c <= hc <= hd <= d <= len(current.text)):
                    raise ValueError('replacement must cover the exact existing edit')
                copied_replacement = current.text[c:d]
            status, reason = 'transfer', 'explicit_scoped_resolution'
        spans = new.spans(*target) if target else []
        # A correction already present changes nothing; a deletion still needs its mask.
        if status == 'already_present' and hunk['kind'] == 'soft_delete':
            status = 'transfer'
        if status == 'transfer':
            if hunk['kind'] == 'text' and len(spans) != 1:
                status, reason = 'conflict', 'cross_paragraph_correction_requires_scoped_text_resolution'
            elif hunk['kind'] == 'text' and target[0] < target[1] and len(set(new.deletions[target[0]:target[1]])) > 1:
                status, reason = 'conflict', 'correction_overlaps_partial_soft_deletion'
            elif not spans:
                status, reason = 'conflict', 'target_has_no_physical_paragraph'
            else:
                operations.append({'hunk_id': hid, 'kind': hunk['kind'], 'target': target,
                                   'replacement': copied_replacement, 'spans': spans})
        report.append({**hunk, 'status': status, 'reason': reason, 'target_span': target,
                       'target_paragraphs': spans, 'resolution': decision})
    if set(decisions) != used:
        raise ValueError('resolution contains unknown or obsolete hunk IDs')
    # No two independently transferred corrections can silently replace one another.
    corrections = [op for op in operations if op['kind'] == 'text']
    for i, op in enumerate(corrections):
        for other in corrections[:i]:
            a, b = op['target']; c, d = other['target']
            if max(a, c) < min(b, d) or a == c:
                raise ValueError('overlapping correction intents require a combined explicit decision')
    result = deepcopy(new.rows); by_id = {str(row['index']): row for row in result}
    for part in new.parts:
        chars = list(zip(part['text'], part['flags']))
        for op in operations:
            if op['kind'] != 'soft_delete':
                continue
            a = max(op['target'][0], part['start']) - part['start']
            b = min(op['target'][1], part['end']) - part['start']
            if a < b:
                chars[a:b] = [(ch, True) for ch, _ in chars[a:b]]
        own = [op for op in corrections if op['spans'][0]['id'] == part['id']]
        for op in sorted(own, key=lambda value: value['target'][0], reverse=True):
            a, b = [p - part['start'] for p in op['target']]
            deleted = any(flag for _, flag in chars[a:b])
            # An insertion inside a deleted span must also remain deleted.
            if a == b:
                deleted = a > 0 and a < len(chars) and chars[a - 1][1] and chars[a][1]
            chars[a:b] = [(ch, deleted) for ch in op['replacement']]
        if own or chars != list(zip(part['text'], part['flags'])):
            by_id[part['id']]['text'] = serialize(chars)
    # Physical IDs, clocks, slides and headings come from incoming, byte-value intact.
    changed_ids = [str(a['index']) for a, b in zip(result, new.rows) if a != b]
    return {'schema_version': VERSION, 'script': result, 'changes': report,
            'summary': dict(Counter(row['status'] for row in report)),
            'changed_paragraph_ids': changed_ids, 'model_calls': 0,
            'all_existing_changes_accounted_for': len(report) == len(changes),
            'merge_complete': not any(row['status'] == 'conflict' for row in report),
            'claim_migration_complete': False}


def prepare(root, *, baseline, existing, incoming, item, instruction_file, resolutions=None):
    with run_lock(root) as root:
        files = {'baseline': Path(baseline).resolve(), 'existing': Path(existing).resolve(),
                 'incoming': Path(incoming).resolve(), 'instruction': Path(instruction_file).resolve()}
        if resolutions:
            files['resolutions'] = Path(resolutions).resolve()
        source_inputs = {k: m.binding(v) for k, v in files.items()}
        implementation = m.binding(Path(__file__))
        if not files['instruction'].read_text().strip():
            raise ValueError('owner direction required')
        if (root / 'manifest.json').exists():
            manifest = m.read(root / 'manifest.json')
            if (manifest['intent']['item'] != item
                    or manifest['intent']['source_inputs'] != source_inputs
                    or manifest['intent']['implementation'] != implementation):
                raise ValueError('merge inputs changed; preserve the prior plan')
            for bound in [*manifest['intent']['files'].values(), *manifest['outputs'].values()]:
                m.cm.source.verify_file(bound)
            return m.read(root / 'comparison.json')
        frozen = {k: m._write_bytes(root / 'inputs' / (k + ('.txt' if k == 'instruction' else '.json')),
                                   path.read_bytes()) for k, path in files.items()}
        intent = {'item': item, 'files': frozen, 'source_inputs': source_inputs,
                  'implementation': implementation}
        resolution = m.read(files['resolutions']) if resolutions else None
        if resolution and resolution.get('input_sha256') != {k: v['file_sha256'] for k, v in intent['files'].items() if k in {'baseline', 'existing', 'incoming'}}:
            raise ValueError('resolutions belong to different transcript versions')
        started = perf_counter()
        result = merge(m.read(files['baseline']), m.read(files['existing']), m.read(files['incoming']), resolutions=resolution)
        outputs = {'comparison': m.immutable(root / 'comparison.json', result),
                   'script': m.immutable(root / 'merged-script.json', result['script'])}
        body = '<meta charset="utf-8"><h1>逐字稿三方合併對照表</h1><p>草稿；尚未發布或遷移 Claim。</p><table><tr><th>既有修改</th><th>狀態</th><th>新版位置／上下文</th></tr>'
        for row in result['changes']:
            body += '<tr>' + ''.join('<td><pre>' + escape(str(value)) + '</pre></td>' for value in
                [row['old'] + '\n→ ' + row['replacement'], row['status'] + ': ' + row.get('reason', ''), row.get('target_paragraphs', '')]) + '</tr>'
        m._write_bytes(root / 'comparison.html', (body + '</table>').encode())
        for bound in [*intent['files'].values(), *source_inputs.values()]:
            m.cm.source.verify_file(bound)
        m.immutable(root / 'manifest.json', {'schema_version': VERSION, 'intent': intent, 'outputs': outputs})
        m.immutable(root / 'metrics.json', {'elapsed_seconds': perf_counter() - started,
                    'existing_changes': len(result['changes']), 'model_calls': 0})
        return result


def apply(review, root, *, actor):
    """Apply text changes to an already-adopted incoming editor through its journal."""
    root = Path(root).resolve(); manifest = m.read(root / 'manifest.json')
    for bound in [manifest['intent']['implementation'], *manifest['intent']['files'].values(), *manifest['outputs'].values()]:
        m.cm.source.verify_file(bound)
    report = m.read(root / 'comparison.json')
    if not report['merge_complete']:
        raise ValueError('unresolved merge conflicts; editor not changed')
    item = manifest['intent']['item']
    operation_id = 'transcript-merge-' + m.sha((root / 'manifest.json').read_bytes())[:32]
    if not report['changed_paragraph_ids']:
        doc = review.document(actor, item)
        if doc['script'] != report['script']:
            raise ValueError('editor differs from the unchanged merged script')
        result = {'operation_id': None, 'revision': doc['context']['revision'],
                  'changed_paragraph_ids': [], 'claim_writes': 0, 'published': False, 'model_calls': 0}
        m.immutable(root / 'editor-apply.json', result)
        return result
    request_path = root / 'editor-request.json'
    if not request_path.exists():
        doc = review.document(actor, item)
        if doc['script'] != rows(m.read(manifest['intent']['files']['incoming']['path'])):
            raise ValueError('editor is not the exact incoming transcript; never overwrite another draft')
        request = {'version': doc['version'], 'epoch': doc['epoch'], 'operation_id': operation_id,
                   'operations': [{'kind': 'text', 'id': str(row['index']), 'text': row['text']}
                                  for row in report['script'] if str(row['index']) in report['changed_paragraph_ids']]}
        m.immutable(request_path, {'actor': actor, 'request': request})
    saved = m.read(request_path)
    if saved['actor'] != actor:
        raise ValueError('merge actor changed')
    response = review.mutate(actor, item, saved['request'])
    current = review.document(actor, item)
    # mutate adds actor attribution on exactly the changed rows.
    expected = deepcopy(report['script'])
    for row in expected:
        if str(row['index']) in report['changed_paragraph_ids']:
            row['user_id'] = actor
    if current['script'] != expected:
        raise ValueError('merged editor readback differs')
    result = {'operation_id': operation_id, 'revision': response['revision'],
              'changed_paragraph_ids': report['changed_paragraph_ids'],
              'claim_writes': 0, 'published': False, 'model_calls': 0}
    m.immutable(root / 'editor-apply.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'apply'])
    parser.add_argument('--root', type=Path, required=True)
    for name in ('baseline', 'existing', 'incoming', 'instruction-file', 'resolutions', 'review-root'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--item'); parser.add_argument('--actor')
    args = parser.parse_args()
    if args.command == 'prepare':
        if not all([args.baseline, args.existing, args.incoming, args.item, args.instruction_file]):
            parser.error('prepare needs baseline, existing, incoming, item and instruction-file')
        result = prepare(args.root, baseline=args.baseline, existing=args.existing, incoming=args.incoming,
                         item=args.item, instruction_file=args.instruction_file, resolutions=args.resolutions)
        print(json.dumps({k: result[k] for k in ('summary', 'merge_complete', 'changed_paragraph_ids')}, ensure_ascii=False))
    else:
        if not args.review_root or not args.actor:
            parser.error('apply needs review-root and actor')
        from backend.api.sc_api.transcript_review import ReviewStore
        with run_lock(args.root):
            print(json.dumps(apply(ReviewStore(args.review_root), args.root, actor=args.actor), ensure_ascii=False))


if __name__ == '__main__':
    main()
