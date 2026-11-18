import copy

import pytest

from backend.pipeline.chapter_unit_plan import compile_plan, digest


def test_chapter_plan_preserves_approved_units_and_reassembles_later_arguments():
    def group(source, number, start):
        key = f'{source}:G{number}'
        paragraph = f'S{number:04}'
        return key, {'source_id': source, 'lecture': source, 'lecture_number': int(source),
                     'group_id': f'G{number}', 'title': key, 'paragraph_keys': [paragraph],
                     'claims': [], 'source_ranges': [
                         {'paragraph_key': paragraph, 'start': start, 'end': start + 5, 'text': key}]}

    groups = dict([group('1', 1, 5), group('3', 1, 10), group('4', 4, 35),
                   group('5', 5, 15), group('6', 1, 10), group('6', 5, 65)])
    approved = {'unit_number': 1, 'groups': [{'key': '1:G1', 'source_ranges': groups['1:G1']['source_ranges']}],
                'playback_order': [],
                'sub_units': [{'id': '1.1', 'title': '八福', 'group_keys': ['1:G1']}]}
    packet = {'scope': ['mat-5-1-48', 'mat-6-1-34', 'mat-7-1-29'],
              'groups': groups, 'inputs': [], 'reserved_unit_numbers': 9,
              'baseline': {'units': {'mat-5-1-12': approved}}}

    def unit(passage, keys):
        return {'passage': passage, 'title': passage, 'sub_units': [
            {'title': '完整論述', 'arguments': [
                {'key': key, 'placement': 'direct', 'reason': '直接講解'} for key in keys]}
        ]}

    decision = {'input_sha256': digest(packet), 'deferred_groups': [],
                'units': [unit('mat-5-27-30', ['3:G1', '4:G4']),
                          unit('mat-6-8-to-7-11', ['5:G5', '6:G1']),
                          unit('mat-7-12-29', ['6:G5'])]}
    plan = compile_plan(packet, decision)
    assert plan['units']['mat-5-1-12'] == approved
    assert [r['group_key'] for r in plan['units']['mat-5-27-30']['playback_order']] == ['3:G1', '4:G4']
    assert [r['group_key'] for r in plan['units']['mat-6-8-to-7-11']['playback_order']] == ['5:G5', '6:G1']
    assert [r['group_key'] for r in plan['units']['mat-7-12-29']['playback_order']] == ['6:G5']

    overlap = copy.deepcopy(decision)
    overlap['units'][1]['passage'] = 'mat-6-8-to-7-12'
    with pytest.raises(ValueError, match='Overlapping'):
        compile_plan(packet, overlap)

    missing = copy.deepcopy(decision)
    missing['units'].pop()
    with pytest.raises(ValueError, match='Groups without a disposition: 6:G5'):
        compile_plan(packet, missing)

    duplicate = copy.deepcopy(decision)
    duplicate['units'][2]['sub_units'][0]['arguments'][0]['key'] = '4:G4'
    with pytest.raises(ValueError, match='duplicate group'):
        compile_plan(packet, duplicate)

    with pytest.raises(ValueError, match='does not match'):
        compile_plan({**packet, 'reserved_unit_numbers': 10}, decision)
