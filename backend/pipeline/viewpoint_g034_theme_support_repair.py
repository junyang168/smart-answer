"""One explicitly authorized g034 theme equivalent-component to supports change.

Preserve the original component locator and all evidence. Adjacent supports
links for different components are not merged or retired. This scope does not
authorize rewriting any Claim, Evidence or CVP.
"""
import argparse
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import re
import subprocess

from dotenv import load_dotenv
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore, stored_operation_payload
from backend.api.canonical_repository.knowledge_models import ViewpointClaimLinkRecord
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import exclusive_cvp_run_lock, file_sha256
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot, fingerprint
from backend.pipeline.viewpoint_owner_evidence_annotation import Verdict
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, write_derived, call_model, subscription_client

BASE=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
PRAYER=BASE/'g034-owner-prayer-link-review-20261004-v1'
ROOT=BASE/'g034-owner-theme-support-repair-20261004-v1'
STORE_CONFLICT_ROOT=ROOT
RECOVERY_ROOT=BASE/'g034-owner-theme-component-store-repair-20261004-v2'
LINK='VCL-db5e440b8856ca2f4ad6'
CLAIM='DK-791c4e404fda-P06-CL010'
CV='CV-3657f53d08ed20c39b95'
PROMPT='''独立审核负责人“同意改为支持”授权的单条成员连接后像。原有equivalent_component关于整个登山宝训的主题不能与太5:17–7:11主题双向等价，原GPT认可异议保留。
现在只将该连接类型改为supports：成分定位[0,15)保留，Claim/Evidence/CVP文字、原有其它连接、证据和出现位置不改。支持必须真实提供目标观点的论据，不能仅因为有关联而批准。
读取完整自身讲道、完整Claim和原证据，判断这个主题成分结合其冒号后完整结构说明是否支持目标局部主题。不能借用别篇论证或补Claim没有说过的结论。
同Claim已有另一supports连接[15,57)，两者不同成分、互不重叠；它已双审核写入，禁止合并/退役/改动它。程序重复键包括成分定位，并非只按Claim/CVP配对。
改后目标观点无等价成员，只有支持连接。必须明确考虑这点是否使该改后状态不成立或需要新的观点修订；出现新范围问题就拒绝，不为推进通过。
decision_id待本次双审核生成fresh provenance，旧批准不能为新关系背书。审核内容后像，最终3ops新身份记录与单条linkupdate要绑定本次原始回答。
只返回一条verdict，evidence_step_id字段固定为给定的viewpoint_claim_link_id。approved=true仅当全部条件成立且problems为空；否则拒绝。不得裁判教授神学是否正确。'''


def postimage(old, adjacent):
    if (old.get('viewpoint_claim_link_id')!=LINK or old.get('claim_id')!=CLAIM
            or old.get('viewpoint_id')!=CV or old.get('link_type')!='equivalent_component'
            or old.get('revision')!=1 or old.get('effective_state')!='active'):
        raise ValueError('not exact authorized g034 equivalent link')
    spans=old.get('component_locator',{}).get('canonical_spans')
    if spans!=[{'start_char':0,'end_char':15,'exact_text':'登山寶訓以門徒的義为主题展开：'}]:
        raise ValueError('theme component drift')
    new=deepcopy(old);new.update(revision=2,link_type='supports',decision_id='PENDING-FRESH-G034-THEME-DUAL-REVIEW')
    ViewpointClaimLinkRecord.model_validate(new)
    for other in adjacent:
        if (other.get('effective_state')=='active' and other.get('viewpoint_claim_link_id')!=LINK
                and other.get('viewpoint_id')==CV and other.get('claim_id')==CLAIM
                and other.get('pinned_claim_revision')==old['pinned_claim_revision']
                and other.get('component_locator')==new['component_locator']):
            raise ValueError('duplicate exact active component')
    return new


def verify_sources(packet):
    for binding in packet['physical_bindings']:
        if file_sha256(Path(binding['path']))!=binding['file_sha256']: raise ValueError('physical source drift')


