"""One-time backfill (#702): mark every existing SourceFragment that holds an
explicit Scripture citation with `scripture_refs`, the field Evidence and
Observation use. New citation SourceFragments are marked when created
(`claim_flash_source_fill.run_citations`); this covers the ones saved earlier.

Source layer only: SourceFragment updates, no Evidence, Observation or Claim
changes. Without Jev ports, quotes introduced by 「耶穌說」 etc. are not judged
and the saved list says so."""
import fcntl
import json
from pathlib import Path

from backend.pipeline import claim_flash_citations as cit, claim_flash_common as f
from backend.pipeline import claim_flash_source_fill as fill, evidence_refresh as e

VERSION = 'claim_flash_citation_tags_v1'
NUMERALS = set('零〇一二三四五六七八九十百0123456789')


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


def run(store, source_id, root, ports=None, *, holy=None, apply=False, drop=()):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root/'tags.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        intent = {'version':VERSION, 'source':pin, 'introduced_judged':ports is not None, **({'drop':sorted(map(list, drop))} if drop else {})}
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
        listing = cit.build(records, sources, source_id, root, ports, holy=holy)
        valid = fill.valid_fragments(records, sources, source_id)
        updates = {}
        found = tags(listing, valid, holy)
        # drop: (paragraph, ref) pairs a corrected list no longer cites, e.g.
        # Gal 2:1 for 「《約翰一書》第二章一節」. Other marks stay.
        drop = {tuple(x) for x in drop}
        keys = set(found) | {k for k in valid if any((valid[k].get('paragraph_key'), r) in drop
                                                     for r in records[k]['payload'].get('scripture_refs', []))}
        for key in sorted(keys):
            payload = records[key]['payload']
            pk = payload.get('paragraph_key')
            kept = {r for r in payload.get('scripture_refs', []) if (pk, r) not in drop}
            refs = sorted(kept | found.get(key, set()))
            if refs == payload.get('scripture_refs', []):
                continue
            post = {k:v for k,v in payload.items() if k != 'scripture_refs'}
            updates[key] = {**post, 'scripture_refs': refs} if refs else post
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
