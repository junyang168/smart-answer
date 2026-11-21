"""Convert received Sunday prose while preserving links and identifier values."""
import re
from opencc import OpenCC

_TRADITIONAL = OpenCC('s2t')
_LINK = re.compile(r'(https?://[^\s<>]+)', re.IGNORECASE)
FIELDS = ('sermon_title', 'hymn_notes', 'response_hymn_notes',
          'announcements_markdown', 'health_prayer_markdown')


def traditional(text):
    if text is None:
        return None
    return ''.join(part if re.match(r'https?://', part, re.I) else _TRADITIONAL.convert(part)
                   for part in _LINK.split(text))


def normalize_service(entry):
    return entry.model_copy(update={field: traditional(getattr(entry, field)) for field in FIELDS})
