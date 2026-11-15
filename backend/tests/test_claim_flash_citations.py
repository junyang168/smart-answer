"""Program-built explicit citations and the fragment-visibility check (#702)."""
import hashlib
import json

import pytest

from backend.pipeline import claim_flash_citations as cit, claim_flash_flow as flow
from backend.pipeline import claim_flash_scripture as stage, evidence_refresh as e

VERSES = {
    ('Matt', 19, 3): '有法利賽人來試探耶穌說：人無論甚麼緣故都可以休妻嗎？',
    ('Matt', 19, 6): '既然如此，夫妻不再是兩個人，乃是一體的了。所以，神配合的，人不可分開。',
    ('Matt', 19, 29): '凡為我的名撇下房屋，或是弟兄、姐妹、父親、母親、兒女、田地的，必要得著百倍，並且承受永生。',
    ('Mark', 10, 29): '耶穌說：我實在告訴你們，人為我和福音撇下房屋，或是弟兄、姐妹、父母、兒女、田地，',
    ('Dan', 7, 1): '巴比倫王伯沙撒元年，但以理在床上做夢，見了腦中的異象，就記錄這夢，述說其中的大意。',
    ('John', 8, 44): '你們是出於你們的父魔鬼，你們父的私慾你們偏要行。他說謊是出於自己；因他本來是說謊的，也是說謊之人的父。',
}
HOLY = cit.Bible(VERSES)


class Jev:
    identity = {'jev': 'test'}

    def __init__(self, picks=None):
        self.calls, self.picks = [], picks or {}

    def judge(self, req):
        self.calls.append(req)
        answers = {}
        for q, spec in req['questions'].items():
            choice = self.picks.get(q, 'not_quote')
            answers[q] = {'type': 'choice', 'choice': choice,
                          'probabilities': {k: float(k == choice) for k in spec['criteria']}}
        return {'model': e.MODEL, 'answers': answers}

    def repair(self, req):
        pytest.fail('the citation list never calls GPT')


def sources(*texts):
    return {'S': {'paragraphs': {f'S{n + 1:04d}': {'text': t} for n, t in enumerate(texts)}}}


def keys(listing, basis=None):
    return {(c['paragraph_key'], c['ref']) for c in listing['citations'] if basis in (None, c['basis'])}


def test_spoken_reference_carries_book_and_chapter_across_paragraphs(tmp_path):
    listing = cit.build({}, sources('我們來看馬太福音十九章。', '第六節說得很清楚。'), 'S', tmp_path, Jev(), holy=HOLY)
    assert ('S0002', 'Matt 19:6') in keys(listing, 'spoken')


@pytest.mark.parametrize('text', ['馬太福音十九章，就兩三節聖經。', '馬太福音十九章，三節的聖經講妻子。',
                                  '馬太福音十九章，29減掉3節，差不多。'])
def test_counted_verses_are_not_verse_numbers(tmp_path, text):
    listing = cit.build({}, sources(text), 'S', tmp_path, Jev(), holy=HOLY)
    assert not any(c['key'][2] for c in listing['citations'] if c['basis'] == 'spoken')


def test_chapter_the_carried_book_lacks_falls_back_to_main_book(tmp_path):
    texts = ('馬太福音十九章三節。', '馬太福音十九章六節。', '但以理書七章一節。', '耶穌講，十九章二十九節。')
    listing = cit.build({}, sources(*texts), 'S', tmp_path, Jev(), holy=HOLY)
    assert ('S0004', 'Matt 19:29') in keys(listing, 'spoken')
    assert not any(c['ref'].startswith('Dan 19') for c in listing['citations'])


def test_read_verse_is_a_verbatim_citation_and_formula_runs_are_not(tmp_path):
    listing = cit.build({}, sources('夫妻不再是兩個人，乃是一體的了。'), 'S', tmp_path, Jev(), holy=HOLY)
    assert keys(listing, 'verbatim') == {('S0001', 'Matt 19:6')}
    assert listing['citations'][0]['quote'] == '夫妻不再是兩個人，乃是一體的了。'


def test_parallel_quote_keeps_the_passage_a_record_holds(tmp_path):
    text = '撇下房屋，或是弟兄、姐妹的。'
    plain = cit.build({}, sources(text), 'S', tmp_path / 'plain', Jev(), holy=HOLY)
    assert not plain['citations'] and plain['unresolved'][0]['reason'] == 'parallel_passage'
    records = {'source_fragments/F': {'payload': {'source_id': 'S', 'paragraph_key': 'S0001', 'verbatim_excerpt': text}},
               'evidence_steps/E': {'payload': {'source_fragment_ids': ['F'], 'scripture_refs': ['馬太福音 19:29']}}}
    held = cit.build(records, sources(text), 'S', tmp_path / 'held', Jev(), holy=HOLY)
    assert keys(held) == {('S0001', 'Matt 19:29')}


def test_introduced_quote_asks_jev_once_and_replays(tmp_path):
    text = '耶穌明講嘛，說謊是魔鬼的兒子，魔鬼是說謊之人的父嘛。'
    ports = Jev({'Q0': 'V0'})
    listing = cit.build({}, sources(text), 'S', tmp_path, ports, holy=HOLY)
    assert ('S0001', 'John 8:44') in keys(listing, 'introduced') and len(ports.calls) == 1
    assert cit.build({}, sources(text), 'S', tmp_path, Jev(), holy=HOLY) == listing
    with pytest.raises(ValueError, match='source changed'):
        cit.build({}, sources(text + '改了'), 'S', tmp_path, Jev(), holy=HOLY)


