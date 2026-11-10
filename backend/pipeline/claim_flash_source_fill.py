"""Complete literal source spans from a saved coverage report; no upper-layer writes."""
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path

from backend.pipeline import claim_flash_common as f, claim_flash_coverage as coverage
from backend.pipeline import claim_flash_source as source_stage, evidence_refresh as e

VERSION = 'claim_flash_source_fill_v1'
MISSING = {'missing_material', 'missing_source_binding', 'missing_scripture_reference'}
SEPARATORS = set('。．.，,；;、：:！？!? \t\r\n')


def read_report(path, source_id, sources):
    path = Path(path).resolve()
    report = json.loads(path.read_text())
    if (report.get('contract') != coverage.CONTRACT or report.get('source_id') != source_id
            or report.get('stage') != 'reverse_coverage'
            or report.get('report_path') != str(path) or report.get('database_writes') != 0):
        raise ValueError('source fill requires the saved same-source read-only coverage report')
    parent = path.parents[2]
    intent_path = parent/'intent.json'
    intent = json.loads(intent_path.read_text())
    source = sources[source_id]
    if (intent.get('contract') != coverage.CONTRACT or intent.get('source_id') != source_id
            or intent.get('source') != source['file']):
        raise ValueError('coverage report source version differs; do not fill from stale findings')
    return report, {'path':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'intent_sha256':hashlib.sha256(intent_path.read_bytes()).hexdigest()}


def covered_by(span, paragraph, valid):
    """A containing fragment or a complete union of exact spans; partial coverage is not enough."""
    pk, quote = span['paragraph_key'], span['quote']
    local = {key:p for key,p in valid.items() if p.get('paragraph_key') == pk}
    containing = [key for key,p in local.items() if quote in p['verbatim_excerpt']]
    if containing:
        return containing
    text = paragraph['text']
    if text.count(quote) != 1:
        return []
    start = text.index(quote); end = start+len(quote); intervals = []
    for key,p in local.items():
        raw = p['verbatim_excerpt']
        if text.count(raw) == 1:
            left = text.index(raw); right = left+len(raw)
            if right > start and left < end:
                intervals.append((left,right,key))
    cursor = start; used = []
    for left,right,key in sorted(intervals):
        if left > cursor and not set(text[cursor:min(left,end)]) <= SEPARATORS:
            return []
        if right > cursor:
            cursor = right; used.append(key)
        if cursor >= end:
            return used
    return used if used and set(text[cursor:end]) <= SEPARATORS else []


def plan(records, sources, source_id, report):
    """Preserve the report's exact spans, including per-candidate sources of joint findings."""
    source = source_stage.current_source(records, sources, source_id)
    if report.get('source_id') != source_id or report.get('contract') != coverage.CONTRACT:
        raise ValueError('source fill report identity differs')
    valid = {}; generated = {}; items = []; pending = []; pins = {'source_documents/'+source_id}
    for key, row in records.items():
        if not key.startswith('source_fragments/') or row.get('retired'):
            continue
        fragment = row['payload']
        if fragment.get('source_id') != source_id or fragment.get('fragment_id') != key.split('/',1)[1]:
            continue
        try:
            e.material({'evidence_step_id':key, 'statement':'',
                        'source_fragment_ids':[fragment['fragment_id']]}, records, sources)
        except (KeyError, ValueError):
            continue
        valid[key] = fragment
    for group in report['findings']:
        for finding in group['findings']:
            if finding['verdict'] not in MISSING:
                continue
            # Keep upper-layer decisions as handoff, even when their source is now present.
            spans = [{'paragraph_key':finding['paragraph_key'], 'quote':finding['quote']}]
            spans += [{k:x[k] for k in ('paragraph_key','quote')} for x in finding.get('candidate_sources',[])]
            unique = {e.digest(x):deepcopy(x) for x in spans}
            for span in unique.values():
                item = {'candidate_ids':finding['candidate_ids'], 'verdict':finding['verdict'], **span}
                try:
                    # No fuzzy semantic edits or relocation to a different paragraph.
                    pk = span['paragraph_key']; paragraph = source['paragraphs'][pk]
                    if not span['quote'] or span['quote'] not in paragraph['text']:
                        raise ValueError('confirmed quote is not literal current source text')
                    existing = covered_by(span, paragraph, valid)
                    if existing:
                        pins.update(existing)
                        item.update(status='already_covered', fragment_ids=[key.split('/',1)[1] for key in existing])
                    else:
                        # Use the existing literal fragment builder; discard its synthetic
                        # owner postimage. No Evidence or owner record is created or updated.
                        working = f.apply_overlay(records, {}, generated)
                        post, fragments, _ = source_stage.binding_postimage('source-only',
                            {'statement':'原文片段補齊'}, [span],
                            {'nearby_paragraphs':[{'source_id':source_id,'paragraph_key':pk,**paragraph}]},
                            working, sources)
                        generated.update(fragments)
                        item.update(status='prepared', fragment_ids=post['source_fragment_ids'])
                        for fid in post['source_fragment_ids']:
                            key = 'source_fragments/'+fid
                            valid[key] = generated.get(key, working.get(key,{}).get('payload'))
                except (KeyError, ValueError) as exc:
                    item.update(status='unresolved', reason=str(exc)); pending.append(item)
                items.append(item)
    overlay = f.apply_overlay(records, {}, generated)
    for key,payload in generated.items():
        e.material({'evidence_step_id':key,'statement':'','source_fragment_ids':[payload['fragment_id']]}, overlay, sources)
    f.source_unchanged(sources)
    return {'version':VERSION, 'source_id':source_id, 'status':'needs_attention' if pending else 'verified',
            'items':items, 'pending':pending, 'updates':{}, 'new_fragments':generated,
            'read_keys':sorted(pins-set(generated)), 'applied':False, 'model_calls':0,
            'claim_qualification':'not_granted',
            'scope':'literal source spans only; coverage findings remain upper-layer handoff, not resolved Evidence/Claim judgements',
            'commit_reason':'Save confirmed literal coverage spans only; no Evidence, Claim or qualification changes'}


