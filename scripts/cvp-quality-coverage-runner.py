#!/usr/bin/env python3
"""Execute the exact authorized #429 coverage proposal once, without SQL writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
from pathlib import Path
import ssl
import urllib.error
import urllib.request

SPEC = importlib.util.spec_from_file_location('quality_call_plan', Path(__file__).with_name('cvp-quality-call-plan.py'))
CP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CP)
H, PK, PF = CP.H, CP.PK, CP.PF
SPEC = importlib.util.spec_from_file_location('quality_citation_binding', Path(__file__).with_name('cvp-quality-citation-binding.py'))
CB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CB)


def material(request):
    text = request['contents'][0]['parts'][0]['text']
    return PK.expand_preview(json.loads(text.rsplit('\n', 1)[-1]))


def source_citation(citation, source):
    return CB.bind(citation, source)


def validate_observations(value, source):
    if not isinstance(value, dict) or not isinstance(value.get('observations'), list) or not isinstance(value.get('limitations'), list):
        raise ValueError('invalid observation response shape')
    seen = set()
    for row in value['observations']:
        if not isinstance(row, dict):
            raise ValueError('observation must be an object')
        oid = row.get('observation_id')
        if not isinstance(oid, str) or not oid or oid in seen:
            raise ValueError('missing/duplicate observation ID')
        seen.add(oid)
        if not isinstance(row.get('judgment'), str) or not row['judgment'].strip() or not isinstance(row.get('qualifications'), list):
            raise ValueError('missing judgment/qualifications')
        if row.get('attribution') not in {'professor_explicit', 'argument_conclusion', 'unresolved'} or row.get('status') not in {'observed', 'uncertain'}:
            raise ValueError('invalid attribution/status')
        if not isinstance(row.get('citations'), list) or not row['citations']:
            raise ValueError('observation requires own-source evidence')
        for citation in row['citations']:
            source_citation(citation, source)
    if len(PF.encoded(value)) > CP.MAX_OBSERVATION_BYTES:
        raise ValueError('observation response exceeds frozen addition budget')
    return value


def validate_coverage(value, observations, packet):
    if not isinstance(value, dict) or not isinstance(value.get('coverage'), list) or not isinstance(value.get('limitations'), list):
        raise ValueError('invalid coverage response shape')
    expected = {r['observation_id'] for r in observations['observations']}
    sources = {s['source_id']: s for s in packet['complete_other_member_sources'] + [packet['complete_target_source']]}
    ids = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'claim_id', 'evidence_step_id', 'source_fragment_id', 'fragment_id', 'viewpoint_id',
                           'viewpoint_revision_id', 'viewpoint_claim_link_id', 'source_id'} and isinstance(item, str):
                    ids.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(packet)
    seen = set()
    for row in value['coverage']:
        if not isinstance(row, dict):
            raise ValueError('coverage row must be an object')
        oid = row.get('observation_id')
        if oid not in expected or oid in seen:
            raise ValueError('duplicate/foreign coverage observation ID')
        seen.add(oid)
        if row.get('status') not in {'covered', 'partial', 'not_recorded', 'uncertain', 'not_assessable', 'requires_historical_context'}:
            raise ValueError('invalid coverage status')
        if not isinstance(row.get('reason'), str) or not isinstance(row.get('record_ids'), list):
            raise ValueError('invalid coverage evidence shape')
        if not set(row['record_ids']).issubset(ids):
            raise ValueError('foreign record ID')
        if not isinstance(row.get('citations'), list) or not row['citations']:
            raise ValueError('coverage requires physical source citations')
        for citation in row['citations']:
            sid = citation.get('source_id')
            if sid not in sources:
                raise ValueError('foreign citation source')
            source_citation(citation, sources[sid])
    if seen != expected:
        raise ValueError('incomplete coverage observation denominator')
    return value


def decode_provider(provider, requested_model):
    blocked = (provider.get('promptFeedback') or {}).get('blockReason')
    candidates = provider.get('candidates') or []
    if blocked:
        return {'state': 'provider_block', 'reason': blocked}
    if len(candidates) != 1:
        return {'state': 'no_complete_answer'}
    finish = candidates[0].get('finishReason')
    if finish in {'SAFETY', 'PROHIBITED_CONTENT', 'RECITATION'}:
        return {'state': 'provider_block', 'reason': finish}
    if finish != 'STOP':
        return {'state': 'no_complete_answer', 'finish_reason': finish}
    version = provider.get('modelVersion')
    if version and version != requested_model and not version.startswith(requested_model + '-'):
        return {'state': 'unexpected_provider_model', 'model_version': version}
    parts = candidates[0].get('content', {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {'state': 'invalid_provider_json'}
    return {'state': 'answered', 'value': value, 'model_version': version,
            'usage': provider.get('usageMetadata')}


def once_call(directory, request, model, endpoint, api_key, maximum_bytes, transport=None):
    body = PF.encoded(request)
    if len(body) > maximum_bytes:
        raise ValueError('exact request exceeds frozen item budget')
    directory.mkdir(parents=True, exist_ok=True)
    binding = {'request_sha256': PF.hashlib.sha256(body).hexdigest(), 'request_bytes': len(body),
               'model': model, 'endpoint': endpoint, 'maximum_calls': 1, 'automatic_retry': False}
    request_file, intent_file, raw_file = (directory / n for n in ('request.json', 'intent.json', 'provider-response.json'))
    if request_file.exists():
        if PF.encoded(json.loads(request_file.read_text())) != body:
            raise ValueError('once identity has conflicting exact request; no resend')
    else:
        PF.write_new(request_file, request)
    if raw_file.exists():
        saved = json.loads(raw_file.read_text())
        if not intent_file.exists() or json.loads(intent_file.read_text()) != binding or saved.get('binding') != binding:
            raise ValueError('saved provider response binding mismatch')
        return decode_provider(saved['provider_response'], model), False
    if intent_file.exists():
        return {'state': 'outcome_unknown', 'reason': 'intent_without_raw; no resend'}, False
    PF.write_new(intent_file, binding)
    if transport is None:
        def transport(body):
            req = urllib.request.Request(endpoint, data=body,
                  headers={'Content-Type': 'application/json', 'x-goog-api-key': api_key})
            bundle = next((p for p in ('/etc/ssl/cert.pem', '/opt/homebrew/etc/openssl@3/cert.pem') if Path(p).is_file()), None)
            with urllib.request.urlopen(req, timeout=300, context=ssl.create_default_context(cafile=bundle)) as response:
                return json.loads(response.read())
    try:
        provider = transport(body)
    except Exception as error:
        PF.write_new(directory / 'failure.json', {'exception_type': type(error).__name__,
                     'http_status': getattr(error, 'code', None), 'outcome': 'unknown', 'automatic_retry': False})
        return {'state': 'outcome_unknown', 'reason': 'transport_failure; no resend'}, True
    PF.write_new(raw_file, {'binding': binding, 'provider_response': provider})
    return decode_provider(provider, model), True


def main():
    runner_sha = PF.digest(Path(__file__))
    binding_sha = PF.digest(Path(CB.__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', type=Path, required=True)
    parser.add_argument('--authorization', type=Path, required=True)
    args = parser.parse_args()
    root = args.preflight.resolve()
    allowed = PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429'
    info = PF.plistlib.loads(PF.subprocess.check_output(['diskutil', 'info', '-plist', str(PF.VOLUME)]))
    if not root.is_relative_to(allowed.resolve()) or info.get('VolumeUUID') != PF.UUID or info.get('ReadOnlyVolume'):
        raise ValueError('independent UUID-verified staging required')
    proposal_root = root / 'coverage-call-proposal-v1'
    proposal = json.loads((proposal_root / 'call-proposal.json').read_text())
    authorization = json.loads(args.authorization.read_text())
    if authorization.get('proposal_file_sha256') != PF.digest(proposal_root / 'call-proposal.json') or authorization.get('maximum_new_calls') != proposal['proposed_maximum_new_calls'] or authorization.get('scope') != 'source_to_viewpoint_coverage_only':
        raise ValueError('exact bounded authorization required')
    if PF.digest(Path(CP.__file__)) != proposal['code_sha256']:
        raise ValueError('frozen request compiler changed')
    baseline = json.loads((root / 'baseline.json').read_text())
    if PF.sha(baseline) != proposal['baseline_sha256']:
        raise ValueError('proposal baseline binding mismatch')
    cfg = PF.settings()
    import os
    api_key = os.environ.get('GEMINI_API_KEY1') or cfg.get('GEMINI_API_KEY1')
    if not api_key:
        raise ValueError('authorized provider credential unavailable')
    def stable():
        if PF.sha(PF.query_snapshot(cfg['KNOWLEDGE_DATABASE_URL'])) != baseline['snapshot_sha256'] or PF.verify_sources(baseline['sources']):
            raise ValueError('SQL/source baseline drift; no new calls')
    run = root / 'coverage-execution-v1'
    run.mkdir(exist_ok=True)
    once_root = allowed / 'coverage-once-v1'
    once_root.mkdir(exist_ok=True)
    records = []
    new_calls = 0
    def invoke(unit, stage, request, maximum_bytes, source_sha):
        nonlocal new_calls
        stable()
        key = PF.sha({'source_physical_sha256': source_sha, 'stage': stage, 'task': 'cvp_quality_source_coverage_v1'})
        result, called = once_call(once_root / key, request, proposal['model'], proposal['endpoint_without_credentials'],
                                   api_key, maximum_bytes)
        new_calls += int(called)
        if new_calls > proposal['proposed_maximum_new_calls']:
            raise ValueError('invocation budget exceeded')
        stable()
        receipt = {'source_unit': unit, 'stage': stage, 'once_key': key, 'new_call': called,
                   'request_sha256': PF.sha(request), 'request_bytes': len(PF.encoded(request)),
                   'provider_state': result['state'], 'result': result}
        print(json.dumps({'source': unit, 'stage': stage, 'provider_state': result['state'],
                          'new_calls_this_invocation': new_calls}), flush=True)
        return receipt
    with (once_root / 'runner.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stable()
        for entry in proposal['entries']:
            unit = entry['source_unit']
            result = {'source_unit': unit, 'observe': {'state': entry['observe_status']},
                      'compare': {'state': entry['comparison_status']}}
            if entry['maximum_observe_calls'] == 0:
                records.append(result)
                continue
            observe_request = json.loads((proposal_root / (unit + '.observe-request.json')).read_text())
            if PF.sha(observe_request) != entry['observe_request_sha256']:
                raise ValueError('frozen observe request drift')
            source = material(observe_request)['complete_source']
            observed = invoke(unit, 'observe', observe_request, entry['observe_request_bytes'], source['physical_sha256'])
            result['observe'] = observed
            if observed['provider_state'] == 'outcome_unknown':
                records.append(result)
                break
            if observed['provider_state'] != 'answered':
                records.append(result)
                continue
            try:
                observations = validate_observations(observed['result']['value'], source)
            except ValueError as error:
                result['observe']['validation_state'] = 'invalid'
                result['observe']['validation_error'] = str(error)
                records.append(result)
                continue
            result['observe']['validation_state'] = 'valid'
            result['observe']['citation_bindings'] = [
                {'observation_id': row['observation_id'], **source_citation(citation, source)}
                for row in observations['observations'] for citation in row['citations']]
            if entry['maximum_comparison_calls'] == 0:
                records.append(result)
                continue
            template = json.loads((proposal_root / (unit + '.comparison-template.json')).read_text())
            packet = material(template)
            if PF.sha(packet) != entry['comparison_context_sha256']:
                raise ValueError('frozen complete comparison context drift')
            leaks = H.blind_leaks(packet)
            if leaks:
                result['compare'] = {'state': 'blind_projection_restricted', 'leak_paths': leaks}
                records.append(result)
                continue
            packet['independent_observations'] = observations
            compare_request = CP.request('compare', PK.compact_preview(packet))
            compared = invoke(unit, 'compare', compare_request, entry['comparison_request_maximum_bytes'], source['physical_sha256'])
            result['compare'] = compared
            if compared['provider_state'] == 'answered':
                try:
                    validate_coverage(compared['result']['value'], observations, packet)
                    result['compare']['validation_state'] = 'valid'
                    sources = {s['source_id']: s for s in packet['complete_other_member_sources'] + [packet['complete_target_source']]}
                    result['compare']['citation_bindings'] = [
                        {'observation_id': row['observation_id'], 'source_id': citation['source_id'],
                         **source_citation(citation, sources[citation['source_id']])}
                        for row in compared['result']['value']['coverage'] for citation in row['citations']]
                except ValueError as error:
                    result['compare']['validation_state'] = 'invalid'
                    result['compare']['validation_error'] = str(error)
            records.append(result)
            if compared['provider_state'] == 'outcome_unknown':
                break
        stable()
    summary = {'schema_version': 'cvp_quality_coverage_execution_v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
               'authorization_file_sha256': PF.digest(args.authorization), 'proposal_file_sha256': PF.digest(proposal_root / 'call-proposal.json'),
               'runner_code_sha256': runner_sha, 'citation_binding_code_sha256': binding_sha, 'records': records,
               'new_calls_this_invocation': new_calls, 'registry_writes': 0, 'forward_calls': 0,
               'all_fixed_source_units_recorded': len(records) == len(proposal['entries']), 'quality_evaluation_complete': False}
    filename = run / ('execution-' + PF.sha(summary)[:20] + '.json')
    PF.write_new(filename, summary)
    print(json.dumps({'execution_record': str(filename), 'new_calls': new_calls, 'source_units_recorded': len(records)}), flush=True)


if __name__ == '__main__':
    main()
