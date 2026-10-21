"""Bounded independent review of temporary assessments; no content writes.

Target success is not whole-group completion. Original verdicts and flags remain
until complete current independent audit accounting is separately validated.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import importlib.util
import sys
from threading import Event
from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.cvp_sql_flow import binding, check_binding, pin_rows, pins
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA = 'wang_independent_revisit_direction_v1'


def reader_module():
    spec = importlib.util.spec_from_file_location('independent_revisit_reader',
        Path(__file__).resolve().parents[2] / 'scripts/audit-library.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare(store, saved):
    if (saved['checkpoint'] != 'audited' or saved['status'] != 'succeeded'
            or saved.get('revisit_required') is not True
            or saved.get('independent_audit_complete') is not False):
        raise ValueError('revisit requires an actual audited temporary exception')
    certificate = saved.get('import_certificate') or {}
    ab = (saved.get('completion') or {}).get('audit') or binding(certificate['audit_path'])
    check_binding(ab)
    assessment = read_artifact(ab['path'])
    from backend.pipeline.viewpoint_content_blocked_fallback import KEY, capture, content_block
    if assessment['meta']['model'] != 'gemini-3.8-flash' or KEY not in assessment['meta']:
        raise ValueError('revisit cannot replace an ordinary semantic refusal')
    check_binding(assessment['meta'][KEY])
    manifest = read_artifact(assessment['meta'][KEY]['path'])
    check_binding(manifest['original_report_binding'])
    original = read_artifact(manifest['original_report_binding']['path'])
    views = [r['viewpoint_id'] for r in assessment['layers']['4']['results']]
    current = store.read_cvp_context(saved['claim_ids'], viewpoint_ids=views)
    scope = {'kind': 'sql_transaction_group', 'complete_target_members': True,
             'claim_ids': saved['claim_ids'], 'viewpoint_ids': views,
             'pending_management_record_keys': []}
    targets = []
    for layer, key in (('3', 'claim_id'), ('4', 'viewpoint_id')):
        for row in assessment['layers'][layer]['results']:
            if row.get('independent') is not False:
                continue
            if row.get('revisit_required') is not True:
                raise ValueError('temporary target omitted revisit disclosure')
            prior = [r for r in original['layers'][layer]['results'] if r[key] == row[key]]
            if len(prior) != 1 or prior[0]['verdict'] != 'blocked':
                raise ValueError('target lacks exact original independent content block')
            check_binding(prior[0]['provider_content_block'])
            content_block(read_artifact(prior[0]['provider_content_block']['path']))
            prompt, schema, sources, actual_pins, _ = capture(layer, row[key],
                semantic_contract_version=assessment['meta'].get('semantic_contract_version', 'legacy_v1'),
                sql_scope=scope, with_pins=True)
            for source in sources:
                check_binding(source)
            targets.append({'layer': layer, 'identity': row[key], 'prompt': prompt, 'schema': schema,
                'source_bindings': sources, 'read_pins': actual_pins,
                'original_block': prior[0]['provider_content_block']})
    if not targets or len({(t['layer'], t['identity']) for t in targets}) != len(targets):
        raise ValueError('missing or duplicate revisit population')
    return {'schema_version': SCHEMA, 'campaign_sha256': saved['campaign_sha256'],
            'run_id': saved['run_id'], 'claim_ids': saved['claim_ids'], 'viewpoint_ids': views,
            'prior_assessment': ab, 'original_report': manifest['original_report_binding'],
            'read_pins': pin_rows(pins(current)), 'targets': targets,
            'registry_writes': 0, 'old_verdicts_immutable': True}


def validate_direction(direction, packet):
    expected = {'schema_version': SCHEMA, 'campaign_sha256': packet['campaign_sha256'],
                'run_id': packet['run_id'], 'packet_sha256': sha256_json(packet),
                'maximum_calls_per_target': 1, 'automatic_retry': False,
                'registry_writes': 0, 'human_content_approval': False}
    if any(direction.get(k) != v for k, v in expected.items()) or not direction.get('authorization_quote'):
        raise ValueError('revisit direction differs from exact current population')


def execute(store, saved, *, authorization, root, model_factory=None):
    packet = prepare(store, saved)
    check_binding(authorization)
    validate_direction(read_artifact(authorization['path']), packet)
    root = Path(root) / saved['run_id'] / 'independent-revisit'
    write_immutable(root / 'direction-binding.json', {'authorization': authorization})
    write_immutable(root / 'packet.json', packet)
    reader = reader_module()
    settings = reader.load_settings()
    if not settings.get('GEMINI_API_KEY1'):
        raise ValueError('independent revisit requires configured Gemini API credential')
    model = (model_factory or reader.IndependentModel)('gemini-3.8-flash', settings['GEMINI_API_KEY1'],
        cache_dir=root / 'judgements', preserve_full_context=True, durable_once=True)
    unknown = Event()

    def judge(target):
        if unknown.is_set():
            raise ValueError('earlier independent revisit outcome unknown; no further calls')
        for source in target['source_bindings']:
            check_binding(source)
        response = model.judge(target['prompt'], target['schema'])
        cache = root / 'judgements' / (hashlib.sha256(
            ('gemini-3.8-flash\n' + target['prompt']).encode()).hexdigest() + '.json')
        if not cache.exists():
            unknown.set()
            raise ValueError('revisit lacks durable result; outcome unknown')
        if read_artifact(cache) != response:
            raise ValueError('revisit actual result drift')
        return {'layer': target['layer'], 'identity': target['identity'],
                'response': response, 'result_binding': binding(cache)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(judge, packet['targets']))
    for row in packet['read_pins']:
        state = store.get_record_state(row['collection'], row['object_id'])
        if state is None or [state['revision'], state['content_sha256'], state['retired']] != row['pin']:
            raise ValueError('revisit current SQL generation drift')
    passing = all(r['response'].get('verdict') == ('supported' if r['layer'] == '3' else 'same')
                  and r['response'].get('issue') == 'none' for r in results)
    receipt = {'schema_version': 'wang_independent_revisit_target_receipt_v1',
               'packet': binding(root / 'packet.json'), 'authorization': authorization,
               'results': results, 'targets_independently_supported': passing,
               'group_complete': False, 'registry_writes': 0,
               'revisit_required': True, 'independent_audit_complete': False}
    write_immutable(root / 'target-receipt.json', receipt)
    return receipt