def run(store, source_id, report_path, root, *, apply=False):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root/'source-fill.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        # Reuse the ordinary immutable preparation/receipt path on resume.
        if (root/'verification.json').exists():
            frozen = json.loads((root/'before.json').read_text())
            _, provenance = read_report(report_path, source_id, frozen['sources'])
            intent = json.loads((root/'intent.json').read_text())
            if intent != {'version':VERSION, 'source':pin, 'report':provenance}:
                raise ValueError('source fill saved input differs')
            f.source_unchanged(frozen['sources'])
            if apply:
                return e.commit_verified(store, root, source_fragments_only=True)
            saved = root/'result.json'
            return json.loads((saved if saved.exists() else root/'verification.json').read_text())
        records, sources = f.load_source(store, source_id, visual_context=True)
        if pin != {'source_id':source_id, **sources[source_id]['file']}:
            raise ValueError('source changed between fill binding and census')
        report, provenance = read_report(report_path, source_id, sources)
        e.save(root/'intent.json', {'version':VERSION, 'source':pin, 'report':provenance})
        result = plan(records, sources, source_id, report)
        e.save(root/'before.json', {'sources':sources,'records':{k:records[k] for k in result['read_keys']}})
        e.save(root/'verification.json', result)
        return e.commit_verified(store, root, source_fragments_only=True) if apply else e.publish(root, result)


def fill_inventory_handoff(store,source_id,findings,previous,root,*,apply=False):
    """Compile uncovered literal inventory units; no new semantic verdict."""
    from backend.pipeline.claim_flash_evidence import inventory
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    with (root/'source-fill.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        records,sources=f.load_source(store,source_id,visual_context=True)
        units=inventory(previous,sources,source_id)
        if any(p.get('reason')!='important_unit_not_covered_by_verified_fragments'
                or p.get('layer')!='source' or p.get('unit') not in units for p in findings):
            raise ValueError('literal completion requires an exact saved source inventory unit')
        e.save(root/'intent.json',{'version':VERSION,'source':sources[source_id]['file'],
            'basis':'mechanical_inventory_span_union_gap','findings':findings,'previous':str(Path(previous).resolve())})
        if (root/'verification.json').exists():
            return e.commit_verified(store,root,source_fragments_only=True) if apply else json.loads((root/'verification.json').read_text())
        report={'source_id':source_id,'contract':coverage.CONTRACT,'findings':[{'findings':[
            {**item['unit'],'verdict':'missing_source_binding','candidate_ids':['inventory-'+e.digest(item['unit'])]}
            for item in findings]}]}
        result=plan(records,sources,source_id,report)
        result['basis']='mechanical_inventory_span_union_gap'
        e.save(root/'before.json',{'sources':sources,'records':{k:records[k] for k in result['read_keys']}})
        e.save(root/'verification.json',result)
        return e.commit_verified(store,root,source_fragments_only=True) if apply else e.publish(root,result)