def test_not_quote_adds_nothing(tmp_path):
    listing = cit.build({}, sources('神說：神啊，你頭腦不太清楚，說謊的父魔鬼在哪裡。'), 'S', tmp_path, Jev(), holy=HOLY)
    assert not keys(listing, 'introduced')


def lower(fragment_text, refs, *, pk='S0001', status='candidate'):
    return {'source_fragments/F': {'payload': {'source_id': 'S', 'paragraph_key': pk, 'verbatim_excerpt': fragment_text}},
            'evidence_steps/E': {'payload': {'source_fragment_ids': ['F'], 'scripture_refs': refs, 'review_status': status}}}


def listing_of(pk, ref, quote):
    b, cv = ref.split(' ')
    c, v = cv.split(':')
    return {'citations': [{'paragraph_key': pk, 'basis': 'spoken', 'ref': ref, 'key': [b, int(c), int(v)], 'quote': quote}]}


def test_held_citation_inside_a_fragment_is_visible():
    src = sources('馬太福音十九章六節，神配合的人不可分開。')
    checked = cit.fragment_check(lower('馬太福音十九章六節，神配合的人不可分開。', ['Matt 19:3-9']), src, 'S',
                                 listing_of('S0001', 'Matt 19:6', '馬太福音十九章六節，神配合的人不可分開。'), holy=HOLY)
    assert checked['visible'] == 1 and not checked['jobs']


def test_held_citation_outside_every_fragment_becomes_a_literal_job():
    src = sources('馬太福音十九章六節講得很清楚。我們看下去。')
    checked = cit.fragment_check(lower('我們看下去。', ['Matt 19:6']), src, 'S',
                                 listing_of('S0001', 'Matt 19:6', '馬太福音十九章六節講得很清楚。'), holy=HOLY)
    [job] = checked['jobs']
    assert job['target_keys'] == ['evidence_steps/E'] and job['program_citation']
    assert job['quote'] == '馬太福音十九章六節講得很清楚。'


def test_unheld_distant_and_protected_citations_are_reported_not_written():
    src = sources(*['段落。'] * 6)
    listing = listing_of('S0001', 'Matt 19:6', '段落。')
    assert cit.fragment_check(lower('段落。', ['Matt 5:32']), src, 'S', listing, holy=HOLY)['unheld']
    far = cit.fragment_check(lower('另一段。', ['Matt 19:6'], pk='S0006'), src, 'S', listing, holy=HOLY)
    assert far['distant'] and not far['jobs']
    locked = cit.fragment_check(lower('另一段。', ['Matt 19:6'], pk='S0002', status='human_approved'), src, 'S', listing, holy=HOLY)
    assert locked['distant'] and not locked['jobs']


def test_bible_cache_must_be_complete(tmp_path):
    (tmp_path / 'MAT').mkdir()
    (tmp_path / 'MAT' / '1.json').write_text('{"1": "x"}')
    cit.bible.cache_clear()
    with pytest.raises(ValueError, match='1 of 1189'):
        cit.bible(tmp_path)
    cit.bible.cache_clear()


def test_old_flow_root_keeps_its_intent(tmp_path):
    base = {'version': flow.VERSION, 'source_id': 'S'}
    assert flow.with_citation_contract(tmp_path, base)['citation_contract'] == cit.CONTRACT
    (tmp_path / 'flow-intent.json').write_text(json.dumps(base))
    assert flow.with_citation_contract(tmp_path, base) == base
    (tmp_path / 'flow-intent.json').write_text(json.dumps({**base, 'citation_contract': cit.CONTRACT}))
    assert flow.with_citation_contract(tmp_path, base)['citation_contract'] == cit.CONTRACT


from backend.tests.test_evidence_refresh import sample  # noqa: E402
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres  # noqa: E402,F401


def test_program_citation_attaches_the_sentence_without_models_or_new_refs(sample):
    store, root, _ = sample
    p = store.get_record_state('source_fragments', 'F')['payload']
    p['verbatim_excerpt'] = '你不知道神的旨意，怎麼遵行？'
    p['verbatim_excerpt_sha256'] = hashlib.sha256(p['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id': 'short', 'source_fragments': [p]}))
    before = store.get_record_state('evidence_steps', 'E')['payload']
    ports = Jev()
    job = {'job_id': 'c', 'reference': 'John 8:44', 'quote': '你沒有聽從祂的話，你根本沒信。', 'paragraph_key': 'S0001',
           'target_keys': ['evidence_steps/E'], 'program_citation': True}
    result = stage.run(store, 'S', [job], root / 'cite', ports, apply=True)
    assert result['status'] == 'applied' and not ports.calls and result['new_fragments']
    after = store.get_record_state('evidence_steps', 'E')['payload']
    assert after.get('scripture_refs', []) == before.get('scripture_refs', [])
    texts = {store.get_record_state('source_fragments', fid)['payload']['verbatim_excerpt'] for fid in after['source_fragment_ids']}
    assert '你沒有聽從祂的話，你根本沒信。' in texts
    missing = stage.run(store, 'S', [{**job, 'job_id': 'd', 'quote': '不在原文。'}], root / 'absent', Jev(), apply=True)
    assert missing['status'] == 'needs_attention' and not missing['updates']
