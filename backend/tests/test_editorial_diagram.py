"""Editorial diagram gate, against the cases of editorial-gate-key.json (E5)."""
import hashlib
from copy import deepcopy

from backend.pipeline.editorial_diagram import gate

SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><rect x="0" y="0" width="400" height="300"/>'
       '<text x="10" y="20">門徒的使命</text><text x="10" y="120">6:8–7:11 inclusio</text></svg>').encode()


def record(svg=SVG):
    ruling = {'verdict': 'holds', 'reason': '他講過。', 'by': 'Claude'}
    return {'svg': {'path': 'c.svg', 'sha256': hashlib.sha256(svg).hexdigest()}, 'boxes': [
        {'text': '門徒的使命', 'claims': [{'claim_id': 'L6-CL0014', 'revision': 2}], 'ruling': dict(ruling)},
        {'text': '6:8–7:11 inclusio', 'claims': [{'claim_id': 'L5-CL0014', 'revision': 1}], 'ruling': dict(ruling)}]}


STORE = {'L6-CL0014': {'revision': 2, 'live': True, 'lecture_held': True},
         'L5-CL0014': {'revision': 1, 'live': True, 'lecture_held': True}}


def run(rec, svg=SVG, store=STORE):
    return gate(rec, svg, store.get)


def test_g0_the_corrected_diagram_passes():
    assert run(record())['status'] == 'pass'


def test_g1_a_changed_label_blocks():
    old = SVG.replace('6:8–7:11'.encode(), '6:1–7:11'.encode())
    result = run(record(), old)
    assert result['status'] == 'block' and any('6:1–7:11' in b for b in result['block'])


def test_g2_a_box_ruled_against_its_claim_blocks():
    rec = record()
    rec['boxes'][0]['ruling'] = {'verdict': 'contradicts', 'reason': '這版說到 7:27，圖把 7:28–29 放在門徒的使命之內。'}
    assert run(rec)['status'] == 'block'


def test_g3_a_box_with_no_claim_blocks():
    svg = SVG.replace(b'</svg>', '<text x="10" y="200">7:6 珍珠不可丟給豬</text></svg>'.encode())
    rec = record(svg)
    assert any('珍珠' in b for b in run(rec, svg)['block'])
    rec['boxes'].append({'text': '7:6 珍珠不可丟給豬', 'claims': [], 'ruling': {'verdict': 'holds', 'reason': '編者加的。'}})
    assert any('has no Claim' in b for b in run(rec, svg)['block'])


def test_g4_a_claim_not_live_in_its_lecture_blocks():
    for state in ({'live': False}, {'lecture_held': False}):
        store = deepcopy(STORE); store['L6-CL0014'].update(state)
        assert run(record(), store=store)['status'] == 'block'
    assert run(record(), store={'L5-CL0014': STORE['L5-CL0014']})['status'] == 'block'


def test_g5_a_revised_claim_flags_a_recheck():
    store = deepcopy(STORE); store['L6-CL0014']['revision'] = 3
    result = run(record(), store=store)
    assert result['status'] == 'flag_recheck' and result['recheck']


def test_an_unruled_box_blocks():
    rec = record(); del rec['boxes'][1]['ruling']
    assert run(rec)['status'] == 'block'
