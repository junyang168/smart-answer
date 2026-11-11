"""One-time normalization of publication catalog fields; never rewrite manuscripts."""
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path

from backend.api.sc_api.sermon_publication import FIELDS, SCHEMA_VERSION, publication_fields
from backend.api.sc_api.transcript_review import ReviewStore, atomic, digest, file_hash, read


def utc_second(value):
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value.astimezone(timezone.utc).replace(microsecond=0) if value.tzinfo else None
    except (ValueError, AttributeError):
        return None


def plan(root, users_root=None):
    root = Path(root).resolve()
    users_path = Path(users_root or root)/'config/config.json'
    catalog_path = root/'config/sermon.json'
    audit_path = root/'logs/sermon_meta_audit.log'
    guards = {str(p): file_hash(p) for p in (catalog_path, users_path, audit_path)}
    users = {u['id']: u for u in read(users_path, {}).get('users', [])}
    events = {}
    if audit_path.exists():
        for line in audit_path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('context') == 'publish' and isinstance(event.get('item'), str):
                events.setdefault(event['item'], []).append(event)
    entries = []
    for meta in read(catalog_path, []):
        item = meta['item']
        folder = ReviewStore(root).folder(item)
        # Promoted Beta archives have one writable authority in production.
        if (folder/'production-link.json').exists():
            continue
        path = root/'script_published'/f'{item}.json'
        if not path.is_file():
            continue
        if meta.get('publication_schema_version') == SCHEMA_VERSION and all(k in meta for k in FIELDS):
            continue
        guards[str(path)] = file_hash(path)
        saved = read(path, {})
        published = saved.get('metadata', {}) if isinstance(saved, dict) else {}
        if meta.get('status') != 'published' and published.get('status') != 'published' and published.get('review_revision') is None:
            continue
        actor = published.get('published_by') or published.get('reviewed_by')
        actor_name = None
        when = meta.get('published_date') or published.get('published_date')
        evidence = 'publication_metadata' if actor else 'unknown'
        revision = published.get('review_revision')
        if type(revision) is int and revision >= 0:
            history_path = folder/'revisions'/f'{revision:08}.json'
            guards[str(history_path)] = file_hash(history_path)
            history = read(history_path, {})
            if (history.get('kind') == 'publish' and history.get('revision') == revision
                    and history.get('operation_id') == published.get('review_operation_id')
                    and (not published.get('review_snapshot_sha256')
                         or published['review_snapshot_sha256'] == guards[str(history_path)])):
                actor = history.get('actor') or actor
                when = history.get('time') or when
                evidence = 'publish_revision'
        elif not actor:
            matches = []
            second = utc_second(published.get('last_updated'))
            for event in events.get(item, []):
                changes = event.get('changes')
                change = changes.get('published_date') if isinstance(changes, dict) else None
                if (when and second is not None and isinstance(change, dict) and change.get('new') == when
                        and utc_second(event.get('timestamp')) == second
                        and isinstance(event.get('actor_id'), str) and event['actor_id']):
                    matches.append(event)
            if len({e['actor_id'] for e in matches}) == 1:
                actor = matches[-1]['actor_id']
                actor_name = matches[-1].get('actor_name')
                evidence = 'legacy_publish_audit'
        name = users.get(actor, {}).get('name') or actor_name
        fields = publication_fields(actor, name, when)
        entries.append(dict(item=item, fields=fields, evidence=evidence,
                            manuscript_sha256=guards[str(path)]))
    return dict(schema_version='sermon-publication-migration.v1', root=str(root),
                guards=guards, entries=entries)


def apply(plan_record, receipt_dir):
    root = Path(plan_record['root'])
    receipt_dir = Path(receipt_dir).resolve()
    if receipt_dir == root or receipt_dir.is_relative_to(root):
        raise ValueError('migration receipts must be outside the data root')
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    catalog_path = root/'config/sermon.json'
    with ExitStack() as locks:
        for entry in sorted(plan_record['entries'], key=lambda e: e['item']):
            folder = ReviewStore(root).folder(entry['item'])
            folder.mkdir(parents=True, exist_ok=True)
            handle = locks.enter_context((folder/'workflow.lock').open('a+b'))
            fcntl.flock(handle, fcntl.LOCK_EX)
            if list((folder/'operations').glob('*.prepared.json')):
                raise ValueError(f"pending editor operation: {entry['item']}")
        handle = locks.enter_context((root/'config/.sermon-review.lock').open('a+b'))
        fcntl.flock(handle, fcntl.LOCK_EX)
        for filename, expected in plan_record['guards'].items():
            if file_hash(Path(filename)) != expected:
                raise ValueError(f'input changed before publication migration: {filename}')
        before = read(catalog_path, [])
        updates = {e['item']: e['fields'] for e in plan_record['entries']}
        after = [{**row, **updates.get(row['item'], {})} for row in before]
        (receipt_dir/'catalog.before.json').write_bytes(catalog_path.read_bytes())
        atomic(receipt_dir/'plan.json', plan_record)
        atomic(receipt_dir/'intent.json', dict(state='prepared', after_sha256=digest(after)))
        if updates:
            atomic(catalog_path, after)
        if read(catalog_path) != after:
            raise ValueError('catalog readback differs from migration')
        for entry in plan_record['entries']:
            path = root/'script_published'/f"{entry['item']}.json"
            if file_hash(path) != entry['manuscript_sha256']:
                raise ValueError(f'manuscript changed during publication migration: {path}')
        result = dict(state='complete', migrated=len(updates),
                      catalog_before_sha256=plan_record['guards'][str(catalog_path)],
                      catalog_after_sha256=file_hash(catalog_path),
                      manuscripts_unchanged=True,
                      manuscripts=[dict(item=e['item'], before_sha256=e['manuscript_sha256'],
                                        after_sha256=file_hash(root/'script_published'/f"{e['item']}.json"))
                                   for e in plan_record['entries']])
        atomic(receipt_dir/'receipt.json', result)
        return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--users-root', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--receipt-dir', type=Path)
    args = parser.parse_args()
    planned = plan(args.data_root, args.users_root)
    if args.apply:
        if args.receipt_dir is None:
            parser.error('--apply requires --receipt-dir')
        print(json.dumps(apply(planned, args.receipt_dir), ensure_ascii=False))
    else:
        from collections import Counter
        print(json.dumps(dict(entries=len(planned['entries']),
                              evidence=dict(Counter(e['evidence'] for e in planned['entries']))), ensure_ascii=False))


if __name__ == '__main__':
    main()
