"""One owner-authorized, read-only resolution of an exact g037 contradiction.

Not an automatic retry exception. Both validated legacy judgments stay frozen;
only this target, unchanged current packet and new contextual policy may run.
No Registry writer, audit rerun or campaign launcher is available here.
"""
import argparse
import fcntl
from pathlib import Path
import shutil

from dotenv import load_dotenv
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_adjudication_history import validated_history, target_identity
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, subscription_client, call_model
from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH

BASE = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357')
OLD = BASE/'g037-owner-claim-condition-repair-20261005-v1/continuation/audit-source-adjudication'
LATEST = BASE/'g037-owner-secondary-supports-repair-20261005-v1/continuation/audit-source-adjudication'
OUTPUT = BASE/'g037-owner-contextual-conflict-resolution-20261005-v1'
TARGET = '4:CV-84ff210efbba6aeff1e0:DK-78f4b0b8eb95-P01-CL001'
PINS = ('a032cfed9ffbabb18af41529c90c0f39d879871ede8c3f73f417edc486351d02',
        '8852dd3a83979e341abb326568450c64e4e3990a78175239b5dc23309398391a')


def exact_conflict(old, old_findings, current, current_findings):
    if len(current['items']) != 1 or current['items'][0]['item_id'] != TARGET:
        raise ValueError('not the single authorized g037 contradiction')
    item = current['items'][0]
    matches = [i for i in old['items'] if i['item_id'] == TARGET]
    if len(matches) != 1 or target_identity(matches[0]) != target_identity(item):
        raise ValueError('changed target is outside conflict resolution authority')
    if (old_findings[TARGET]['verdict'], current_findings[TARGET]['verdict']) != (
            'audit_objection_rejected', 'audit_objection_upheld'):
        raise ValueError('not the exact preserved rejected/upheld contradiction')


