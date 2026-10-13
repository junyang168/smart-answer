#!/usr/bin/env python3
"""Report bounded coverage evidence without turning record coverage into CVP quality."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('quality_coverage_runner', Path(__file__).with_name('cvp-quality-coverage-runner.py'))
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)
PF = R.PF


def destination_references(row, packet):
    """Resolve cited identities mechanically; do not judge their semantic equivalence."""
    cited = set(row['record_ids'])
    identity, related = [], []
    for v in packet['connected_complete_viewpoints']:
        viewpoint = v['viewpoint']
        direct = cited.intersection({viewpoint['viewpoint_id'], viewpoint.get('viewpoint_revision_id')})
        for link in v['links']:
            if cited.intersection({link['claim_id'], link['viewpoint_claim_link_id']}):
                destination = {'viewpoint_id': viewpoint['viewpoint_id'], 'claim_id': link['claim_id'],
                               'viewpoint_claim_link_id': link['viewpoint_claim_link_id'], 'link_type': link['link_type']}
                (identity if link['link_type'] in {'equivalent_full', 'equivalent_component'} else related).append(destination)
        if direct:
            identity.append({'viewpoint_id': viewpoint['viewpoint_id'], 'direct_reference': True})
    claims = sorted(cited.intersection({c['claim_id'] for c in packet['source_claims']}))
    kind = ('identity_destination_reference_resolved' if identity else
            'related_role_only' if related else 'claim_only' if claims else 'no_destination_cited')
    return {'kind': kind, 'identity_references': identity, 'related_references': related,
            'target_claim_ids': claims, 'semantic_identity_validated': False}


def build(root, execution_file):
    execution = json.loads(execution_file.read_text())
    proposal_root = root / 'coverage-call-proposal-v1'
    proposal = json.loads((proposal_root / 'call-proposal.json').read_text())
    if execution['proposal_file_sha256'] != PF.digest(proposal_root / 'call-proposal.json'):
        raise ValueError('execution proposal mismatch')
    once_root = root.parent / 'coverage-once-v1'
    fixed = {e['source_unit']: e for e in proposal['entries']}
    if len(execution['records']) != len(fixed) or {r['source_unit'] for r in execution['records']} != set(fixed):
        raise ValueError('fixed source denominator incomplete or duplicated')
    units, outcomes, statuses, destinations, usage = [], Counter(), Counter(), Counter(), Counter()
    diagnostic_statuses, diagnostic_destinations = Counter(), Counter()
    total_intents, raw_count = 0, 0
    for record in execution['records']:
        unit = record['source_unit']
        item = {'source_unit': unit, 'stages': {}, 'observation_count': None, 'coverage_rows': [],
                'citation_validation_failures': []}
        for stage in ('observe', 'compare'):
            receipt = record[stage]
            state = receipt.get('provider_state', receipt.get('state'))
            item['stages'][stage] = {'provider_state': state, 'validation_state': receipt.get('validation_state')}
            if receipt.get('validation_error'):
                item['stages'][stage]['validation_error'] = receipt['validation_error']
            if 'once_key' not in receipt:
                continue
            directory = once_root / receipt['once_key']
            raw_file = directory / 'provider-response.json'
            total_intents += int((directory / 'intent.json').exists())
            item['stages'][stage]['request_file_sha256'] = PF.digest(directory / 'request.json')
            if not raw_file.exists():
                continue
            raw = json.loads(raw_file.read_text())
            if raw['binding']['request_sha256'] != receipt['request_sha256'] or R.decode_provider(raw['provider_response'], proposal['model']) != receipt['result']:
                raise ValueError('raw response/receipt binding mismatch')
            raw_count += 1
            item['stages'][stage]['provider_raw_sha256'] = PF.digest(raw_file)
            for key, value in (raw['provider_response'].get('usageMetadata') or {}).items():
                if type(value) is int:
                    usage[key] += value
        observed = record['observe']
        if observed.get('provider_state') == 'answered':
            source = R.material(json.loads((proposal_root / f'{unit}.observe-request.json').read_text()))['complete_source']
            obs = observed['result']['value']['observations']
            item['observation_count'] = len(obs)
            for o in obs:
                for c in o['citations']:
                    try:
                        R.source_citation(c, source)
                    except ValueError as error:
                        item['citation_validation_failures'].append({'observation_id': o['observation_id'],
                            'physical_ordinal': c['physical_ordinal'], 'quote_sha256': R.CB.digest(c['quote']),
                            'reason': str(error)})
        compared = record['compare']
        packet = None
        if (proposal_root / f'{unit}.comparison-template.json').exists():
            packet = R.material(json.loads((proposal_root / f'{unit}.comparison-template.json').read_text()))
            item['blind_projection_leak_paths'] = R.H.blind_leaks(packet)
        if compared.get('provider_state') == 'answered':
            sources = {s['source_id']: s for s in packet['complete_other_member_sources'] + [packet['complete_target_source']]}
            for row in compared['result']['value']['coverage']:
                reference = destination_references(row, packet)
                for c in row['citations']:
                    try:
                        R.source_citation(c, sources[c['source_id']])
                    except ValueError as error:
                        item['citation_validation_failures'].append({'stage': 'compare',
                            'observation_id': row['observation_id'], 'source_id': c['source_id'],
                            'physical_ordinal': c['physical_ordinal'], 'quote_sha256': R.CB.digest(c['quote']),
                            'reason': str(error)})
                usable = compared.get('validation_state') == 'valid' and not item['blind_projection_leak_paths']
                item['coverage_rows'].append({'observation_id': row['observation_id'],
                    'model_status': row['status'], 'destination_references': reference,
                    'usable_independent_quality_evidence': usable})
                diagnostic_statuses[row['status']] += 1
                diagnostic_destinations[reference['kind']] += 1
                if usable:
                    statuses[row['status']] += 1
                    destinations[reference['kind']] += 1
        if compared.get('validation_state') == 'valid':
            outcome = ('comparison_blind_projection_incomplete' if item['blind_projection_leak_paths']
                       else 'complete_valid_comparison')
        elif observed.get('validation_state') == 'invalid':
            outcome = 'invalid_observation_citations'
        elif compared.get('provider_state') == 'no_complete_answer':
            outcome = 'comparison_no_complete_answer'
        elif compared.get('validation_state') == 'invalid':
            outcome = 'invalid_comparison_evidence'
        else:
            outcome = 'protected_source_or_context'
        item['outcome'] = outcome
        outcomes[outcome] += 1
        units.append(item)
    return {'schema_version': 'cvp_quality_coverage_report_v1',
        'execution_file_sha256': PF.digest(execution_file), 'report_code_sha256': PF.digest(Path(__file__)),
        'dependency_code_sha256': {Path(m.__file__).name: PF.digest(Path(m.__file__))
                                   for m in (R, R.H, R.PK, R.PF, R.CB)},
        'model': proposal['model'], 'fixed_source_denominator': len(fixed), 'units': units,
        'source_outcomes': dict(outcomes), 'accepted_comparison_observation_denominator': sum(statuses.values()),
        'model_status_counts': dict(statuses), 'mechanical_destination_reference_counts': dict(destinations),
        'diagnostic_only_model_status_counts': dict(diagnostic_statuses),
        'diagnostic_only_destination_reference_counts': dict(diagnostic_destinations),
        'provider_usage_totals': dict(usage), 'actual_unique_intents': total_intents, 'authenticated_new_raw_responses': raw_count,
        'authorized_maximum_new_calls': proposal['proposed_maximum_new_calls'],
        'quality_evaluation_complete': False, 'six_dimension_pass': None, 'population_defect_rate': None,
        'sampling_limitations': ['Fixed exclusions retained; invalid/truncated responses are not negative findings.',
            'Accepted comparisons are a selected subset, not a representative whole-registry defect denominator.',
            'Model covered status includes Claim coverage; resolved CVP references do not validate identity.',
            'Production-roster disposition and forward six-dimension evidence remain incomplete.'],
        'registry_writes': 0, 'forward_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', required=True, type=Path)
    parser.add_argument('--execution', required=True, type=Path)
    args = parser.parse_args()
    root = args.preflight.resolve()
    if not root.is_relative_to((PF.VOLUME / 'WangKnowledgePlatform/staging/cvp-quality-429').resolve()):
        raise ValueError('independent staging required')
    report = build(root, args.execution)
    output = root / 'coverage-execution-v1' / ('report-' + PF.sha(report)[:20] + '.json')
    PF.write_new(output, report)
    print(json.dumps({'report_file': str(output), 'report_sha256': PF.digest(output),
        **{k: v for k, v in report.items() if k not in {'units', 'sampling_limitations'}}}))


if __name__ == '__main__':
    main()
