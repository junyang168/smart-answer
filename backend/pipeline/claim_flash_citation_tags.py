"""Update Claim source layer, step 3 (#702): mark every existing SourceFragment
that holds an explicit Scripture citation with `scripture_refs`, the field
Evidence and Observation use, and remove a mark the paragraph's current
citation list no longer supports. New citation SourceFragments are marked when
created (`claim_flash_source_fill.run_citations`).

Source layer only: SourceFragment updates, no Evidence, Observation or Claim
changes. Without Jev ports, quotes introduced by 「耶穌說」 etc. are not judged
and the saved list says so."""
import fcntl
import json
from pathlib import Path

from backend.pipeline import claim_flash_citations as cit, claim_flash_common as f
from backend.pipeline import claim_flash_source_fill as fill, evidence_refresh as e

VERSION = 'claim_flash_citation_tags_v2'
NUMERALS = cit.NUMERALS


def holds(citation, excerpt, holy):
    """The SourceFragment contains the citation itself: the words naming or
    introducing the verse, or an 8-character run of the verse he reads."""
    t = cit.norm(excerpt)
    if citation['basis'] != 'verbatim':
        mark = cit.norm(citation.get('mark') or '')
        # 「三節」 must not be the tail of 「三十三節」.
        return bool(mark) and any(t[i - 1:i] not in NUMERALS
                                  for i in range(len(t)) if t.startswith(mark, i))
    verse = cit.norm(holy.verses.get(tuple(citation['key']), ''))
    n = cit.QUOTE_CHARS
    return any(t[i:i + n] in verse for i in range(len(t) - n + 1))


def parse(ref):
    """'Matt 19:6' -> ('Matt', 19, 6); 'Matt 19' -> ('Matt', 19, None)."""
    book, _, place = ref.rpartition(' ')
    chapter, _, verse = place.partition(':')
    return book, int(chapter), int(verse) if verse else None


def supported(ref, cited):
    """The paragraph's current list still cites this verse, or for a chapter
    mark, some verse of that chapter. `scripture_refs` is written only by this
    flow, so no older list is needed to correct it."""
    key = parse(ref)
    return key in cited or (key[2] is None and any(k[:2] == key[:2] for k in cited))


def tags(listing, valid, holy):
    by_paragraph = {}
    for key, payload in valid.items():
        by_paragraph.setdefault(payload.get('paragraph_key'), []).append((key, payload))
    found = {}
    for c in listing['citations']:
        for key, payload in by_paragraph.get(c['paragraph_key'], []):
            if holds(c, payload.get('verbatim_excerpt') or '', holy):
                found.setdefault(key, set()).add(c['ref'])
    return found


def run(store, source_id, root, ports=None, *, holy=None, apply=False, listing=None):
    """`listing` is the citation list the source layer just built; without it
    the list is built here (Jev introduced picks only with ports)."""
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root/'tags.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        intent = {'version':VERSION, 'source':pin,
                  **({'listing':e.digest(listing)} if listing is not None else {'introduced_judged':ports is not None})}
        if (root/'verification.json').exists():
            if json.loads((root/'intent.json').read_text()) != intent:
                raise ValueError('citation tag saved input differs')
            f.source_unchanged(json.loads((root/'before.json').read_text())['sources'])
            if apply:
                return e.commit_verified(store, root, source_fragments_only=True)
            saved = root/'result.json'
            return json.loads((saved if saved.exists() else root/'verification.json').read_text())
        holy = holy or cit.default_bible()
        records, sources = f.load_source(store, source_id, visual_context=True)
        if pin != {'source_id':source_id, **sources[source_id]['file']}:
            raise ValueError('source changed between binding and census')
        e.save(root/'intent.json', intent)
        if listing is None:
            listing = cit.build(records, sources, source_id, root, ports, holy=holy)
        valid = fill.valid_fragments(records, sources, source_id)
        cited = {}
        for c in listing['citations']:
            cited.setdefault(c['paragraph_key'], set()).add(tuple(c['key']))
        found = tags(listing, valid, holy)
        # A mark stays when the fragment holds a lead-in the models were asked
        # about with that verse offered: a fresh Jev or GPT answer alone never
        # removes one (2Cor 6:14 for 「保羅說：「信與不信的原不相配」」).
        asked = {}
        for q in listing.get('asked', []):
            for key, payload in valid.items():
                if payload.get('paragraph_key') == q['paragraph_key'] and holds(
                        {'basis': 'introduced', 'mark': q['mark']}, payload.get('verbatim_excerpt') or '', holy):
                    asked.setdefault(key, set()).update(q['candidates'])
        updates = {}
        for key in sorted(valid):
            payload = records[key]['payload']
            current = payload.get('scripture_refs') or []
            kept = {r for r in current if supported(r, cited.get(payload.get('paragraph_key'), set()))
                    or r in asked.get(key, ())}
            refs = sorted(kept | found.get(key, set()))
            if refs == sorted(current):
                continue
            # The store reads a missing field as unchanged; no marks left is [].
            updates[key] = {**payload, 'scripture_refs': refs}
        f.source_unchanged(sources)
        read_keys = sorted({'source_documents/'+source_id, *updates})
        result = {'version':VERSION, 'source_id':source_id, 'status':'verified', 'items':[],
                  'citations':len(listing['citations']), 'introduced':listing.get('introduced', 'judged'),
                  'updates':updates, 'new_fragments':{}, 'read_keys':read_keys, 'applied':False,
                  'model_calls':0 if ports is None else None, 'claim_qualification':'not_granted',
                  'scope':'mark existing SourceFragments holding an explicit Scripture citation with scripture_refs',
                  'commit_reason':'Mark SourceFragments that hold an explicit Scripture citation with scripture_refs; no Evidence, Observation or Claim changes'}
        e.save(root/'before.json', {'sources':sources, 'records':{k:records[k] for k in read_keys}})
        e.save(root/'verification.json', result)
        return e.commit_verified(store, root, source_fragments_only=True) if apply else e.publish(root, result)