def run(calibration, *, preflight_only=False):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, source_review_adapter, validate_findings
    from backend.pipeline.viewpoint_authorized_source_repair_357 import snapshot
    from backend.pipeline.codex_subscription_preflight import verify_runtime
    if calibration.resolve() != BASE/'cvp-semantic-calibration-20261005-v1':
        raise ValueError('calibration must use the preserved canonical result')
    result = read_artifact(calibration/'result.json')
    if (not result['passed'] or result['registry_writes'] != 0 or result['production_approval']
            or result['g037_completion'] or set(result['results']) != {'gpt','opus','gemini'}):
        raise ValueError('calibration did not pass; never production approval')
    from backend.pipeline.viewpoint_semantic_eval import score
    from backend.pipeline.viewpoint_semantic_calibration import Answers
    dataset = read_artifact(calibration/'private-expected-dataset.json')
    for name in ('gpt','opus','gemini'):
        raw = read_artifact(calibration/f'raw-{name}.json')
        if raw['artifact_sha256'] != sha256_json({k:v for k,v in raw.items() if k!='artifact_sha256'}):
            raise ValueError('calibration raw drift')
        if name == 'gemini':
            import json
            response = json.loads(''.join(p.get('text','') for p in raw['provider_response']['candidates'][0]['content']['parts']))
        else:
            response = raw['response']
        checked = score(dataset, Answers.model_validate(response).model_dump()['answers'])
        saved = read_artifact(calibration/f'{name}-score.json')
        if not checked['passed'] or any(saved.get(k)!=v for k,v in checked.items()):
            raise ValueError('calibration score drift')
    with (BASE/'immediate-recovery-20261002-v1/recovery.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if OUTPUT.exists():
            raise ValueError('existing conflict attempt; inspect receipts, no retry')
        old, old_f = validated_history(OLD)
        current, current_f = validated_history(LATEST)
        for root, pin in zip((OLD,LATEST),PINS):
            if read_artifact(root/'gpt-final-handoff.json')['artifact_sha256'] != pin:
                raise ValueError('authorized historical judgment drift')
        exact_conflict(old, old_f, current, current_f)
        store = PostgresKnowledgeStore()
        live = snapshot(store)
        audit = Path(current['audit_binding']['path'])
        if build_packet(audit, store) != current:
            raise ValueError('current Registry or full own-source packet drift')
        client = subscription_client('codex','gpt-6.1-sol','high')
        client.executable = shutil.which('codex') or client.executable
        runtime = verify_runtime(client,BASE/'immediate-recovery-20261002-v1/codex-runtime-proofs')
        adapter = source_review_adapter(client,OUTPUT/'raw-gpt.json')
        adapter.max_request_bytes = 500000
        if adapter.request_bytes(current)>500000:
            raise ValueError('conflict request exceeds frozen ceiling')
        if any(read_artifact(r/'raw-gpt.json')['prompt_sha256']==adapter.prompt_sha256 for r in (OLD,LATEST)):
            raise ValueError('not a new contextual policy; do not repeat old request')
        preflight = read_artifact(calibration/'preflight.json')
        if preflight['contract_sha256'] != binding(CONTRACT_PATH)['file_sha256']:
            raise ValueError('calibrated contract changed')
        authority = {'schema_version':'wang_g037_contextual_conflict_once_v1',
            'authorization_quote':'好, 你继续', 'target':TARGET, 'maximum_model_calls':1,
            'registry_writes':0, 'human_content_approval':False,
            'old_budgets_reset':False,'output_root':str(OUTPUT),
            'bindings':[binding(r/n) for r in (OLD,LATEST) for n in ('packet.json','raw-gpt.json','gpt-final-handoff.json')]
                +[binding(p) for p in sorted(calibration.glob('*.json'))]+[binding(CONTRACT_PATH)]}
        if preflight_only:
            return {'status':'readonly_preflight_verified','request_bytes':adapter.request_bytes(current),
                    'new_model_calls':0,'registry_writes':0,'production_approval':False}
        # Stable reservation lives outside the new output: code/root changes
        # cannot create a second budget for this explicitly authorized action.
        reservation = LATEST/'owner-contextual-conflict-start.json'
        with reservation.open('x',encoding='utf-8') as stream:
            import json
            json.dump(authority,stream,ensure_ascii=False,indent=2)
        write_immutable(OUTPUT/'owner-authorization.json',authority)
        write_immutable(OUTPUT/'packet.json',current)
        write_immutable(OUTPUT/'runtime-preflight.json',runtime)
        write_immutable(OUTPUT/'request-preflight.json',{'bytes':adapter.request_bytes(current),'model':adapter.model_id,
            'backend':adapter.backend,'prompt_sha256':adapter.prompt_sha256,'generation_config_sha256':adapter.generation_config_sha256})
        try:
            response,calls,_ = call_model(adapter,current,OUTPUT/'raw-gpt.json')
            if calls!=1 or client.cli_identity!=runtime['identity']['cli']:
                raise ValueError('actual call/runtime identity drift')
            findings = validate_findings(response,current)
            if snapshot(store)!=live or build_packet(audit,store)!=current:
                raise ValueError('Registry/source changed during conflict resolution')
            finding = findings[TARGET]
            report = {'schema_version':'wang_audit_source_adjudication_handoff_v1',
                'packet_sha256':current['artifact_sha256'],'actions':[{'item_id':TARGET,
                    'verdict':finding['verdict'],'next_action':'independent_resolution_required' if finding['verdict']=='audit_objection_rejected' else 'owner_judgment_required','gpt':finding}],
                'group_complete':False,'master_data_mutations':0,'maximum_model_calls':1}
            report['artifact_sha256']=sha256_json(report)
            write_immutable(OUTPUT/'gpt-final-handoff.json',report)
            return report
        except Exception as exc:
            if client.last_raw_response is not None:
                write_immutable(OUTPUT/'failed-transport.json',{'raw_response':client.last_raw_response})
            write_immutable(OUTPUT/'failure.json',{'error_type':type(exc).__name__,'no_retry':True,'registry_writes':0})
            raise


if __name__ == '__main__':
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calibration',type=Path,required=True)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    print(run(args.calibration,preflight_only=args.preflight_only))
