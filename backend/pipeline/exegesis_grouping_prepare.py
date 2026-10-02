"""Read-only #411 preparation; never promotes citation keys to primary owners."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from backend.pipeline import exegesis_passage_location_runner as loc
from backend.pipeline.exegesis_intelligent_grouping_job import verify_current, exact, unit_schema, FORMAL_LEDGER, FORMAL_PACKET
from backend.pipeline.viewpoint_passage_grouping_preflight import passage_sort_key


def reconcile(ledger, packet, reviewed):
    if ledger['artifact_sha256'] != FORMAL_LEDGER or packet['artifact_sha256'] != FORMAL_PACKET or ledger['packet_sha256'] != packet['artifact_sha256']:
        raise ValueError('formal input SHA/binding drift')
    claims = {c['claim_id']: c for c in packet['claims']}
    selected = [d for d in ledger['decisions'] if d['role'] == 'passage_exegesis']
    expected = {d['claim_id'] for d in selected}
    if len(selected) != 3843 or len(expected) != 3843:
        raise ValueError('exegesis denominator drift')
    exception_ids = {d['claim_id'] for d in selected if d['passage_identity_status'] in {'disputed', 'pending_context_reference_verification'}}
    exact([r['claim']['claim_id'] for r in reviewed['rows']], exception_ids, '624 exception scope')
    index = {r['claim']['claim_id']: r for r in reviewed['rows']}
    rows = []
    for decision in selected:
        cid = decision['claim_id']; claim = claims[cid]; audit = index.get(cid)
        if audit and any(audit['claim'].get(k) != v for k,v in claim.items()):
            raise ValueError('review/frozen Claim graph drift')
        if audit and audit['status'] == 'source_verified_independent_agreement':
            adopted = audit['arbitration'] or audit['original_primary_review']
            if adopted['primary'] != audit['primary'] or audit['independent_review']['primary'] != audit['primary']:
                raise ValueError('actual reviewed primary differs')
            passage_sort_key(audit['primary'])
            status = 'primary_confirmed_passage_membership_not_reviewed'
        elif audit:
            if audit['status'] != 'unresolved' or not audit['missing']:
                raise ValueError('unresolved exception lacks explicit evidence gap')
            status = 'unresolved_primary'
        else:
            status = 'existing_keys_require_primary_and_membership_review'
        rows.append(dict(claim=claim, role_decision=decision, preparation_status=status,
            primary=audit['primary'] if audit and status.startswith('primary_confirmed') else '',
            passage_location_review=audit, secondary_relations=audit['secondary_relations'] if audit else None,
            original_claim_and_evidence_references_preserved=True))
    exact([r['claim']['claim_id'] for r in rows], expected, 'preparation scope')
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('ledger','packet','location-input','location-ledger','deferred','output-root'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    root=args.output_root.resolve();root.mkdir(parents=True,exist_ok=False)
    ledger,packet,reviewed,deferred=[loc.checked(x) for x in (args.ledger,args.packet,args.location_ledger,args.deferred)]
    location=loc.checked(args.location_input)
    if location['artifact_sha256'] != reviewed['input_sha256']:
        raise ValueError('location review/input binding drift')
    rows=reconcile(ledger,packet,reviewed)
    claims=[r['claim'] for r in rows]
    verify_current(claims)
    spec=importlib.util.spec_from_file_location('preparation_source_reader',Path('scripts/claim-role-source-context-audit.py'))
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    ids=sorted({c['source_id'] for c in claims});metadata=audit.source_payloads(ids)
    previous={s['source_id']:s for b in location['batches'] for s in b.get('sources',[b.get('source')])}
    sources=[];position_flags=[]
    by_source=defaultdict(list)
    for c in claims:by_source[c['source_id']].append(c)
    for sid in ids:
        first=by_source[sid][0]
        path,raw,paragraphs,file_sha,body_sha=audit.matching_source(metadata[sid],first['source_file_sha256'])
        for c in by_source[sid]:
            if c['source_file_sha256']!=first['source_file_sha256'] or c['source_content_sha256']!=first['source_content_sha256']:
                raise ValueError('multiple frozen source versions in one preparation scope')
        source=dict(source_id=sid,path=str(path),file_sha256=file_sha,source_revision=first['source_revision'],
            source_content_sha256=first['source_content_sha256'],frozen_file_sha256=first['source_file_sha256'],
            source_body_sha256=body_sha,match='exact_file' if file_sha==first['source_file_sha256'] else 'exact_body_alternate_file',
            paragraphs=[dict(paragraph_key=f'S{i+1:04d}',text=t) for i,t in enumerate(paragraphs)],linked_files=[])
        for paragraph in previous.get(sid,{}).get('paragraphs',[]):
            if 'visual_source_path' in paragraph:
                visual=Path(paragraph['visual_source_path'])
                sha=hashlib.sha256(visual.read_bytes()).hexdigest()
                if sha!=paragraph['visual_file_sha256']:raise ValueError('visual source drift')
                item=dict(path=str(visual),file_sha256=sha)
                if item not in source['linked_files']:source['linked_files'].append(item)
        for c in by_source[sid]:
            for step in c['evidence_steps']:
                for f in step['fragments']:
                    quote=f['verbatim_excerpt'];matches=[i+1 for i,t in enumerate(paragraphs) if quote and quote in t]
                    visuals=[v for v in source['linked_files'] if Path(v['path']).read_text()==quote]
                    if not matches and not visuals:
                        raise ValueError('frozen fragment absent from physical source: '+f['fragment_id'])
                    key=str(f.get('paragraph_key') or '')
                    index=int(key[1:]) if re.fullmatch(r'S\d{4}',key) else None
                    if matches and index not in matches:
                        position_flags.append(dict(claim_id=c['claim_id'],fragment_id=f['fragment_id'],
                            frozen_paragraph_key=key,physical_matching_paragraph_indices=matches,
                            status='position_mapping_requires_review_not_rewritten'))
        sources.append(source)
    source_index={s['source_id']:s for s in sources}
    counts=dict(Counter(r['preparation_status'] for r in rows))
    bindings=dict(role_ledger_sha256=ledger['artifact_sha256'],role_packet_sha256=packet['artifact_sha256'],
        location_ledger_sha256=reviewed['artifact_sha256'],deferred_sha256=deferred['artifact_sha256'])
    loc.seal(root/'scope-and-ownership-preparation.json',dict(schema_version='wang_exegesis_grouping_preparation_v1',
        status='prepared_not_grouping_authorization',**bindings,counts=counts,rows=rows,total_exegesis=3843,
        other_excluded=8743,deferred_excluded=170,master_data_mutations=0,model_calls_executed=0))
    loc.seal(root/'sources.json',dict(**bindings,sources=sources))
    loc.seal(root/'unresolved-primary.json',dict(**bindings,rows=[r for r in rows if r['preparation_status']=='unresolved_primary'],
        not_deferred=True,not_silently_excluded=True))
    tasks=[]
    for sid in ids:
        members=[r for r in rows if r['claim']['source_id']==sid and r['preparation_status']!='unresolved_primary']
        if members:tasks.append(dict(task_key=sid,claim_ids=[r['claim']['claim_id'] for r in members],
            source_id=sid,source_file_sha256=source_index[sid]['file_sha256'],
            purpose='source-local primary/paragraph-boundary review; transport task, not a semantic group',
            cross_source_membership_review_required=True))
    loc.seal(root/'source-review-task-plan.json',dict(**bindings,tasks=tasks,status='plan_only_not_executed',
        position_review_flags=position_flags,scope_complete_sources=True))
    books=defaultdict(list)
    for row in rows:
        if row['preparation_status']=='unresolved_primary':continue
        keys=[row['primary']] if row['primary'] else row['role_decision'].get('interpreted_passage_keys',[])
        for book in {key.split('.')[0] for key in keys}:books[book].append(row)
    capacities=[]
    prompt=Path('backend/pipeline/prompts/exegesis_passage_unit_planning.md').read_text()
    schema=json.dumps(unit_schema(),ensure_ascii=False,sort_keys=True)
    for book,members in sorted(books.items()):
        needed={r['claim']['source_id'] for r in members}
        payload=dict(scope_label=book,claims=members,sources=[dict(source_index[sid],original_text=Path(source_index[sid]['path']).read_text(),
            linked_files=[dict(f,original_text=Path(f['path']).read_text()) for f in source_index[sid]['linked_files']]) for sid in sorted(needed)])
        wire='Perform structured extraction without tools or file changes. Return only JSON.\n'+prompt+'\n===== USER INPUT =====\n'+json.dumps(payload,ensure_ascii=False,indent=2)
        lower=len(wire.encode())+len(schema.encode())
        capacities.append(dict(book=book,provisional_claim_count=len(members),request_byte_lower_bound=lower,
            limit=500000,already_over_limit=lower>500000,not_authoritative_primary_membership=True))
    loc.seal(root/'request-capacity-report.json',dict(**bindings,books=capacities,
        measurement='complete prompt + schema + payload lower bound, excludes CLI arguments; no truncation',
        production_ready=False,reason='source-local passage nominations and cross-source membership review required before final unit freezing'))
    loc.seal(root/'validation-report.json',dict(**bindings,current_claim_graph_verified=3843,physical_sources_verified=len(sources),
        counts=counts,missing=0,duplicate=0,foreign=0,source_match_counts=dict(Counter(s['match'] for s in sources)),
        fragment_presence_verified=True,position_mapping_review_flags=len(position_flags),
        deferred_not_processed=True,other_not_processed=True,grouping_started=False,cvp_generated=0,database_mutations=0))
    print(json.dumps(dict(output=str(root),counts=counts,sources=len(sources),position_review_flags=len(position_flags),
        books_over_capacity=sum(b['already_over_limit'] for b in capacities)),ensure_ascii=False))


if __name__=='__main__':main()
