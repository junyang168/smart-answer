"""Additive physical-source bindings; never changes a provider quote.

Presentation matches are explicitly distinct from raw verbatim matches.
Deletion barriers, punctuation, words and Greek diacritics are preserved.
"""
from __future__ import annotations

import hashlib
import re


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def visible_text(text):
    # Never join two live passages across a proofreader's deletion.
    text = re.sub(r'~~([^~]+?)~~', '\x00', text, flags=re.S)
    text = re.sub(r'\*\*([^*]+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'\1', text)
    def word_character(c):
        return c.isalnum() and not ('\u3400' <= c <= '\u9fff')
    def whitespace(match):
        left = text[match.start() - 1] if match.start() else ''
        right = text[match.end()] if match.end() < len(text) else ''
        return ' ' if left and right and word_character(left) and word_character(right) else ''
    return re.sub(r'\s+', whitespace, text)


def bind(citation, source):
    if not isinstance(citation, dict):
        raise ValueError('citation must be an object')
    ordinal, quote = citation.get('physical_ordinal'), citation.get('quote')
    if type(ordinal) is not int or not isinstance(quote, str) or not quote.strip():
        raise ValueError('invalid citation shape')
    rows = [r for r in source['segments'] if r['physical_ordinal'] == ordinal]
    if len(rows) != 1 or rows[0]['editorial']:
        raise ValueError('citation is not a professor body segment')
    row = rows[0]
    physical = row.get('physical_text', row['live_text'])
    live_runs = re.split(r'~~[^~]+?~~', physical, flags=re.S)
    if quote in row['live_text'] and any(quote in run for run in live_runs):
        kind = 'raw_verbatim'
    elif visible_text(quote) and visible_text(quote) in visible_text(physical):
        kind = 'presentation_equivalent_not_raw_verbatim'
    else:
        raise ValueError('citation omits/changes source words or punctuation, or crosses deletion')
    return {'physical_ordinal': ordinal, 'binding_kind': kind,
            'quote_sha256': digest(quote), 'physical_segment_sha256': digest(physical),
            'quote_unchanged': True, 'physical_source_unchanged': True}


def observation_evidence(observation, source):
    """Literal citation integrity never decides whether a judgment is faithful."""
    citations = []
    for citation in observation.get('citations', []):
        try:
            citations.append({'citation_integrity': 'bound', **bind(citation, source)})
        except ValueError as error:
            citations.append({'physical_ordinal': citation.get('physical_ordinal'),
                'citation_integrity': 'needs_source_check', 'reason': str(error),
                'quote_sha256': digest(citation.get('quote', ''))})
    return {'observation_id': observation['observation_id'], 'citations': citations,
            'semantic_judgment_state': 'not_evaluated', 'is_content_defect': None,
            'requires_semantic_source_check': any(c['citation_integrity'] != 'bound' for c in citations),
            'retained_in_denominator': True, 'block_entire_source': False}
