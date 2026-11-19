import json
from pathlib import Path

import pytest

from backend.pipeline import svg_diagram as s

ARROWS = '''<svg xmlns="http://www.w3.org/2000/svg"><defs><marker id="m"/></defs>
<text x="400" y="60" font-size="48">神</text>
<line x1="380" y1="80" x2="280" y2="200" marker-end="url(#m)"/>
<line x1="420" y1="80" x2="520" y2="200" marker-end="url(#m)"/>
<g transform="translate(200, 240)"><text x="0" y="0" font-size="42">舊約</text><text x="0" y="60">1. 施恩</text><text x="0" y="110">2. 要求</text></g>
<g transform="translate(600, 240)"><text x="0" y="0" font-size="42">新約</text><text x="0" y="60">1. 施恩</text><text x="0" y="110">2. 要求</text></g></svg>'''

BOXES = '''<svg xmlns="http://www.w3.org/2000/svg"><style>.normal { font-size: 22px; }</style>
<text x="50" y="45">登山寶訓</text>
<rect x="50" y="70" width="750" height="400"/><line x1="50" y1="165" x2="800" y2="165"/>
<text x="425" y="115">論彌賽亞</text><text x="425" y="150" class="normal">太 5:3-12&#160;&#160;應驗賽 61</text>
<text x="425" y="235">門徒的使命</text>
<rect x="130" y="280" width="590" height="150"/><line x1="130" y1="355" x2="720" y2="355"/>
<text x="425" y="330" class="normal">太 5:13-16&#160;&#160;綜合</text>
<text x="425" y="410" class="normal">太 5:17-7:29&#160;&#160;闡明，細節</text></svg>'''

CIRCLES = '''<svg xmlns="http://www.w3.org/2000/svg"><g transform="translate(400, 280)">
<circle cx="-40" cy="0" r="130"/><circle cx="40" cy="0" r="130"/>
<text x="0" y="-20" font-size="42">重</text><text x="0" y="35" font-size="42">疊</text></g>
<text x="280" y="500" font-size="28">摩西的律法</text><text x="520" y="500" font-size="28">基督的律法</text></svg>'''

SIDE_BY_SIDE = '''<svg xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="400" height="300"/><text x="200" y="30">框</text>
<rect x="10" y="50" width="180" height="100"/><text x="100" y="80">左</text>
<rect x="210" y="50" width="180" height="100"/><text x="300" y="80">右</text>
<rect x="10" y="200" width="380" height="50"/><text x="200" y="230">下</text></svg>'''


def rel(svg):
    return set(s.read(svg)['relations'])


def test_arrows_go_from_the_nearest_text_to_the_nearest_text():
    got = rel(ARROWS)
    assert ('arrow', '神', '舊約') in got and ('arrow', '神', '新約') in got


def test_a_translated_group_is_a_column_under_its_heading():
    assert ('column', '舊約', ('1. 施恩', '2. 要求')) in rel(ARROWS)


def test_cells_cut_by_a_line_and_a_title_above_the_box_name_the_regions():
    got = rel(BOXES)
    assert ('inside', '論彌賽亞', '登山寶訓') in got
    assert ('inside', '太 5:3-12 應驗賽 61', '論彌賽亞') in got
    assert ('above', '論彌賽亞', '門徒的使命') in got
    assert ('inside', '太 5:13-16 綜合', '門徒的使命') in got
    assert ('above', '太 5:13-16 綜合', '太 5:17-7:29 闡明，細節') in got


def test_text_in_both_circles_and_labels_under_them():
    got = rel(CIRCLES)
    assert ('overlap', '基督的律法', '摩西的律法') in got
    assert ('inside_both', '重', ('基督的律法', '摩西的律法')) in got


def test_level_boxes_are_side_by_side_not_one_above_the_other():
    got = rel(SIDE_BY_SIDE)
    assert ('left_of', '左', '右') in got and ('above', '左', '右') not in got
    assert ('above', '右', '下') in got


def test_nbsp_and_runs_of_spaces_normalise():
    assert '太 5:3-12 應驗賽 61' in s.read(BOXES)['texts']


def test_an_unknown_transform_is_refused():
    with pytest.raises(ValueError, match='unsupported transform'):
        s.read('<svg xmlns="http://www.w3.org/2000/svg"><g transform="rotate(30)"><text>x</text></g></svg>')


def test_grade_fails_a_missing_word_and_a_missing_relation():
    key = {'key': 'D', 'texts': ['登山寶訓', '論彌賽亞', '太 5:3-12 應驗賽 61', '門徒的使命', '太 5:13-16 綜合', '太 5:17-7:29 闡明，細節'],
           'relations': [['inside', '太 5:17-7:29 闡明，細節', '門徒的使命']]}
    assert s.grade(key, BOXES)['passed']
    wrong = BOXES.replace('7:29', '7:27')
    result = s.grade(key, wrong)
    assert not result['passed'] and result['texts_missing'] == ['太 5:17-7:29 闡明，細節']
    moved = dict(key, relations=[['inside', '太 5:17-7:29 闡明，細節', '論彌賽亞']])
    assert s.grade(moved, BOXES)['relations_missing']


def test_the_saved_key_covers_his_three_diagrams():
    keys = json.loads(s.KEY.read_text(encoding='utf-8'))
    assert [d['key'] for d in keys['diagrams']] == ['A', 'B', 'D']
