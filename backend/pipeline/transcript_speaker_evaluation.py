"""Compare speaker candidates with held-out explicit human labels.

Unlabelled text is not a negative example. Scores concern only source characters
aligned to explicit labels; they cannot establish whole-lecture precision/recall.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

ROLE = r'(聽眾|听众|教授)(?:插話|插话|問|问)?\s*[:：]'


def labelled_reference(text: str) -> tuple[str, list[str | None]]:
    roles: list[str | None] = [None] * len(text)
    ignored = [False] * len(text)
    for match in re.finditer(r'~~.*?~~', text, re.S):
        ignored[match.start():match.end()] = [True] * len(match.group())
    for match in re.finditer(r'(?m)^\s*' + ROLE, text):
        next_label = re.search(r'(?m)^\s*' + ROLE, text[match.end():])
        end = match.end() + next_label.start() if next_label else len(text)
        role = 'professor' if match.group(1) == '教授' else 'audience'
        roles[match.end():end] = [role] * (end - match.end())
        ignored[match.start():match.end()] = [True] * len(match.group())
    for match in re.finditer(r'[（(]\s*' + ROLE + r'([^）)]*)[）)]', text):
        role = 'professor' if match.group(1) == '教授' else 'audience'
        start, end = match.span(2)
        roles[start:end] = [role] * (end - start)
        for left, right in [(match.start(), start), (end, match.end())]:
            ignored[left:right] = [True] * (right - left)
    return normalized_roles(text, roles, ignored)


def normalized_roles(text: str, roles: list, ignored: list | None = None) -> tuple[str, list]:
    result, labels = [], []
    for i, char in enumerate(text):
        if ignored and ignored[i]:
            continue
        for value in unicodedata.normalize('NFKC', char).casefold():
            if value.isalnum():
                result.append(value)
                labels.append(roles[i])
    return ''.join(result), labels


def compare(reference: str, turns: list[dict]) -> dict:
    expected, labels = labelled_reference(reference)
    text = ''.join(t['text'] for t in turns)
    actual, predicted = normalized_roles(text, [r for t in turns for r in [t['role']] * len(t['text'])])
    explicit = sum(role is not None for role in labels)
    matched, correct, errors = 0, 0, []
    for block in difflib.SequenceMatcher(None, expected, actual, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            left, right = block.a + offset, block.b + offset
            if labels[left] is None:
                continue
            matched += 1
            if labels[left] == predicted[right]:
                correct += 1
            else:
                errors.append({'reference_offset': left, 'character': expected[left],
                               'expected': labels[left], 'candidate': predicted[right]})
    return {'explicit_reference_characters': explicit,
            'matched_reference_characters': matched,
            'reference_alignment_coverage': matched / explicit if explicit else None,
            'correctly_attributed_matched_characters': correct,
            'label_agreement_on_matched_characters': correct / matched if matched else None,
            'mismatches': errors,
            'scope': 'explicit_labels_only_not_whole_lecture_accuracy'}
