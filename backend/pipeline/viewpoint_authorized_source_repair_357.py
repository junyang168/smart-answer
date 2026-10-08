"""Two explicitly authorized, source-bound Claim repairs; no mother/CVP edits.

Prepare and independently review first. Apply only the identical dry-run under
the global registry lock, with backup and full-registry optimistic fingerprint.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.api.canonical_repository.viewpoint_production_safety import collection_fingerprint, exclusive_cvp_run_lock, file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_artifact, write_immutable, subscription_client, stable_decided_at

SOURCE = 'notes_manuscript:2章-东方的博士'
FILE_SHA = 'e41bfde6233d770760e1f2a2b6bbc69da6395e1105dede4a6e4764d35b50f416'
SPECS = [
    ('DK-160529097a2b-P03-CL001', 'e0db6b5c3973d48110acf9880f8d2f7fd5e1a1068946ae044a27a8e75a80ca92',
     '約瑟帶領家人前往埃及是當時實際而相對安全的選擇，並非為對應預言而刻意安排。',
     'S0036', '埃及在地理上離伯利恆最近，也是相對安全的避難地點。', 'historical_background'),
    ('DK-160529097a2b-P03-CL006', '9b17de3782f054f05b9591be43379328e6d206d1eb018d713faaa6ef4ec6b34b',
     '以色列與耶穌都從埃及被召出、承擔神所賦使命；以色列是神的兒子，耶穌更是神的兒子。兩者是更滿意義的類比關係，而非預言與應驗關係。',
     'S0051', '以色列是神的兒子，耶穌更是神的兒子', 'scripture_evidence'),
]
LINK_IDS = {'VCL-4af4b6d6b7749b72be3c','VCL-e55e33ed80528536bc05','VCL-74e2adcc514ab3f6e6d2','VCL-b3d761c93b0b42b3c6c5','VCL-bb493b81cef155196330'}

def fingerprint(states, collections):
    return sha256_json({c:sorted(sha256_json(p) for (col,o),p in states.items() if col==c) for c in collections})

def snapshot(store):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute('SELECT collection,object_id,payload FROM wang_knowledge.objects WHERE retired_at IS NULL')
        return {(c,o):p for c,o,p in cur.fetchall()}

def build(states, root):
    source=states[('source_documents',SOURCE)]
    path, file_sha, projection=load_context_source(source)
    assert file_sha==FILE_SHA
    package={'package_id':'CLAIM-SOURCE-REPAIR-357-G006-20261002-V1','claims':[], 'evidence_steps':[], 'source_fragments':[], 'viewpoint_claim_links':[]}
    items=[]
    time=stable_decided_at(root)
    ids={s[0] for s in SPECS}
    links={o:p for (c,o),p in states.items() if c=='viewpoint_claim_links' and p.get('claim_id') in ids}
    assert set(links)==LINK_IDS, 'Live dependent link set drift'
    forbidden={'argument_routes','argument_route_revisions','argument_route_attestations','claim_concept_units','viewpoint_resolution_ledgers'}
    dependents=[(c,o) for (c,o),p in states.items() if c in forbidden and any(i in json.dumps(p) for i in ids|LINK_IDS)]
    assert not dependents, f'Unplanned pinned dependencies: {dependents}'
    for cid, pin, statement, key, quote, kind in SPECS:
        old=states[('claims',cid)]
        assert old['revision']==3 and semantic_record_sha(old)==pin
        new=deepcopy(old); new.update(revision=4,statement=statement,reviewed_at=time,
            reviewed_by='gpt-6.1-sol+claude-opus-5-5',
            review_note=f'Authorized source-qualifier repair; fresh independent reviews: {root}/review-report.json (bound to exact repair packet). Historical revision 3 retained.')
        eid=cid.replace('CL','E357R'); fid='FR-357-G006-'+eid
        assert ('evidence_steps',eid) not in states and ('source_fragments',fid) not in states
        text=projection.spoken_rows[int(key[1:])-1]['text']
        assert text.count(quote)==1
        step=deepcopy(states[('evidence_steps',old['evidence_step_ids'][0])])
        step.update(revision=1,evidence_step_id=eid,statement=quote,step_type=kind,
            discourse_role='補足原文限定，不移除既有論證',source_fragment_id=None,
            source_fragment_ids=[fid],produced_claim_ids=[cid],scripture_refs=old['scripture_refs'])
        template=states[('source_fragments',step_template_fragment(states,old))]
        fragment=deepcopy(template)
        fragment.update(revision=1,fragment_id=fid,paragraph_key=key,source_segment_index=int(key[1:]),
            verbatim_excerpt=quote,verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest(),
            paragraph_text_sha256=hashlib.sha256(text.encode()).hexdigest(),source_sha256=projection.body_sha256)
        new['evidence_step_ids'].append(eid)
        assert len(new['occurrences'])==1 and new['occurrences'][0]['source_id']==SOURCE
        anchor=deepcopy(new['occurrences'][0]['anchors'][0])
        anchor.update(evidence_id=eid,evidence_type=kind,paragraph_key=key,
            discourse_role=step['discourse_role'],proposed_highlight={'text':quote,'status':'proposed'})
        new['occurrences'][0]['anchors'].append(anchor)
        package['claims'].append(new); package['evidence_steps'].append(step); package['source_fragments'].append(fragment)
        revised_links=[]
        for lid,link in links.items():
            if link['claim_id']!=cid: continue
            revised=deepcopy(link); revised['revision']+=1; revised['pinned_claim_revision']=4
            if link['link_type']=='supports':
                # Preserve the existing support predicate, not expand its scope.
                parts=['約瑟帶領家人前往埃及是當時實際','的選擇'] if cid.endswith('CL001') else ['以色列與耶穌都從埃及被召出','承擔神所賦使命']
            else:
                parts=[s['exact_text'] for s in link['component_locator']['canonical_spans']]
            spans=[]
            for part in parts:
                assert statement.count(part)==1
                start=statement.index(part); spans.append({'start_char':start,'end_char':start+len(part),'exact_text':part})
            revised['component_locator']={'claim_sha256':semantic_record_sha(new),'canonical_spans':spans,'statement_component':''.join(parts)}
            package['viewpoint_claim_links'].append(revised); revised_links.append(revised)
        evidence=[]
        for existing in old['evidence_step_ids']:
            e=states[('evidence_steps',existing)]
            for f in e.get('source_fragment_ids',[]):
                fr=states[('source_fragments',f)]
                para=projection.spoken_rows[int(str(fr['paragraph_key'])[1:])-1]['text']
                assert fr['verbatim_excerpt'] in para and fr['source_id']==SOURCE
                evidence.append({'step':e,'fragment':fr,'paragraph_text':para})
        items.append({'before':old,'after':new,'new_evidence_step':step,'new_fragment':fragment,
            'source_paragraph':text,'existing_evidence':evidence,'updated_links':revised_links,
            'target_viewpoints':[{
                'identity':states[('canonical_viewpoints',v)],
                'current_revision':states[('viewpoint_revisions',states[('canonical_viewpoints',v)]['current_revision_id'])]
            } for v in sorted({l['viewpoint_id'] for l in revised_links})]})
    return package, {'schema_version':'wkp357_authorized_source_repair_packet_v1',
        'authorization':'user: 授权继续; exactly two Claim/evidence repairs, mother unchanged',
        'source_path':str(path),'source_file_sha256':file_sha,'source_body_sha256':projection.body_sha256,
        'package_sha256':sha256_json(package),'items':items,'no_other_claims_or_cvp_content_changes':True}

def step_template_fragment(states, claim):
    return states[('evidence_steps',claim['evidence_step_ids'][0])]['source_fragment_ids'][0]

def verify_applied(store, root):
    """Read-only recovery after a committed write; never replay the change set.

    Compare the normalized dry-run post-image, not input records that omit
    schema defaults such as SourceFragment.visual_facts=[].
    """
    plan=read_artifact(root/'dry-run-plan.json'); proof=read_artifact(root/'before.json')
    receipt=read_artifact(root/'apply-receipt.json')
    assert receipt['status']=='applied' and receipt['change_set_id']==plan['change_set_id']
    live=snapshot(store)
    before={(r['collection'],r['object_id']):r['payload'] for r in proof['before_records']}
    for op in plan['operations']:
        key=(op['collection'],op['object_id'])
        assert live[key]==op['payload'], f'Readback differs: {key}'
        if op['operation']=='create': del live[key]
        else: live[key]=before[key]
    assert fingerprint(live,proof['collections'])==proof['pre_fingerprint_sha256'], 'Unexpected non-target change'
    packet=read_artifact(root/'packet.json')
    assert file_sha256(Path(packet['source_path']))==FILE_SHA
    result={'status':'applied_readback_verified','change_set_id':plan['change_set_id'],'claims_repaired':2,
        'new_evidence_steps':2,'new_source_fragments':2,'updated_links':5,
        'all_other_records_and_mother_unchanged':True,'independent_reaudit':'pending',
        'readback_basis':'exact normalized dry-run operation payloads'}
    write_immutable(root/'verified-result.json',result)
    return result

class Verdict(BaseModel):
    model_config=ConfigDict(extra='forbid')
    claim_id:str
    approved:bool
    reason:str
    problems:list[str]

class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    verdicts:list[Verdict]

def review_call(adapter, packet, root, label):
    try:
        return call_model(adapter,packet,root/f'raw-{label}.json')
    except Exception as exc:
        raw=getattr(getattr(adapter,'_client',None),'last_raw_response',None)
        write_immutable(root/f'failed-review-{label}.json',{
            'packet_sha256':sha256_json(packet),'error':str(exc),
            'model_id':adapter.model_id,'raw_response':raw,'retry_started':False})
        raise

PROMPT='''独立核查两条明确授权的 Claim 来源修复。只判断是否忠于给定教授原文，不评价神学对错。
对每个 item 恰好返回一个 verdict。检查 after 完整主张能否由现有及新增逐字证据推出、是否保留相对/更是等限定、是否加入未讲内容。
新增证据必须只支持本 Claim。updated_links 必须仍正确支持或等同 target_viewpoints 中相应观点，不能把 support 当 equivalent。
链接中原先支持的局部成分保持不扩张；Claim 新限定通过自身证据补齐。母本和 CVP 文字不能改。
批准仅当上述全部成立，problems 为空。只用 packet，不执行任何代码或数据库操作。'''

def main():
    from backend.pipeline.cvp_retired_runtime import reject_legacy_execution
    reject_legacy_execution()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--review',action='store_true'); parser.add_argument('--apply',action='store_true')
    parser.add_argument('--verify-applied',action='store_true')
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args(); root=args.output_root.resolve()
    load_dotenv(Path(__file__).resolve().parents[2]/'.env'); store=PostgresKnowledgeStore()
    if args.verify_applied:
        with exclusive_cvp_run_lock({'global_lock_path':str(root.parent/'registry-cvp.lock')}):
            print(json.dumps(verify_applied(store,root)))
        return
    if args.review:
        packet=read_artifact(root/'packet.json'); reviews={}
        for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
            adapter=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),prompt=PROMPT,response_model=Review,schema_name='authorized_claim_source_repair_review_v1')
            raw,_,_=review_call(adapter,packet,root,label)
            review=Review.model_validate(raw)
            assert len(review.verdicts)==2 and {v.claim_id for v in review.verdicts}=={s[0] for s in SPECS}
            reviews[label]=review.model_dump(mode='json')
        result={'packet_sha256':sha256_json(packet),'package_sha256':packet['package_sha256'],'reviews':reviews,
            'approved':all(v['approved'] and not v['problems'] for r in reviews.values() for v in r['verdicts'])}
        write_immutable(root/'review-report.json',result); print(json.dumps(result,ensure_ascii=False)); return
    with exclusive_cvp_run_lock({'global_lock_path':str(root.parent/'registry-cvp.lock')}):
        states=snapshot(store); collections=sorted({c for c,o in states})
        package,packet=build(states,root)
        write_immutable(root/'package.json',package); write_immutable(root/'packet.json',packet)
        pre=fingerprint(states,collections)
        proof={'collections':collections,'pre_fingerprint_sha256':pre,
            'before_records':[{'collection':c,'object_id':o,'payload':p} for (c,o),p in states.items() if c=='claims' and o in {s[0] for s in SPECS} or c=='viewpoint_claim_links' and o in LINK_IDS]}
        write_immutable(root/'before.json',proof)
        if not (root/'review-report.json').exists():
            assert not args.apply
            print('prepared exact two-Claim packet; fresh reviews required before structural dry-run'); return
        review=read_artifact(root/'review-report.json')
        assert review['approved'] and review['packet_sha256']==sha256_json(packet) and review['package_sha256']==sha256_json(package)
        package=package | {'source_repair_application':{
            'schema_version':'wang_authorized_source_repair_v1','user_authorization':packet['authorization'],
            'approval_status':'not_human_approved','review_packet':packet,
            'review_artifacts':[read_artifact(root/f'raw-{label}.json') for label in ('gpt','opus')]}}
        write_immutable(root/'reviewed-package.json',package)
        plan=store.plan_package(package,source_kind='wkp357_authorized_source_repair')
        assert len(plan.operations)==11
        if not args.apply:
            write_immutable(root/'dry-run-plan.json',plan.as_dict()); print('dry_run_pass: 2 claims, 2 new steps, 2 new fragments, 5 updated links'); return
        dry=read_artifact(root/'dry-run-plan.json')
        assert dry['operations']==json.loads(json.dumps(plan.as_dict()['operations'])) and dry['source_sha256']==plan.source_sha256
        assert args.backup and args.backup.is_absolute() and args.backup.is_file()
        subprocess.run(['pg_restore','--list',str(args.backup)],check=True,capture_output=True)
        assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip(), 'Commit runner before apply'
        intent={'authorization':packet['authorization'],'review_sha256':sha256_json(review),'backup_path':str(args.backup),'backup_sha256':file_sha256(args.backup),'runner_sha256':file_sha256(Path(__file__))}
        write_immutable(root/'apply-intent.json',intent)
        receipt=store.apply_plan(plan,metadata={'authorized_source_repair':intent},expected_registry_fingerprint_sha256=pre,registry_collections=collections)
        write_immutable(root/'apply-receipt.json',receipt)
        print(json.dumps(verify_applied(store,root)))

if __name__=='__main__': main()
