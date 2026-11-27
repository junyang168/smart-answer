"""Synchronize duplicate Claim anchors from current, owned Evidence bindings.

Only occurrence locator metadata changes. No model, Claim prose, Evidence,
approval, source registration, CVP or Route writes are permitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from html import escape
import json
from pathlib import Path

from backend.api.canonical_repository.knowledge_models import evidence_fragment_ids
from backend.pipeline import claim_management as cm
from backend.pipeline import transcript_claim_migration as m
from backend.pipeline import transcript_evidence_migration as e
from backend.pipeline import transcript_evidence_apply as ea

VERSION = 'wang_transcript_claim_anchor_sync_v1'
LOCATOR_FIELDS = {'proposed_highlight','paragraph_key','media_time','media_end_time',
    'source_segment_index','source_fragment_id','source_sha256','paragraph_text_sha256',
    'verbatim_excerpt_sha256','locator_precision','highlight_resolution',
    'source_binding_status','locator_content_review_required','evidence_independence'}


def own_occurrence(occurrence, source):
    sid = occurrence.get('source_id')
    aliases={source['source_id'],source.get('transcript_id'),source.get('canonical_source_id')}-{None,''}
    return sid in aliases or (not sid and
        (occurrence.get('transcript_id') == source.get('transcript_id')
         or occurrence.get('lecture') == source.get('transcript_id')))


def highlight(anchor):
    h = anchor.get('proposed_highlight', {})
    return h.get('text', '') if isinstance(h, dict) else str(h or '')


def related_quote(quote, excerpt):
    a = e.literal_layout(quote)[0]; b = e.literal_layout(excerpt)[0]
    return bool(a and b and (a in b or len(b) >= 4 and b in a))


def literal_subquote(quote, excerpt):
    if quote and excerpt.count(quote) == 1:
        return quote
    a = e.literal_layout(quote)[0]; b, offsets = e.literal_layout(excerpt)
    hits = e.positions(b, a)
    return excerpt[offsets[hits[0]]:offsets[hits[0]+len(a)-1]+1] if a and len(hits) == 1 else excerpt


def current_fragment(state, source, snapshot):
    if not state or state['retired']:
        return False
    f = state['payload']
    if (f.get('source_id') != source['source_id'] or f.get('source_sha256') != snapshot['body_sha256']
            or f.get('anchor_state') != 'source_version_bound'):
        return False
    p = snapshot['paragraphs'].get(str(f.get('paragraph_key')), {})
    q = f.get('verbatim_excerpt', '')
    return bool(q and q in p.get('text', '') and '~~' not in q
        and not ea.context_only(q)
        and f.get('paragraph_text_sha256') == m.sha(p['text'].encode())
        and f.get('verbatim_excerpt_sha256') == m.sha(q.encode())
        and f.get('media_time') == p.get('start_time')
        and f.get('media_end_time') == p.get('end_time'))


def select_fragments(anchor, evidence, records, prior, source, snapshot):
    ids = evidence_fragment_ids(evidence); quote = highlight(anchor)
    valid = {fid:records['source_fragments/'+fid] for fid in ids
             if current_fragment(records.get('source_fragments/'+fid),source,snapshot)}
    pinned=anchor.get('source_fragment_id')
    if (pinned in valid and e.literal_layout(quote)[0]
            and e.literal_layout(quote)[0] in e.literal_layout(valid[pinned]['payload']['verbatim_excerpt'])[0]):
        return [pinned], 'exact_current_evidence', []
    exact = [fid for fid,s in valid.items()
             if e.literal_layout(quote)[0] and e.literal_layout(quote)[0]
             in e.literal_layout(s['payload']['verbatim_excerpt'])[0]]
    if exact:
        # An existing correct paragraph selects its known occurrence. Otherwise
        # retain each explicit owned occurrence; never choose an arbitrary first.
        located = [fid for fid in exact if valid[fid]['payload']['paragraph_key'] == anchor.get('paragraph_key')
                   and valid[fid]['payload']['media_time'] == anchor.get('media_time')]
        return located or exact, 'exact_current_evidence', []
    ek = 'evidence_steps/'+str(evidence['evidence_step_id'])
    old_ev = prior.get(ek,{}).get('payload',{})
    historical = [fid for fid in evidence_fragment_ids(old_ev)
                  if related_quote(quote,prior.get('source_fragments/'+fid,{}).get('payload',{}).get('verbatim_excerpt',''))]
    descendants = set(); inactive = []
    for fid in historical:
        state = records.get('source_fragments/'+fid,{})
        f = state.get('payload',{})
        descendants.update(f.get('replacement_fragment_ids') or [fid])
        if f.get('anchor_state') in {'source_deleted','context_only'}:
            inactive.append(f.get('anchor_state'))
    chosen = [fid for fid in ids if fid in descendants and fid in valid]
    if chosen:
        return chosen, 'historical_fragment_handoff', inactive
    if historical and inactive:
        return [], 'source_deleted' if 'source_deleted' in inactive else 'context_only', inactive
    if historical:
        return [], 'historical_fragment_pending_location', inactive
    states = {records.get('source_fragments/'+fid,{}).get('payload',{}).get('anchor_state') for fid in ids}
    return [], ('source_deleted' if 'source_deleted' in states or evidence.get('source_binding_status') == 'source_deleted'
                else 'context_only' if evidence.get('source_binding_status') == 'context_only'
                else 'no_current_owned_source'), list(states-{None})


def projected_anchor(anchor, fragment, evidence, snapshot, *, impact_preserved=False):
    out = deepcopy(anchor); p = snapshot['paragraphs'][fragment['paragraph_key']]
    q = literal_subquote(highlight(anchor),fragment['verbatim_excerpt'])
    # Keep a source fragment's full literal slice when a short highlight is
    # repeated. A paragraph-bound short answer is explicitly disclosed; no word
    # timestamp or unique-character locator is invented.
    if p['text'].count(q) > 1 and p['text'].count(fragment['verbatim_excerpt']) == 1:
        q = fragment['verbatim_excerpt']
    oldq = highlight(anchor)
    h = deepcopy(anchor.get('proposed_highlight',{}))
    if not isinstance(h,dict): h = {'status':'proposed'}
    h['text'] = q
    if oldq != q: h['status'] = 'proposed'
    out.update(proposed_highlight=h,paragraph_key=fragment['paragraph_key'],
        media_time=p.get('start_time'),media_end_time=p.get('end_time'),
        source_segment_index=p.get('index'),source_fragment_id=fragment['fragment_id'],
        source_sha256=snapshot['body_sha256'],paragraph_text_sha256=m.sha(p['text'].encode()),
        verbatim_excerpt_sha256=m.sha(q.encode()),locator_precision='paragraph',
        highlight_resolution='unique' if p['text'].count(q)==1 else 'repeated_within_paragraph',
        source_binding_status='source_version_bound',
        locator_content_review_required=bool(anchor.get('locator_content_review_required') or (e.script_layout(oldq)[0] != e.script_layout(q)[0] and not impact_preserved) or evidence.get('support_eligibility')=='withheld_unreviewed'
                                             or evidence.get('source_binding_status') in {'needs_claim_refresh','partially_deleted'}))
    return out


def group_overlaps(anchors, snapshot):
    """Repeated physical source counts once, without deciding Claim meaning."""
    spans=[]
    for anchor in anchors:
        p=snapshot['paragraphs'][anchor['paragraph_key']]['text'];q=highlight(anchor)
        # No arbitrary character position for a repeated short answer.
        if p.count(q)!=1: continue
        spans.append((anchor['paragraph_key'],p.index(q),p.index(q)+len(q),anchor))
    remaining=set(range(len(spans)))
    while remaining:
        indexes={min(remaining)};queue=list(indexes)
        while queue:
            i=queue.pop();p,l,h,_=spans[i]
            for j in remaining-indexes:
                p2,l2,h2,_=spans[j]
                if p==p2 and l<h2 and l2<h:indexes.add(j);queue.append(j)
        remaining-=indexes
        if len(indexes)<2:continue
        group='TAS-'+cm.sha256_json([snapshot['body_sha256'],[(spans[i][0],spans[i][1],spans[i][2]) for i in sorted(indexes)]])[:24]
        for position,i in enumerate(sorted(indexes)):
            spans[i][3]['evidence_independence']={'basis_group_id':group,'count_once_per_basis_group':True,
                                                  'is_basis_representative':position==0}
    cm.source.validate_basis_groups(spans)


def compile_projection(records, prior, *, source_id, snapshot, preserved_evidence=()):
    source=records['source_documents/'+source_id]['payload'];updates={};rows=[];skipped=[]
    source_order = {key: i for i, key in enumerate(snapshot['paragraphs'])}
    for key,state in sorted(records.items()):
        if not key.startswith('claims/') or state['retired']:continue
        before=state['payload']
        if not any(own_occurrence(o,source) for o in before.get('occurrences',[])):continue
        if before.get('review_status')=='superseded':skipped.append(key);continue
        after=deepcopy(before)
        for oi,occurrence in enumerate(after.get('occurrences',[])):
            if not own_occurrence(occurrence,source):continue
            result=[];notes=deepcopy(occurrence.get('anchor_migration_notes',[]))
            for ai,anchor in enumerate(occurrence.get('anchors',[])):
                eid=anchor.get('evidence_id');evstate=records.get('evidence_steps/'+str(eid))
                if eid not in before.get('evidence_step_ids',[]) or not evstate or evstate['retired']:
                    raise ValueError('Claim anchor Evidence ownership is missing: '+key)
                evidence=evstate['payload']
                if ea.context_only(highlight(anchor)):
                    ids,reason,inactive=[],'context_only',[]
                else:
                    ids,reason,inactive=select_fragments(anchor,evidence,records,prior,source,snapshot)
                made=[projected_anchor(anchor,records['source_fragments/'+fid]['payload'],evidence,snapshot,
                    impact_preserved='evidence_steps/'+str(eid) in preserved_evidence) for fid in ids]
                made.sort(key=lambda anchor: source_order[anchor['paragraph_key']])
                result.extend(made)
                if not made:
                    note={'old_anchor_sha256':cm.sha256_json(anchor),'evidence_id':eid,
                          'reason':reason,'source_sha256':snapshot['body_sha256']}
                    if note not in notes:notes.append(note)
                rows.append({'claim_key':key,'occurrence':oi,'anchor':ai,'evidence_id':eid,
                             'reason':reason,'before':anchor,'after':made,'inactive_source_states':inactive})
            # Equal source quotations with identical annotation are one locator.
            seen=set();unique=[]
            for anchor in result:
                token=cm.sha256_json(anchor)
                if token not in seen:unique.append(anchor);seen.add(token)
            unique.sort(key=lambda anchor: source_order[anchor['paragraph_key']])
            group_overlaps(unique,snapshot)
            occurrence['anchors']=unique
            if notes:occurrence['anchor_migration_notes']=notes
        if after!=before:updates[key]=after
    report={'schema_version':VERSION,'source_id':source_id,'updates':updates,'anchors':rows,
            'summary':dict(Counter(r['reason'] for r in rows)), 'skipped_superseded_claims':skipped,
            'model_calls':0,'claim_prose_changed':False,'new_qualification_granted':False}
    validate_projection(records,report,snapshot)
    return report


def validate_projection(records, report, snapshot):
    source=records['source_documents/'+report['source_id']]['payload'];source_id=source['source_id']
    for row in report['anchors']:
        for anchor in row['after']:
            if ({k:v for k,v in row['before'].items() if k not in LOCATOR_FIELDS}
                    != {k:v for k,v in anchor.items() if k not in LOCATOR_FIELDS}):
                raise ValueError('anchor synchronization changed speaker, assertion or discourse annotation')
            if highlight(row['before'])!=highlight(anchor) and anchor['proposed_highlight'].get('status')!='proposed':
                raise ValueError('changed quotation cannot acquire an approval')
    for key,after in report['updates'].items():
        before=records[key]['payload']
        if ({k:v for k,v in before.items() if k!='occurrences'} != {k:v for k,v in after.items() if k!='occurrences'}
                or len(before['occurrences'])!=len(after['occurrences'])):
            raise ValueError('anchor synchronization changed Claim content, authority or topology')
        for old,occurrence in zip(before['occurrences'],after['occurrences']):
            if not own_occurrence(old,source):
                if old!=occurrence:raise ValueError('foreign occurrence changed')
                continue
            if ({k:v for k,v in old.items() if k not in {'anchors','anchor_migration_notes'}}
                    != {k:v for k,v in occurrence.items() if k not in {'anchors','anchor_migration_notes'}}):
                raise ValueError('occurrence content changed')
            for anchor in occurrence['anchors']:
                eid=anchor.get('evidence_id');fstate=records.get('source_fragments/'+str(anchor.get('source_fragment_id')))
                ev=records.get('evidence_steps/'+str(eid),{}).get('payload',{})
                if (eid not in after['evidence_step_ids'] or not current_fragment(fstate,source,snapshot)
                        or anchor['source_fragment_id'] not in evidence_fragment_ids(ev)):
                    raise ValueError('new anchor lacks exact current owned Evidence fragment chain')
                f=fstate['payload'];p=snapshot['paragraphs'][f['paragraph_key']];q=highlight(anchor)
                if (not q or q not in f['verbatim_excerpt'] or q not in p['text'] or '~~' in q or ea.context_only(q)
                        or anchor['source_sha256']!=snapshot['body_sha256'] or anchor['paragraph_key']!=f['paragraph_key']
                        or anchor['media_time']!=p.get('start_time') or anchor['media_end_time']!=p.get('end_time')
                        or anchor['paragraph_text_sha256']!=m.sha(p['text'].encode())
                        or anchor['verbatim_excerpt_sha256']!=m.sha(q.encode())):
                    raise ValueError('anchor literal source or paragraph clock drift')


def package_for(report, authority=None):
    result={'package_id':'claim-anchor-sync-'+cm.sha256_json(report)[:24],
            'claims':[v for _,v in sorted(report['updates'].items())]}
    if authority is not None:result['source_repair_application']=authority
    return result


def validate_package(package):
    """Store authority: recompute exact metadata copies, never borrow AI review."""
    app=package.get('source_repair_application') or {}
    fields={'schema_version','before_records','prior_records','report','owner_direction','source_files'}
    if set(app) not in (fields, fields|{'impact_evidence_plan'}) or app['schema_version']!=VERSION:
        raise ValueError('anchor sync authority fields differ')
    for bound in [app['before_records'],app['prior_records'],app['report'],app['owner_direction'],*app['source_files']]:
        cm.source.verify_file(bound)
    records=m.read(app['before_records']['path']);prior=m.read(app['prior_records']['path'])
    report=m.read(app['report']['path']);direction=m.read(app['owner_direction']['path'])
    if (direction.get('authorized_by')!='owner' or not direction.get('instruction')
            or direction.get('new_qualification_granted') is not False):
        raise ValueError('owner locator scope is missing; no Claim approval can be inferred')
    source=records['source_documents/'+report['source_id']]['payload'];snapshot=ea.snap(source['source_path'])
    from backend.api.canonical_repository.postgres_store import record_content_sha
    if any(record_content_sha(s['payload'])!=s['content_sha256'] for s in records.values()):
        raise ValueError('frozen anchor preimage hash differs')
    if (m.binding(Path(source['source_path'])) not in app['source_files']
            or snapshot['body_sha256']!=source['source_body_sha256']):
        raise ValueError('anchor sync source identity differs')
    preserved=[]
    if app.get('impact_evidence_plan'):
        cm.source.verify_file(app['impact_evidence_plan'])
        ep=m.read(app['impact_evidence_plan']['path'])
        if ep['records']!=app['prior_records'] or ep['source_id']!=report['source_id']:
            raise ValueError('impact handoff differs from original Evidence inputs')
        cm.source.verify_file(ep['report'])
        er=m.read(ep['report']['path'])
        from backend.pipeline.transcript_migration_impact import verified_preserved
        preserved=verified_preserved(er,prior,snapshot)
        for key in preserved:
            wanted={**er['updates'][key], 'revision':prior[key]['revision']+1}
            if records.get(key,{}).get('payload')!=wanted:
                raise ValueError('semantic decision differs from committed Evidence')
    compiled=compile_projection(records,prior,source_id=report['source_id'],snapshot=snapshot,
                                preserved_evidence=preserved)
    if compiled!=report or package!=package_for(compiled,app):
        raise ValueError('anchor package differs from exact deterministic source projection')


def validate_apply_plan(plan,metadata,pins):
    """A store caller must carry the same complete read set and sealed package."""
    bound=(metadata or {}).get('migration_plan')
    if not bound or not pins:raise ValueError('anchor synchronization requires its exact plan and read pins')
    cm.source.verify_file(bound);p=m.read(bound['path'])
    if (p['records']!=p['package']['source_repair_application']['before_records']
            or p['report']!=p['package']['source_repair_application']['report']):
        raise ValueError('anchor execution uses a different preimage or comparison')
    records=m.read(p['records']['path']);report=m.read(p['report']['path'])
    expected={cm.source.split_key(k):(s['revision'],s['content_sha256'],s['retired']) for k,s in records.items()}
    if pins!=expected or plan.fingerprint_sha256!=p['fingerprint_sha256'] or plan.source_sha256!=cm.sha256_json(p['package']):
        raise ValueError('anchor apply plan or read set differs from its frozen authority')
    validate_package(p['package']);validate_plan(plan,report,records)


def validate_plan(plan,report,records):
    if ({op.collection+'/'+op.object_id for op in plan.operations}!=set(report['updates']) or plan.review_events):
        raise ValueError('anchor migration has extra writes or review events')
    for op in plan.operations:
        key=op.collection+'/'+op.object_id
        if op.collection!='claims' or op.operation!='update' or op.before_sha256!=records[key]['content_sha256']:
            raise ValueError('invalid anchor migration operation')
        if ({k:v for k,v in op.payload.items() if k!='revision'}
                !={k:v for k,v in report['updates'][key].items() if k!='revision'}):
            raise ValueError('store changed planned anchor postimage')


def render(report, applied=False):
    title='Claim 原文定位已同步' if applied else 'Claim 原文定位同步對照表（寫入前）'
    rows=[]
    for row in report['anchors']:
        old=row['before'];new=row['after']
        values=[row['claim_key'],row['evidence_id'],row['reason'],
                f"{old.get('paragraph_key')} / {old.get('media_time')}",highlight(old),
                '\n'.join(f"{a['paragraph_key']} / {a['media_time']}–{a['media_end_time']}" for a in new),
                '\n'.join(highlight(a) for a in new) or '移出有效定位；原文只留在交易歷史']
        rows.append('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in values)+'</tr>')
    return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;margin:2rem}td,th{border:1px solid #ccd3df;padding:.6rem;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}td:nth-child(5),td:nth-child(7){min-width:20rem}thead{position:sticky;top:0;background:#eef3f9}</style><h1>'+title+'</h1><p>只同步 Claim 重複保存的引文與段落時間。Claim 論點、Evidence、審核與 CVP／Route 不改；未授予新的內容資格。已刪除和純語境的引文移出有效定位，歷史保留。時間為段落邊界。</p><p>'+escape(json.dumps(report['summary'],ensure_ascii=False))+'</p><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in ['Claim','Evidence','處理','舊定位','舊引文','新定位','新引文'])+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></html>'


def prepare(store,root,*,source_id,evidence_run,published,instruction):
    root=Path(root).resolve();evidence_run=Path(evidence_run).resolve()
    intent={'source_id':source_id,'evidence_run':str(evidence_run),'published':str(Path(published).resolve()),'instruction':instruction}
    if (root/'plan.json').exists():
        saved=m.read(root/'plan.json')
        if saved['intent']!=intent:raise ValueError('saved anchor intent differs')
        return saved
    if not instruction.strip():raise ValueError('owner direction required')
    er=m.read(evidence_run/'plan.json');history=store.get_change_history(er['change_set_id'])
    if er['source_id']!=source_id:raise ValueError('Evidence handoff source differs')
    if not history or history['status']!='applied':
        cm.source.verify_file(er['report'])
        if m.read(er['report']['path'])['updates']:raise ValueError('missing applied Evidence handoff')
        ea.verify(store,evidence_run)  # A fresh, exact no-op has no transaction history.
    cm.source.verify_file(er['records']);prior=m.read(er['records']['path'])
    records={cm.source.key(*k):cm.model_state(v) for k,v in store.read_affected_context([('source_documents',source_id)]).items()}
    source=records['source_documents/'+source_id]['payload'];current=m.binding(Path(source['source_path']));pub=m.binding(Path(published))
    if current['file_sha256']!=source['source_file_sha256']:raise ValueError('registered source file drift')
    snapshot=ea.snap(source['source_path'])
    if snapshot['body_sha256']!=source['source_body_sha256'] or {**snapshot,'file':pub}!=ea.snap(published):
        raise ValueError('source does not match published body, row identities and clocks')
    cm.source.verify_file(er['report'])
    preserved=m.read(er['report']['path']).get('preserved_eligibility', [])
    report=compile_projection(records,prior,source_id=source_id,snapshot=snapshot,preserved_evidence=preserved)
    rb=m.immutable(root/'records-before.json',records);comparison=m.immutable(root/'comparison.json',report)
    direction=m.immutable(root/'owner-direction.json',{'authorized_by':'owner','instruction':instruction,'new_qualification_granted':False})
    authority={'schema_version':VERSION,'before_records':rb,'prior_records':er['records'],'report':comparison,
               'owner_direction':direction,'source_files':[current,pub]}
    if preserved:
        authority['impact_evidence_plan']=m.binding(evidence_run/'plan.json')
    package=package_for(report,authority)
    plan=store.plan_package(package,source_kind=VERSION,transactional=True);validate_plan(plan,report,records)
    value={'schema_version':VERSION,'intent':intent,'records':rb,'report':comparison,'source_files':[current,pub,er['records'],m.binding(evidence_run/'plan.json')],
           'implementation':m.binding(Path(__file__)),'package':package,'change_set_id':plan.change_set_id,'fingerprint_sha256':plan.fingerprint_sha256}
    for b in value['source_files']:cm.source.verify_file(b)
    m.immutable(root/'plan.json',value);m._write_bytes(root/'comparison.html',render(report).encode())
    return value


def apply(store,root):
    root=Path(root);p=m.read(root/'plan.json')
    for b in [p['implementation'],p['records'],p['report'],*p['source_files']]:cm.source.verify_file(b)
    h=store.get_change_history(p['change_set_id'])
    if h and h['status']=='applied':verify(store,root);return {'status':'already_applied','change_set_id':p['change_set_id']}
    records=m.read(p['records']['path']);report=m.read(p['report']['path']);sid=p['intent']['source_id']
    observed={cm.source.key(*k):cm.model_state(v) for k,v in store.read_affected_context([('source_documents',sid)]).items()}
    if records!=observed:raise ValueError('records changed since anchor comparison; recapture without overwriting prior plan')
    source=records['source_documents/'+sid]['payload'];validate_projection(records,report,ea.snap(source['source_path']))
    validate_package(p['package'])
    plan=store.plan_package(p['package'],source_kind=VERSION,transactional=True);validate_plan(plan,report,records)
    if plan.fingerprint_sha256!=p['fingerprint_sha256']:raise ValueError('anchor transaction changed')
    if not plan.operations:
        result={'status':'already_synchronized','change_set_id':None,'model_calls':0}
        m.immutable(root/'apply.json',result);verify(store,root);return result
    pins={cm.source.split_key(k):(v['revision'],v['content_sha256'],v['retired']) for k,v in records.items()}
    result=store.apply_plan(plan,metadata={'actor':'owner_authorized_locator_sync','reason':p['intent']['instruction'],
        'migration_plan':m.binding(root/'plan.json'),'claim_prose_changed':False,'new_qualification_granted':False,'model_calls':0},expected_current_record_pins=pins)
    m.immutable(root/'apply.json',result);verify(store,root);return result


def verify(store,root):
    root=Path(root);p=m.read(root/'plan.json');records=m.read(p['records']['path']);report=m.read(p['report']['path'])
    for bound in [p['records'],p['report'],*p['source_files']]:cm.source.verify_file(bound)
    h=store.get_change_history(p['change_set_id'])
    if report['updates'] and (not h or h['status']!='applied'):raise ValueError('anchor synchronization lacks committed history')
    current=store.read_current_records([cm.source.split_key(k) for k in records]);after={cm.source.key(*k):cm.model_state(v) for k,v in current.items()}
    for key,before in records.items():
        if key in report['updates']:
            if after.get(key,{}).get('payload')!={**report['updates'][key],'revision':before['revision']+1}:
                raise ValueError('anchor postimage readback differs: '+key)
        elif after.get(key)!=before:raise ValueError('non-target record changed: '+key)
    source=after['source_documents/'+p['intent']['source_id']]['payload'];snapshot=ea.snap(source['source_path'])
    validate_projection(after,{**report,'updates':{key:after[key]['payload'] for key in report['updates']}},snapshot)
    for b in p['source_files']:cm.source.verify_file(b)
    result={'status':'verified','change_set_id':p['change_set_id'],'updated_claims':len(report['updates']),
            'summary':report['summary'],'skipped_superseded_claims':len(report['skipped_superseded_claims']),
            'model_calls':0,'claim_prose_changed':False,'new_qualification_granted':False}
    m.immutable(root/'verification.json',result);m._write_bytes(root/'result.html',render(report,True).encode());return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['prepare','apply','verify'])
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--source-id');parser.add_argument('--evidence-run',type=Path)
    parser.add_argument('--published',type=Path);parser.add_argument('--instruction-file',type=Path);args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store=PostgresKnowledgeStore()
    if args.command=='prepare':
        if not all([args.source_id,args.evidence_run,args.published,args.instruction_file]):parser.error('prepare requires source, evidence run, publication and direction')
        p=prepare(store,args.root,source_id=args.source_id,evidence_run=args.evidence_run,published=args.published,instruction=args.instruction_file.read_text())
        r=m.read(p['report']['path']);print(json.dumps({'summary':r['summary'],'updated_claims':len(r['updates'])},ensure_ascii=False))
    elif args.command=='apply':
        plan=m.read(args.root/'plan.json');report=m.read(plan['report']['path'])
        with ea.locks(plan['intent']['source_id'],record_keys=report['updates']):result=apply(store,args.root)
        print(json.dumps(result,ensure_ascii=False))
    else:print(json.dumps(verify(store,args.root),ensure_ascii=False))


if __name__=='__main__':main()