def prepare(store):
    states=snapshot(store);old=states['viewpoint_claim_links',LINK]
    adjacent=[v for (c,_),v in states.items() if c=='viewpoint_claim_links' and v.get('viewpoint_id')==CV and v.get('effective_state')=='active']
    before_packet=read_artifact(PRAYER/'packet.json')
    # No replay of prior adjudication: consume its bound upheld item directly.
    adjudication=read_artifact(PRAYER/'audit-source-adjudication/packet.json')
    handoff=read_artifact(PRAYER/'audit-source-adjudication/gpt-final-handoff.json')
    for artifact in (adjudication,handoff):
        if artifact['artifact_sha256']!=sha256_json({k:v for k,v in artifact.items() if k!='artifact_sha256'}): raise ValueError('adjudication SHA mismatch')
    expected_item=f'4:{CV}:{CLAIM}'
    if len(handoff['actions'])!=1 or handoff['actions'][0]['item_id']!=expected_item or handoff['actions'][0]['verdict']!='audit_objection_upheld':
        raise ValueError('not exact original upheld objection')
    item=adjudication['items'][0]
    if old not in item['connections'] or states['claims',CLAIM]!=item['claim']: raise ValueError('upheld Claim or link drift')
    packet={'schema_version':'wang_g034_owner_theme_support_v1',
        'owner_authorization':{'quote':'同意改为支持','allowed_link_id':LINK,'automatic_future_permission':False},
        'before_link':old,'after_link':postimage(old,adjacent),'claim':states['claims',CLAIM],
        'viewpoint_revision':states['viewpoint_revisions',old['validated_against_viewpoint_revision_id']],
        'evidence':[states['evidence_steps',b['evidence_step_id']] for b in old['evidence_bindings']],
        'complete_own_source':before_packet['complete_own_source'],'adjacent_links':adjacent,
        'upheld_adjudication':handoff,'physical_bindings':before_packet['physical_bindings'],
        'registry_before_sha256':fingerprint(states,sorted({c for c,_ in states}))}
    verify_sources(packet);write_immutable(ROOT/'packet.json',packet)
    write_immutable(ROOT/'before.json',{'collections':sorted({c for c,_ in states}),'pre_fingerprint_sha256':packet['registry_before_sha256']})
    return states,packet


def adapter(provider,model):
    obj=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),prompt=PROMPT,
        response_model=Verdict,schema_name='wang_g034_theme_support_verdict_v1',compact_payload=True)
    obj.max_request_bytes=500000
    return obj


