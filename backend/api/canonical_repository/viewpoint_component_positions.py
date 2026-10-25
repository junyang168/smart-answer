"""Raw Unicode positions in explicit Claim fields, never statement fallbacks."""
from __future__ import annotations

import re
from collections.abc import Mapping


def raw_claim_text(claim, path):
    if not isinstance(path, list) or not path:
        raise ValueError('component field path is empty')
    value = claim
    for part in path:
        if isinstance(value, Mapping) and type(part) is str and part in value:
            value = value[part]
        elif isinstance(value, list) and type(part) is int and 0 <= part < len(value):
            value = value[part]
        else:
            raise ValueError('component field path does not resolve in raw Claim')
    if not isinstance(value, str):
        raise ValueError('component field path must resolve to raw text')
    return value


def validated_field_spans(locator, claim, *, claim_sha):
    """Return (path, start, end, exact) tuples bound to the full raw Claim.

    input_sha256 identifies the admitting packet. Graph readers cannot recreate
    that packet; its exact binding is enforced at admission. Here both SHA
    formats and current Claim SHA are checked without rewriting either value.
    """
    if (not isinstance(locator, Mapping)
            or set(locator) != {'input_sha256','claim_sha256','spans'}
            or any(not isinstance(locator.get(key),str) or not re.fullmatch(r'[0-9a-f]{64}',locator[key])
                   for key in ('input_sha256','claim_sha256'))
            or locator['claim_sha256'] != claim_sha(claim)
            or not isinstance(locator.get('spans'),list) or not locator['spans']):
        raise ValueError('component field locator input/Claim SHA or spans invalid')
    result = []
    for span in locator['spans']:
        if not isinstance(span,Mapping) or set(span) != {'path','start_char','end_char','exact_text'}:
            raise ValueError('component field span shape invalid')
        text = raw_claim_text(claim, span['path'])
        a, b, exact = span['start_char'], span['end_char'], span['exact_text']
        if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text)
                or not isinstance(exact,str) or text[a:b] != exact):
            raise ValueError('component field span does not match raw Unicode positions')
        current = (tuple(span['path']), a, b, exact)
        if any(p == current[0] and a < d and c < b for p,c,d,_ in result):
            raise ValueError('component field spans overlap or repeat within one path')
        result.append(current)
    return result
