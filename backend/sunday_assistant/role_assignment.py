"""Rotate eligible readers, at most four, each reading at most eight verses."""
from datetime import date, timedelta

from backend.sunday_data_lock import sunday_data_lock


def required_reader_count(references):
    from backend.api.scripture import parse_reference
    from backend.sunday_scripture_layout import minimum_readers
    lengths=[]
    for reference in references:
        info=parse_reference(reference)
        lengths.append(info['end']-info['start']+1)
    return minimum_readers(lengths)


def assign_readers(repository, store, days):
    with sunday_data_lock():
        entries = {entry.date: entry for entry in repository.list_sunday_services()}
        workers = {worker.name: worker for worker in repository.list_sunday_workers()}
        for day in sorted(days):
            entry = entries.get(day)
            if entry is None or not entry.scripture:
                continue
            key = 'auto_scriptureReaders:' + day
            receipt = store.get(key)
            owned = receipt and receipt.get('status') in {'applied','applying'} and receipt.get('after') == entry.scripture_readers
            if entry.scripture_readers and not owned:
                continue  # Preserve manually saved reader order.
            candidates = [name for name, worker in workers.items() if worker.is_available_on(day) and
                          '讀經經文' in worker.preferred_roles]
            required = required_reader_count(entry.scripture)
            if len(candidates) < required:
                continue
            occupied = {entry.sermon_speaker, entry.pianist, entry.presider, entry.worship_leader}
            if owned and len(entry.scripture_readers) == required and receipt.get('scripture') == entry.scripture and all(name in candidates and name not in occupied for name in entry.scripture_readers):
                continue
            previous = entries.get((date.fromisoformat(day)-timedelta(days=7)).isoformat())
            previous_readers = set(previous.scripture_readers) if previous else set()
            def rank(name):
                prior_dates = [date.fromisoformat(prior.date) for prior in entries.values() if prior.date < day and name in prior.scripture_readers]
                recent = sum(value >= date.fromisoformat(day)-timedelta(days=84) for value in prior_dates)
                return name in occupied, name in previous_readers, recent, max(prior_dates, default=date.min), name
            chosen = sorted(candidates, key=rank)[:required]
            if entry.scripture_readers == chosen:
                continue
            record = {'before': entry.scripture_readers, 'after': chosen, 'scripture': entry.scripture,
                      'status': 'applying', 'basis': 'eligible_available_reading_rotation'}
            store.put(key, record)
            updated = entry.model_copy(update={'scripture_readers': chosen})
            repository.update_sunday_service(day, updated)
            entries[day] = updated
            store.put(key, {**record, 'status': 'applied'})
