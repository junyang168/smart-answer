"""Exact g034 owner-authorized prayer-component review; no Registry writes."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint
from backend.pipeline.viewpoint_owner_evidence_annotation import Verdict, validate_package, verify_applied
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, write_derived, call_model, subscription_client

BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
ANNOTATION = BASE/'g034-owner-e029-erratum-binding-repair-20261004-v1'
ROOT = BASE/'g034-owner-prayer-link-review-20261004-v1'
LINK = 'VCL-57f906fb0b01ba1062a5'
CLAIM = 'DK-791c4e404fda-P06-CL010'
EVIDENCE = 'DK-791c4e404fda-P06-E029'
PROMPT = '''独立审核负责人批准的g034祷告成分连接补齐后像。只审核before_link到after_link的单一连接变更及effective_disposition。
关系仍supports：该Claim关于门徒义主题的具体结构展开为目标观点提供支持，不声称与完整观点等价。
保留原有施舍及其它成分，把原先暂缓的“祷告”及相邻“与”纳入同一局部支持成分，新增自身E029绑定。其它连接/Claim/CVP/母本/原引文不变。
校注是负责人确认口误后的编辑校注，不是教授原话，不声称核听录音。母本只能作校注依据，不能拼接成此Claim未说过的实质论证。
本Claim只说随后讨论祷告等实践，没有断言祷告全部经节范围；审核自身讲道完整上下文是否支持该结构展开。
新连接decision_id留待本次双审生成fresh provenance，不能继承旧decision作为扩大成分的批准。此包仅内容后像审核，后续实际3ops整包必须绑定本次双审。
effective_disposition只解除本Claim祷告成分暂缓，不修改原冻结scope-run，也不代表g034完成。Claim完整成分须无跳项/重叠，原主题等价连接不动。
只返回一个verdict，evidence_step_id固定为给定E029。批准必须approved=true且problems=[]；实质问题必须拒绝，不评价教授神学对错。'''


def postimage(old, claim, evidence):
    if old['viewpoint_claim_link_id'] != LINK or old['claim_id'] != CLAIM or old['link_type'] != 'supports':
        raise ValueError('not exact authorized prayer supports connection')
    if evidence['evidence_step_id'] != EVIDENCE or evidence['support_eligibility'] != 'eligible_with_label' or not evidence.get('editorial_annotations'):
        raise ValueError('missing reviewed labeled Evidence')
    text = claim['statement']
    if text[51:53] != '禱告' or old['component_locator']['canonical_spans'] != [
        {'start_char':15,'end_char':50,'exact_text':text[15:50]},
        {'start_char':53,'end_char':57,'exact_text':text[53:57]}]:
        raise ValueError('prayer component or prior locator drift')
    new = deepcopy(old)
    new.update(revision=old['revision']+1)
    new['component_locator'] = {'claim_sha256':old['component_locator']['claim_sha256'],
        'canonical_spans':[{'start_char':15,'end_char':57,'exact_text':text[15:57]}],
        'statement_component':text[15:57]}
    new['evidence_bindings'] = sorted(old['evidence_bindings'] + [
        {'evidence_step_id':EVIDENCE,'source_fragment_id':fid} for fid in evidence['source_fragment_ids']],
        key=lambda x:(x['evidence_step_id'],x['source_fragment_id']))
    new['decision_id'] = 'PENDING-FRESH-G034-PRAYER-DUAL-REVIEW'
    ViewpointClaimLinkRecord.model_validate(new)
    return new


def run():
    project = Path(__file__).resolve().parents[2]
    if project.name != 'wkp-357-worktree-per-session': raise ValueError('wrong worktree')
    load_dotenv(project/'.env'); store = PostgresKnowledgeStore()
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
            if (ROOT/'failure.json').exists(): raise ValueError('failed review cannot retry')
            validate_package(read_artifact(ANNOTATION/'reviewed-package.json'))
            annotation_proof = verify_applied(store,ANNOTATION)
            states = snapshot(store); claim = states['claims',CLAIM]; evidence = states['evidence_steps',EVIDENCE]
            old = states['viewpoint_claim_links',LINK]; new = postimage(old,claim,evidence)
            packet = {'schema_version':'wang_g034_owner_prayer_link_review_v1',
                'owner_authorization':read_artifact(ANNOTATION/'packet.json')['owner_authorization'],
                'annotation_proof':annotation_proof,'annotation_package_sha256':file_sha256(ANNOTATION/'reviewed-package.json'),
                'before_link':old,'after_link':new,'claim':claim,
                'viewpoint':states['viewpoint_revisions',old['validated_against_viewpoint_revision_id']],
                'own_evidence':[states['evidence_steps',eid] for eid in claim['evidence_step_ids']],
                'complete_own_source':read_artifact(ANNOTATION/'packet.json')['complete_own_source'],
                'complete_mother_text':read_artifact(ANNOTATION/'packet.json')['complete_mother_text'],
                'physical_bindings':read_artifact(ANNOTATION/'packet.json')['physical_bindings'],
                'other_claim_links':[v for (c,_),v in states.items() if c=='viewpoint_claim_links' and v['claim_id']==CLAIM and v['viewpoint_claim_link_id']!=LINK],
                'effective_disposition':{'claim_id':CLAIM,'resolved_prayer_span':[51,53],
                    'support_span':[15,57],'unchanged_equivalent_span':[0,15],
                    'original_scope_run_preserved':True,'group_complete':False},
                'registry_before_sha256':fingerprint(states,sorted({c for c,_ in states}))}
            write_immutable(ROOT/'packet.json',packet)
            for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
                adapter = StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),
                    prompt=PROMPT,response_model=Verdict,schema_name='wang_g034_prayer_link_verdict_v1',compact_payload=True)
                adapter.max_request_bytes=500000
                raw_path=ROOT/f'raw-{label}.json'; start=ROOT/f'{label}-start.json'
                if start.exists() and not raw_path.exists(): raise ValueError('review started without answer; no replay')
                size=adapter.request_bytes(packet)
                if size>500000: raise ValueError('frozen capacity exceeded')
                write_immutable(start,{'packet_sha256':sha256_json(packet),'request_bytes':size,'maximum_calls':1})
                write_derived(ROOT/'progress.json',{'status':f'reviewing_{label}','group_complete':False,'registry_writes':0})
                try:
                    response,_,_=call_model(adapter,packet,raw_path)
                    verdict=Verdict.model_validate(response)
                    if verdict.evidence_step_id!=EVIDENCE or not verdict.approved or verdict.problems: raise ValueError(f'{label} rejected prayer supports postimage')
                except Exception as exc:
                    write_immutable(ROOT/'failure.json',{'error':str(exc),'role':label,'registry_writes':0}); raise
            if fingerprint(snapshot(store),sorted({c for c,_ in states}))!=packet['registry_before_sha256']: raise ValueError('Registry drift during review')
            result={'status':'prayer_link_dual_review_approved','packet_sha256':sha256_json(packet),
                'registry_writes':0,'group_complete':False,'next_action':'fresh_provenance_package_and_exact_disposition_gate'}
            write_immutable(ROOT/'review-report.json',result);write_derived(ROOT/'progress.json',result)
            print(json.dumps(result))


if __name__ == '__main__': run()
