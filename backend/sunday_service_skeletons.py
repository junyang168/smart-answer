"""Maintain the next two Sunday rows; choose only eligible, available presiders."""
from datetime import date, timedelta

from backend.api.models import SundayServiceEntry
from backend.sunday_data_lock import sunday_data_lock

REQUIRED = {'sermonSpeaker': '講員', 'presider': '司會', 'worshipLeader': '領詩'}


def next_two_sundays(as_of: date):
    first = as_of + timedelta(days=(6 - as_of.weekday()) % 7 or 7)
    return [first, first + timedelta(days=7)]


def skeleton_status(repository, as_of):
    entries = {entry.date: entry for entry in repository.list_sunday_services()}
    result = []
    for day in next_two_sundays(as_of):
        entry = entries.get(day.isoformat())
        fields = entry.model_dump(by_alias=True) if entry else {}
        workers = {worker.name: worker for worker in repository.list_sunday_workers()}
        missing = [label for key, label in REQUIRED.items() if not fields.get(key)
                   or (fields[key] in workers and not workers[fields[key]].is_available_on(day.isoformat()))]
        result.append({'date': day.isoformat(), 'missing': missing, 'complete': not missing})
    return result


def maintain_skeletons(repository, store, as_of):
    with sunday_data_lock():
        entries = {entry.date: entry for entry in repository.list_sunday_services()}
        workers = {worker.name: worker for worker in repository.list_sunday_workers()}
        historical = {entry.presider for entry in entries.values() if entry.date <= as_of.isoformat() and entry.presider}
        eligible = {name for name, worker in workers.items() if '司會' in worker.preferred_roles
                    or (not worker.preferred_roles and name in historical)}
        for day in next_two_sundays(as_of):
            key = day.isoformat()
            if key not in entries:
                entry = SundayServiceEntry(date=key, holdHolyCommunion=day.day <= 7)
                repository.create_sunday_service(entry)
                entries[key] = entry
            entry = entries[key]
            rotation = store.settings().worship_rotation or sorted(name for name, worker in workers.items() if '領詩' in worker.preferred_roles)
            leader_receipt = store.get('auto_worshipLeader:' + key)
            leader_owned = leader_receipt and leader_receipt.get('status') in {'applied', 'applying'} and leader_receipt.get('after') == entry.worship_leader
            occupied_roles = {entry.presider, entry.sermon_speaker, entry.pianist, *entry.scripture_readers}
            needs_leader = not entry.worship_leader or (leader_owned and entry.worship_leader in workers and
                (not workers[entry.worship_leader].is_available_on(key) or entry.worship_leader in occupied_roles))
            if needs_leader and rotation:
                previous = sorted((prior for prior in entries.values() if prior.date < key and prior.worship_leader in rotation), key=lambda prior: prior.date)
                anchor = previous[-1] if previous else None
                expected = None
                if anchor:
                    weeks = (day - date.fromisoformat(anchor.date)).days // 7
                    expected = rotation[(rotation.index(anchor.worship_leader) + weeks) % len(rotation)]
                available_leaders = [name for name in rotation if name in workers and workers[name].is_available_on(key)]
                preferred_leaders = [name for name in available_leaders if name not in occupied_roles] or available_leaders
                chosen_leader = expected if expected in preferred_leaders else next(iter(preferred_leaders), None)
                if chosen_leader != entry.worship_leader:
                    receipt = {'before': entry.worship_leader, 'after': chosen_leader, 'status': 'applying', 'basis': 'existing_prepopulation_rotation', 'anchor_date': anchor.date if anchor else None}
                    store.put('auto_worshipLeader:' + key, receipt)
                    updated = entry.model_copy(update={'worship_leader': chosen_leader})
                    try:
                        repository.update_sunday_service(key, updated)
                    except ValueError as exc:
                        store.put('auto_worshipLeader:' + key, {**receipt, 'status': 'blocked', 'error': str(exc)})
                    else:
                        entries[key] = entry = updated
                        store.put('auto_worshipLeader:' + key, {**receipt, 'status': 'applied'})
            ownership = store.get('auto_presider:' + key)
            owned = ownership and ownership.get('status') in {'applied', 'applying'} and ownership.get('after') == entry.presider
            occupied = {entry.worship_leader, entry.sermon_speaker, entry.pianist, *entry.scripture_readers}
            if entry.presider and (not owned or (entry.presider in workers and workers[entry.presider].is_available_on(key) and entry.presider not in occupied)):
                continue
            candidates = [name for name in eligible if workers[name].is_available_on(key) and name not in occupied]
            if not candidates:
                continue
            def rank(name):
                assigned = [date.fromisoformat(other.date) for other in entries.values() if other.presider == name and other.date != key]
                recent = sum(as_of - timedelta(days=84) <= prior <= day for prior in assigned)
                last = max((prior for prior in assigned if prior < day), default=date.min)
                return recent, last, name
            chosen = min(candidates, key=rank)
            receipt = {'before': entry.presider, 'after': chosen, 'status': 'applying', 'basis': 'eligible_available_rotation'}
            store.put('auto_presider:' + key, receipt)
            updated = entry.model_copy(update={'presider': chosen})
            try:
                repository.update_sunday_service(key, updated)
            except ValueError as exc:
                store.put('auto_presider:' + key, {**receipt, 'status': 'blocked', 'error': str(exc)})
                continue
            entries[key] = updated
            store.put('auto_presider:' + key, {**receipt, 'status': 'applied'})
    return skeleton_status(repository, as_of)