ROLES=[('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]


def reviews(packet, *, cached_only=False):
    result={}
    for label,provider,model in ROLES:
        obj=adapter(provider,model);path=ROOT/f'raw-{label}.json';start=ROOT/f'{label}-review-start.json'
        if cached_only and not path.exists(): raise ValueError('saved approval missing; no model fallback')
        if start.exists() and not path.exists(): raise ValueError('started review without answer; no replay')
        size=obj.request_bytes(packet)
        if size>500000: raise ValueError('frozen capacity exceeded')
        write_immutable(start,{'packet_sha256':sha256_json(packet),'request_bytes':size,'maximum_calls':1})
        write_derived(ROOT/'progress.json',{'status':f'reviewing_{label}','group_complete':False,'registry_writes':0})
        try:
            response,calls,_=call_model(obj,packet,path);verdict=Verdict.model_validate(response)
            if (cached_only and calls) or verdict.evidence_step_id!=LINK or not verdict.approved or verdict.problems:
                raise ValueError(f'{label} rejected theme supports postimage')
        except Exception as exc:
            write_immutable(ROOT/'failure.json',{'role':label,'error':str(exc),'registry_writes':0});raise
        result[label]=response
    return result


def migrate_store_conflict_cache(store, packet):
    """Only an unchanged, never-applied failed transaction may reuse approvals."""
    if ROOT != RECOVERY_ROOT: raise ValueError('wrong mechanical recovery root')
    old_packet=read_artifact(STORE_CONFLICT_ROOT/'packet.json')
    old_plan=read_artifact(STORE_CONFLICT_ROOT/'dry-run-plan.json')
    if old_packet!=packet: raise ValueError('reviewed semantic packet drift')
    if (STORE_CONFLICT_ROOT/'apply-receipt.json').exists(): raise ValueError('prior apply receipt exists')
    if not (STORE_CONFLICT_ROOT/'apply-intent.json').exists(): raise ValueError('missing original terminal reservation')
    if store.get_change_set_status(old_plan['fingerprint_sha256']) is not None:
        raise ValueError('prior ChangeSet exists; no recovery replay')
    proof=read_artifact(STORE_CONFLICT_ROOT/'apply-conflict-zero-write.json')
    if proof!={'registry_unchanged':True,'link_unchanged':True,'database_change_set_status':None,'registry_writes':0}:
        raise ValueError('not exact zero-write conflict proof')
    bindings=[]
    for label,provider,model in ROLES:
        raw=STORE_CONFLICT_ROOT/f'raw-{label}.json'
        response,calls,_=call_model(adapter(provider,model),packet,raw)
        verdict=Verdict.model_validate(response)
        if calls or not verdict.approved or verdict.problems or verdict.evidence_step_id!=LINK:
            raise ValueError('invalid frozen dual approval')
        bindings.append({'path':str(raw),'file_sha256':file_sha256(raw)})
        write_immutable(ROOT/raw.name,read_artifact(raw))
    write_immutable(ROOT/'mechanical-cache-handoff.json',{
        'source_root':str(STORE_CONFLICT_ROOT),'semantic_packet_sha256':sha256_json(packet),
        'source_raw_bindings':bindings,'new_model_calls':0,'prior_registry_writes':0,
        'old_failure_preserved':True})


def run(*,apply=False,recover_store_conflict=False):
    global ROOT
    if recover_store_conflict: ROOT=RECOVERY_ROOT
    from backend.pipeline.viewpoint_audit_link_repair import package_for
    project=Path(__file__).resolve().parents[2]
    if project.name!='wkp-357-worktree-per-session': raise ValueError('wrong worktree')
    load_dotenv(project/'.env');store=PostgresKnowledgeStore()
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with exclusive_cvp_run_lock({'global_lock_path':str(BASE/'registry-cvp.lock')}):
            if any((ROOT/n).exists() for n in ('failure.json','apply-intent.json','apply-receipt.json')):
                raise ValueError('terminal failure/application reservation; no retry')
            states,packet=prepare(store)
            if recover_store_conflict: migrate_store_conflict_cache(store,packet)
            result=reviews(packet,cached_only=apply or recover_store_conflict)
            collections=sorted({c for c,_ in states})
            if fingerprint(snapshot(store),collections)!=packet['registry_before_sha256']: raise ValueError('Registry drift during review')
            write_immutable(ROOT/'review-report.json',{'approved':True,'reviews':result,'packet_sha256':sha256_json(packet)})
            package=package_for(states,{'targets':[{'before_link':packet['before_link'],'after_link':packet['after_link']}]},result,ROOT,
                identity_digest=sha256_json({'g034_theme_support':packet,'reviews':result}))
            decision=package['viewpoint_identity_decisions'][0]
            decision.update(policy_version='wang-g034-owner-theme-support-v1',reason='Owner-authorized exact theme component downgrade to supports; own-source postimage independently dual reviewed; component and Claim/CVP unchanged.')
            plan=store.plan_package(package,source_kind='wang_g034_owner_theme_support')
            expected={('viewpoint_claim_links','update'):1,('viewpoint_identity_candidates','create'):1,('viewpoint_identity_decisions','create'):1}
            if len(plan.operations)!=3 or any(sum(o.collection==c and o.operation==k for o in plan.operations)!=n for (c,k),n in expected.items()):
                raise ValueError('not exact three authorized operations')
            write_immutable(ROOT/'effective-package.json',package);write_immutable(ROOT/'dry-run-plan.json',json.loads(json.dumps(plan.as_dict())))
            if not apply:
                write_derived(ROOT/'progress.json',{'status':'dual_review_dry_run_passed','group_complete':False,'registry_writes':0})
                print('dual review and exact dry-run passed; zero writes');return
            if subprocess.check_output(['git','status','--porcelain'],text=True).strip(): raise ValueError('commit tested executor before apply')
            backup_label='before-g034-theme-'+sha256_json({'root':str(ROOT)})[:16]
            out=subprocess.run(['scripts/backup-registry.sh',backup_label],cwd=project,check=True,capture_output=True,text=True)
            match=re.search(r'backup-registry: wrote (.+\.dump) \(',out.stdout)
            if not match: raise ValueError('backup not reported')
            backup=Path(match.group(1)).resolve();listed=subprocess.run(['pg_restore','--list',str(backup)],check=True,capture_output=True,text=True)
            if not listed.stdout.strip(): raise ValueError('empty restore catalog')
            proof={'path':str(backup),'file_sha256':file_sha256(backup),'pg_restore_list_passed':True};write_immutable(ROOT/'backup.json',proof)
            verify_sources(packet)
            if fingerprint(snapshot(store),collections)!=packet['registry_before_sha256']: raise ValueError('pre-apply drift')
            write_immutable(ROOT/'apply-intent.json',{'packet_sha256':sha256_json(packet),'backup':proof})
            receipt=store.apply_plan(plan,metadata={'owner_theme_support':str(ROOT)},expected_registry_fingerprint_sha256=packet['registry_before_sha256'],registry_collections=collections)
            write_immutable(ROOT/'apply-receipt.json',receipt)
            expected_states=deepcopy(states)
            for op in plan.operations: expected_states[op.collection,op.object_id]=stored_operation_payload(op)
            if fingerprint(snapshot(store),collections)!=fingerprint(expected_states,collections): raise ValueError('full Registry readback mismatch')
            verify_sources(packet)
            verified={'status':'applied_readback_verified','change_set_id':plan.change_set_id,'operations':3,
                'claim_evidence_source_cvp_text_writes':0,'group_complete':False,'next_action':'effective_disposition_and_full_audit'}
            write_immutable(ROOT/'verified-result.json',verified);write_derived(ROOT/'progress.json',verified);print(json.dumps(verified))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--recover-store-conflict',action='store_true');args=p.parse_args();run(apply=args.apply,recover_store_conflict=args.recover_store_conflict)
