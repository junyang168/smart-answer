"""Replay saved model answers through the current creation code, with zero model calls.

Design: ``docs/wang-knowledge-platform/10-extraction/argument_layer_rebuild_design.md`` §5.
A saved answer is reused only for a request whose ``input`` is identical to the
one it answered. The prompt text may differ: that difference is the program
delta being tested, and it is recorded. When the input differs, the replay stops
at that step and names it, so an old answer is never applied to new material.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline import evidence_refresh as e


class InputChanged(Exception):
    """The current code sends this step different material than the saved answer saw."""


def saved_answers(old_root):
    out = []
    for request_path in sorted(Path(old_root).glob('**/gpt/*/request.json')):
        response_path = request_path.with_name('response.json')
        if response_path.exists():
            request = json.loads(request_path.read_text())
            out.append({'input': request.get('input'), 'instruction_sha256': e.digest(request.get('instruction', '')),
                        'path': str(response_path), 'answer': json.loads(response_path.read_text())})
    return out


class ReplayPorts:
    identity = {'gpt': {'model': 'replay of saved answers (no model call)', 'effort': 'n/a', 'backend': 'replay'}}

    def __init__(self, old_root):
        self.answers = saved_answers(old_root)
        self.used = []

    def repair(self, request):
        task = request['input'].get('task')
        for saved in self.answers:
            if saved['input'] == request['input']:
                self.used.append({'task': task, 'answer': saved['path'],
                                  'instruction_changed': saved['instruction_sha256'] != e.digest(request.get('instruction', ''))})
                return saved['answer']
        same_task = [s for s in self.answers if (s['input'] or {}).get('task') == task]
        changed = sorted({k for s in same_task for k in set(s['input']) | set(request['input'])
                          if s['input'].get(k) != request['input'].get(k)}) if same_task else []
        raise InputChanged(f'{task}: no saved answer has this input; fields that differ: {changed or "no saved answer for this task"}')


def replay(old_root, new_root, *, source_id, source_file, groups):
    """Run creation (steps first) into new_root, answering only from old_root."""
    from backend.pipeline import claim_creation_flash as creation
    new_root = Path(new_root)
    if new_root.exists() and any(new_root.iterdir()):
        raise ValueError(f'replay target is not empty: {new_root}')
    ports = ReplayPorts(old_root)
    try:
        out = creation.run(source_id, source_file, new_root, ports, groups=groups, grouped_package=True, steps_first=True)
        status = 'complete'
    except InputChanged as stop:
        out, status = {'stopped_at': str(stop)}, 'input_changed'
    report = {'status': status, 'old_root': str(old_root), 'new_root': str(new_root), 'model_calls': 0,
              'answers_used': ports.used, 'candidate_path': out.get('candidate_path') if isinstance(out, dict) else None,
              **({'stopped_at': out['stopped_at']} if status != 'complete' else {})}
    new_root.mkdir(parents=True, exist_ok=True)
    e.save(new_root / 'replay.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description='Replay saved answers through the current code; no model calls')
    p.add_argument('--old-root', required=True, help='Run folder holding the saved answers')
    p.add_argument('--new-root', required=True, help='Empty folder for the replayed run')
    p.add_argument('--source-id', required=True)
    p.add_argument('--source-file', required=True)
    p.add_argument('--groups', help='Groups JSON; defaults to the old run\'s groups.json')
    a = p.parse_args()
    groups = json.loads(Path(a.groups or Path(a.old_root) / 'groups.json').read_text())
    groups = groups.get('groups', groups) if isinstance(groups, dict) else groups
    print(json.dumps(replay(a.old_root, a.new_root, source_id=a.source_id, source_file=a.source_file, groups=groups),
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
