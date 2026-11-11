"""Source-fragment attribution, never inherited from a material's summary.

Only explicit transcript turn labels or fragment-local facts establish roles.
Unlabelled speech is unknown; a professor's summary can cite audience context.
"""
import re

LABEL = re.compile(r'(?m)^[ \t]*(?:\*\*)?(教授|聽眾|听众)(?:插話|插话|問|问)?'
                   r'(?:\*\*)?\s*[:：](?:\*\*)?')


def source_roles(fragment, paragraph=None):
    quote = fragment['verbatim_excerpt']
    text = paragraph if paragraph is not None else quote
    labels = list(LABEL.finditer(text))
    literal = None
    if labels and text.count(quote) == 1:
        start = text.index(quote); end = start + len(quote)
        spans = [(0, labels[0].start(), None)] + [
            (m.start(), labels[i+1].start() if i+1 < len(labels) else len(text),
             'professor' if m.group(1) == '教授' else 'audience')
            for i, m in enumerate(labels)]
        covered = {role for a, b, role in spans
                   if max(a, start) < min(b, end)
                   and text[max(a, start):min(b, end)].strip(' \t\n*：:')}
        if covered and None not in covered:
            literal = next(iter(covered)) if len(covered) == 1 else 'mixed'
    speaker = literal or fragment.get('speaker')
    # A turn label proves who spoke, not whether that person's fragment is
    # an adopted assertion, quotation, question, or contextual premise.
    stance = fragment.get('stance') if fragment.get('speaker') == speaker else None
    assertive = (speaker == 'professor' and stance == 'asserted') if speaker and stance else (
        False if speaker in {'audience', 'quoted_source', 'mixed'} else None)
    return {'speaker': speaker, 'stance': stance, 'assertive': assertive}
