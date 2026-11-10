"""One publication record shared by both transcript editors and the catalog."""
from datetime import datetime
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 'sermon-publication.v1'
FIELDS = ('publication_schema_version', 'published_date', 'published_by', 'published_by_name')
CHICAGO = ZoneInfo('America/Chicago')


def publication_fields(actor, name, when):
    """Persist an explicit publisher (or unknown), and a timezone-aware date."""
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace('Z', '+00:00'))
    if when is not None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=CHICAGO)
        when = when.astimezone(CHICAGO).isoformat()
    return dict(publication_schema_version=SCHEMA_VERSION, published_date=when,
                published_by=actor or None, published_by_name=name if actor else None)
