"""Read the review publisher's exact editorial slide wrapper as non-speech.

This grants no visual evidence authority. Bare SVGs and unknown wrappers remain
visible to the common source projection's normal visual-adapter rejection.
"""
from copy import deepcopy
import re

WRAPPER = re.compile(
    r'<figure data-transcript-slide="v1" data-source-image-sha256="([0-9a-f]{64})">\s*'
    r'<img src="[^"<>]+" alt="投影片（非口述）" />\s*'
    r'<figcaption>投影片（非口述）</figcaption>\s*'
    r'<details><summary>投影片文字（機器重建，非口述）</summary>\s*'
    r'(?P<svg><svg\b[^>]*>.*?</svg>)\s*</details>\s*</figure>', re.S)


def spoken_document(document):
    result = deepcopy(document)
    rows = result.get('script') if isinstance(result, dict) else result
    changed = False
    for row in rows:
        text = str(row.get('text') or '')
        consumed = False
        while text.startswith('<figure data-transcript-slide='):
            match = WRAPPER.match(text)
            if not match or f'data-source-image-sha256="{match.group(1)}"' not in match.group('svg'):
                raise ValueError('unrecognized or unbound publisher slide wrapper')
            # The publisher joins figures and original text with precisely two
            # newlines. Remove only those delimiters, preserving original text.
            end = match.end()
            if text[end:end + 2] != '\n\n':
                raise ValueError('publisher slide separator differs')
            text = text[end + 2:]
            changed = True
            consumed = True
        if consumed:
            row['text'] = text
    return result, changed
