"""Grade whether a lecture's points answer the questions their topics raise.

Key: ``argument_golden/topic-questions.json`` (owner 2026-11-18). An item passes when
one Claim's own sentence matches every group of the item; words that sit only in the
reasons under it do not count. Reads a run's candidate; no model call.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

KEY = Path(__file__).parent / 'argument_golden' / 'topic-questions.json'


def statement(claim: dict) -> str:
    return claim.get('statement') or claim.get('title') or ''


def grade(key: dict, lecture: str, claims: list[dict]) -> list[dict]:
    out = []
    for item in key['items']:
        if item['lecture'] != lecture:
            continue
        best, best_hits = None, -1
        for c in claims:
            text = statement(c)
            hits = sum(any(re.search(p, text) for p in group) for group in item['groups'])
            if hits > best_hits:
                best, best_hits = c, hits
        out.append({'id': item['id'], 'question': item['question'], 'optional': item.get('optional', False),
                    'passed': best_hits == len(item['groups']), 'groups_held': f"{best_hits}/{len(item['groups'])}",
                    'closest_claim': best['claim_id'][-6:] if best else None,
                    'closest_statement': statement(best) if best else ''})
    return out


def main():
    p = argparse.ArgumentParser(description="Grade a run's points against the topic-question key")
    p.add_argument('--lecture', required=True, help='transcript id, e.g. nysc-2011-2-1')
    p.add_argument('--candidate', required=True, type=Path)
    a = p.parse_args()
    claims = json.loads(a.candidate.read_text(encoding='utf-8'))['claims']
    print(json.dumps(grade(json.loads(KEY.read_text(encoding='utf-8')), a.lecture, claims), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
