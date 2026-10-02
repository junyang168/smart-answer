"""Reviewed current-source completion of group six; no Claim/CVP text writes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint, Review, review_call
from backend.pipeline.viewpoint_evidence_source_context import load_context_source
from backend.pipeline.viewpoint_resolution_runtime import call_model, subscription_client, read_artifact, write_immutable, stable_decided_at

LINKS=['VCL-e55e33ed80528536bc05','VCL-b3d761c93b0b42b3c6c5','VCL-dd33910fa6336cb7d3e3']
PROMPT='''独立审核三个 CVP 连接的修复方案；不评价教授神学对错，不修改 Claim 或 CVP 文字。
每条 item 恰好一个 verdict，claim_id 使用给定 Claim ID。核实 after_link 的关系、全部成分和 evidence_bindings 是否由此 Claim 自己的原文支持且吻合目标 CVP。
前两条把已修复并双审通过的 Claim 原文限定完整保留为原有观点的支持理由，不把支持当成等同。第三条把「信徒应按正确词义解读」从错误 equivalent_component 改为 applies；实践方法不是具体词义选择命题本身。
applies 应表示此 Claim 把目标词义观点用于信徒解经态度；supports 应是真正论据而不是反向关系。任何不合适关系必须拒绝，不能为了完成率而批准。
problems 为空且所有条件成立才 approved=true。只读包，不执行操作。'''

def prepare(states):
    package={'package_id':'VCL-G006-COMPLETION-357-20261002-V1','viewpoint_claim_links':[]}; items=[]
    for lid in LINKS:
        old=states[('viewpoint_claim_links',lid)]; claim=states[('claims',old['claim_id'])]
        new=deepcopy(old); new['revision']+=1
        if lid==LINKS[2]:
            assert old['revision']==1 and old['link_type']=='equivalent_component'
            new['link_type']='applies'
            # The application conclusion requires its own application evidence,
            # not merely the word-sense premise; retain the complete attitude.
            part=claim['statement']
            new['component_locator']['canonical_spans']=[{'start_char':0,'end_char':len(part),'exact_text':part}]
            new['component_locator']['statement_component']=part
            eid='DK-160529097a2b-P03-E020'
            new['evidence_bindings'].append({'evidence_step_id':eid,'source_fragment_id':states[('evidence_steps',eid)]['source_fragment_ids'][0]})
            new['evidence_bindings']=sorted(new['evidence_bindings'],key=lambda b:(b['evidence_step_id'],b['source_fragment_id']))
        else:
            assert old['revision']==2 and old['link_type']=='supports' and claim['revision']==4
            end=claim['statement'].index('並非') if lid==LINKS[0] else claim['statement'].index('兩者是')
            part=claim['statement'][:end]
            new['component_locator']['canonical_spans']=[{'start_char':0,'end_char':end,'exact_text':part}]
            new['component_locator']['statement_component']=part
            eid=claim['claim_id'].replace('CL','E357R')
            step=states[('evidence_steps',eid)]
            new['evidence_bindings'].append({'evidence_step_id':eid,'source_fragment_id':step['source_fragment_ids'][0]})
            new['evidence_bindings']=sorted(new['evidence_bindings'],key=lambda b:(b['evidence_step_id'],b['source_fragment_id']))
        source=states[('source_documents',claim['occurrences'][0]['source_id'])]
        path,sha,projection=load_context_source(source)
        evidence=[]
        for b in new['evidence_bindings']:
            assert b['evidence_step_id'] in claim['evidence_step_ids']
            step=states[('evidence_steps',b['evidence_step_id'])]; fragment=states[('source_fragments',b['source_fragment_id'])]
            assert fragment['source_id']==source['source_id'] and b['source_fragment_id'] in step['source_fragment_ids']
            text=projection.spoken_rows[int(fragment['paragraph_key'][1:])-1]['text']
            assert fragment['verbatim_excerpt'] in text
            evidence.append({'step':step,'fragment':fragment,'paragraph_text':text})
        cvp=states[('canonical_viewpoints',old['viewpoint_id'])]
        items.append({'claim_id':claim['claim_id'],'claim':claim,'before_link':old,'after_link':new,
            'target_revision':states[('viewpoint_revisions',cvp['current_revision_id'])], 'evidence':evidence,
            'source_path':str(path),'source_file_sha256':sha})
        package['viewpoint_claim_links'].append(new)
    # Source hierarchy and qualifiers may be preserved, never synthesized.
    dep=[(c,o) for (c,o),p in states.items() if c in {'argument_routes','argument_route_revisions','argument_route_attestations'} and any(lid in json.dumps(p) for lid in LINKS)]
    assert not dep, dep
    return package,{'schema_version':'wkp357_current_source_link_completion_v1','package_sha256':sha256_json(package),'items':items,'claim_writes':0,'cvp_text_writes':0}

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--output-root',type=Path,required=True)
    ap.add_argument('--review',action='store_true'); ap.add_argument('--apply',action='store_true'); ap.add_argument('--backup',type=Path)
    args=ap.parse_args(); root=args.output_root.resolve(); load_dotenv(Path(__file__).resolve().parents[2]/'.env'); store=PostgresKnowledgeStore()
    if args.review:
        packet=read_artifact(root/'packet.json'); results={}
        for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
            adapter=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),prompt=PROMPT,response_model=Review,schema_name='current_source_link_completion_v1')
            raw,_,_=review_call(adapter,packet,root,label); result=Review.model_validate(raw)
            assert len(result.verdicts)==3 and {v.claim_id for v in result.verdicts}=={i['claim_id'] for i in packet['items']}
            results[label]=result.model_dump(mode='json')
        report={'packet_sha256':sha256_json(packet),'reviews':results,'approved':all(v['approved'] and not v['problems'] for r in results.values() for v in r['verdicts'])}
        write_immutable(root/'review-report.json',report); print(json.dumps(report,ensure_ascii=False)); return
    with exclusive_cvp_run_lock({'global_lock_path':str(root.parent/'registry-cvp.lock')}):
        states=snapshot(store); collections=sorted({c for c,o in states}); package,packet=prepare(states)
        write_immutable(root/'package.json',package); write_immutable(root/'packet.json',packet)
        proof={'collections':collections,'pre_fingerprint_sha256':fingerprint(states,collections)}; write_immutable(root/'before.json',proof)
        if not (root/'review-report.json').exists():
            assert not args.apply
            print('prepared; fresh relation reviews required before decision amendment and dry-run'); return
        report=read_artifact(root/'review-report.json')
        assert report['approved'] and report['packet_sha256']==sha256_json(packet)
        # Identity decisions are immutable: append fresh authority for CL008,
        # never change the original decision or CL004's identity judgement.
        old_decision=states[('viewpoint_identity_decisions',package['viewpoint_claim_links'][2]['decision_id'])]
        candidate=deepcopy(states[('viewpoint_identity_candidates',old_decision['identity_candidate_id'])])
        digest=sha256_json(packet)
        candidate.update(identity_candidate_id='VIC-'+digest[:20],proposed_action='match_existing',
            candidate_claim_ids=[packet['items'][2]['claim_id']],candidate_viewpoint_ids=[old_decision['resolved_viewpoint_id']],generation_fingerprint=digest)
        candidate_identity={'claims':candidate['candidate_claim_ids'],'viewpoints':candidate['candidate_viewpoint_ids'],
            'relations':candidate['seed_relation_ids'],'action':candidate['proposed_action'],'blockers':candidate['blocker_codes'],
            'coverage_snapshot_id':candidate['coverage_snapshot_id'],'generation_fingerprint':digest,
            'scope_manifest_sha256':candidate['scope_manifest_sha256']}
        candidate['identity_candidate_id']='VIC-'+sha256_json(candidate_identity)[:20]
        decision=deepcopy(old_decision)
        decision.update(identity_decision_id='VID-'+digest[:20],identity_candidate_id=candidate['identity_candidate_id'],
            revision=1,decision='match_existing',claim_link_decisions=[{'claim_id':packet['items'][2]['claim_id'],'link_type':'applies'}],
            input_sha256=digest,created_at=stable_decided_at(root),review_artifact_sha256=sha256_json(report),
            semantic_call_artifact_sha256s=sorted({read_artifact(root/f'raw-{label}.json')['artifact_sha256'] for label in ('gpt','opus')}))
        decision['reason']=f'Fresh dual-model relation review: {root}/review-report.json SHA {sha256_json(report)}; CL008 applies the word-sense judgement, not an equivalent proposition. CL004 identity preserved.'
        assert ('viewpoint_identity_candidates',candidate['identity_candidate_id']) not in states and ('viewpoint_identity_decisions',decision['identity_decision_id']) not in states
        package=deepcopy(package)
        package['viewpoint_claim_links'][2]['decision_id']=decision['identity_decision_id']
        package=package|{'viewpoint_identity_candidates':[candidate],'viewpoint_identity_decisions':[decision]}
        plan=store.plan_package(package,source_kind='wkp357_reviewed_current_source_link_completion')
        assert len(plan.operations)==5
        write_immutable(root/'effective-package-append-decision-v3.json',package)
        if not args.apply:
            write_immutable(root/'dry-run-plan.json',plan.as_dict()); print('dry_run_pass: three link updates; fresh immutable candidate and decision'); return
        dry=read_artifact(root/'dry-run-plan.json')
        assert dry['operations']==json.loads(json.dumps(plan.as_dict()['operations'])) and dry['source_sha256']==plan.source_sha256
        assert args.backup and args.backup.is_absolute() and args.backup.is_file()
        subprocess.run(['pg_restore','--list',str(args.backup)],check=True,capture_output=True)
        assert not subprocess.check_output(['git','status','--porcelain'],text=True).strip()
        intent={'authorization':'user: 授权继续; routine reviewed group-six CVP completion; no other Claim edits',
                'review_sha256':sha256_json(report),'backup_path':str(args.backup),'backup_sha256':file_sha256(args.backup)}
        write_immutable(root/'apply-intent.json',intent)
        receipt=store.apply_plan(plan,metadata={'reviewed_link_completion':intent},expected_registry_fingerprint_sha256=proof['pre_fingerprint_sha256'],registry_collections=collections)
        write_immutable(root/'apply-receipt.json',receipt)
        for op in plan.operations:
            assert store.get_record_state(op.collection,op.object_id)['payload']==op.payload
            states[op.collection,op.object_id]=op.payload
        assert fingerprint(snapshot(store),collections)==fingerprint(states,collections)
        for i in packet['items']: assert file_sha256(Path(i['source_path']))==i['source_file_sha256']
        write_immutable(root/'verified-result.json',{'status':'applied_readback_verified','change_set_id':plan.change_set_id,'updated_links':3,'new_identity_authority_records':2,'old_identity_decisions_unchanged':True,'all_other_records_and_sources_unchanged':True})
        print('applied_readback_verified')

if __name__=='__main__': main()
