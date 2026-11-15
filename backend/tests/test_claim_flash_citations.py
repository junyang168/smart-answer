"""Program-built explicit citations and the fragment-visibility check (#702)."""
import hashlib
import json

import pytest

from backend.pipeline import claim_flash_citations as cit, claim_flash_flow as flow
from backend.pipeline import claim_flash_source_fill as fill, evidence_refresh as e

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
            if 'prayer' in spec['criteria']:
                praying = self.picks.get('prayer') and '禱告' in req['state'].get('paragraph', '')
                choice = 'prayer' if praying else 'not_prayer'
            else:
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
    assert next(c['mark'] for c in listing['citations'] if c['ref'] == 'Matt 19:6') == '第六節'


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
    asked = [c for c in ports.calls if 'prayer' not in c['questions']['Q0']['criteria']]
    assert ('S0001', 'John 8:44') in keys(listing, 'introduced') and len(asked) == 1
    assert cit.build({}, sources(text), 'S', tmp_path, Jev(), holy=HOLY) == listing
    with pytest.raises(ValueError, match='source changed'):
        cit.build({}, sources(text + '改了'), 'S', tmp_path, Jev(), holy=HOLY)


def test_not_quote_adds_nothing(tmp_path):
    listing = cit.build({}, sources('神說：神啊，你頭腦不太清楚，說謊的父魔鬼在哪裡。'), 'S', tmp_path, Jev(), holy=HOLY)
    assert not keys(listing, 'introduced')


def fragment(text, pk='S0001', fid='F'):
    return {f'source_fragments/{fid}': {'payload': {'source_id': 'S', 'paragraph_key': pk, 'verbatim_excerpt': text}}}


def literal(records, src):
    """Test stand-in for the literal binding check: text present in its paragraph."""
    return {k: r['payload'] for k, r in records.items() if k.startswith('source_fragments/')
            and r['payload']['verbatim_excerpt'] in src['S']['paragraphs'][r['payload']['paragraph_key']]['text']}


def check(records, src, listing, holy=HOLY):
    return cit.fragment_check(records, src, 'S', listing, holy=holy, valid=literal(records, src))


def listing_of(pk, ref, quote, mark=None):
    b, cv = ref.split(' ')
    c, v = cv.split(':')
    return {'citations': [{'paragraph_key': pk, 'basis': 'spoken', 'ref': ref, 'key': [b, int(c), int(v)], 'quote': quote,
                           'mark': mark or quote}]}


def test_any_source_fragment_shows_the_citation_whoever_links_it():
    src = sources('馬太福音十九章六節講得很清楚。')
    checked = check(fragment('馬太福音十九章六節講得很清楚。'), src, listing_of('S0001', 'Matt 19:6', '馬太福音十九章六節講得很清楚。'))
    assert checked['visible'] == 1 and not checked['spans']


def test_fragment_holding_the_reference_but_not_the_sentence_end_is_visible():
    # Lecture 1 S0128: the fragment stops before 「，跟你們講。」 yet holds the citation.
    sentence = '所以我現在告訴各位說，馬太十九章六節所講是一樣，同樣的事，跟你們講。'
    checked = check(fragment('所以我現在告訴各位說，馬太十九章六節所講是一樣，同樣的事'), sources(sentence),
                    listing_of('S0001', 'Matt 19:6', sentence, '馬太十九章六節'))
    assert checked['visible'] == 1 and not checked['spans']


def test_loose_reading_two_paragraphs_away_is_visible():
    # Lecture 4 S0135: he names 19:22; a fragment two paragraphs on has his loose reading.
    holy = cit.Bible({('Matt', 19, 22): '那少年人聽見這話，就憂憂愁愁地走了；因為他的產業很多。'})
    src = sources('十九章第二十二節。', '別的。', '那少年人聽了這話就憂愁走了，因為他的財產甚多。')
    checked = check(fragment('那少年人聽了這話就憂愁走了，因為他的財產甚多。', pk='S0003'), src,
                    listing_of('S0001', 'Matt 19:22', '十九章第二十二節。', '十九章第二十二節'), holy)
    assert checked['visible'] == 1 and not checked['spans']


def test_fragment_from_an_earlier_transcript_version_covers_nothing():
    # Lecture 1 S0029 carries two replaced fragments whose text is not in it.
    src = sources('我們從馬太福音十九章六節開始。')
    records = fragment('馬太福音十九章六節開始，已經過去了。')
    assert not literal(records, src)
    assert check(records, src, listing_of('S0001', 'Matt 19:6', '我們從馬太福音十九章六節開始。', '馬太福音十九章六節'))['spans']


def test_uncovered_citation_becomes_a_fragment_span_without_any_evidence():
    # Lecture 1 S0030: he names 5:32 and no SourceFragment holds the sentence.
    src = sources('其實這節聖經啊，馬太福音十九章六節，耶穌也是在談這個問題。', '別的。')
    checked = check(fragment('別的。', pk='S0002'), src,
                    listing_of('S0001', 'Matt 19:6', '其實這節聖經啊，馬太福音十九章六節，耶穌也是在談這個問題。', '馬太福音十九章六節'))
    assert checked['spans'] == [{'reference': 'Matt 19:6', 'paragraph_key': 'S0001',
                                 'quote': '其實這節聖經啊，馬太福音十九章六節，耶穌也是在談這個問題。'}]


def test_named_chapter_without_a_fragment_becomes_a_span():
    # Lecture 1 S0029: 「我們從《馬太福音》第五章開始的處理。」 is in no SourceFragment.
    src = sources('我們從《馬太福音》第十九章開始的處理。')
    listing = {'citations': [
        {'paragraph_key': 'S0001', 'basis': 'spoken', 'ref': 'Matt 19', 'key': ['Matt', 19, None],
         'quote': '我們從《馬太福音》第十九章開始的處理。', 'mark': '馬太福音》第十九章'}]}
    assert check({}, src, listing)['spans'][0]['quote'] == '我們從《馬太福音》第十九章開始的處理。'


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


def test_citation_fill_saves_only_a_source_fragment(sample):
    store, root, _ = sample
    p = store.get_record_state('source_fragments', 'F')['payload']
    p['verbatim_excerpt'] = '你不知道神的旨意，怎麼遵行？'
    p['verbatim_excerpt_sha256'] = hashlib.sha256(p['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id': 'short', 'source_fragments': [p]}))
    evidence = store.get_record_state('evidence_steps', 'E')
    spans = [{'reference': 'John 8:44', 'paragraph_key': 'S0001', 'quote': '你沒有聽從祂的話，你根本沒信。'}]
    result = fill.run_citations(store, 'S', spans, root / 'cite', apply=True)
    assert result['status'] == 'applied', result['items']
    assert result['model_calls'] == 0 and not result['updates']
    [new] = result['new_fragments'].values()
    assert new['verbatim_excerpt'] == '你沒有聽從祂的話，你根本沒信。'
    saved = store.get_record_state('source_fragments', new['fragment_id'])['payload']
    assert saved['verbatim_excerpt'] == new['verbatim_excerpt'] and saved['scripture_refs'] == ['John 8:44']
    assert store.get_record_state('evidence_steps', 'E') == evidence
    assert fill.run_citations(store, 'S', spans, root / 'cite', apply=True)['status'] == 'applied'
    again = fill.run_citations(store, 'S', spans, root / 'again', apply=True)
    assert not again['new_fragments'] and again['items'][0]['status'] == 'already_covered'
    missing = fill.run_citations(store, 'S', [{**spans[0], 'quote': '不在原文。'}], root / 'absent', apply=True)
    assert missing['status'] == 'needs_attention' and not missing['new_fragments']


def test_tag_needs_the_citation_itself_in_the_fragment():
    from backend.pipeline import claim_flash_citation_tags as tags
    spoken = {'basis': 'spoken', 'key': ['Matt', 5, 32], 'ref': 'Matt 5:32', 'mark': '馬太福音三十二節'}
    assert tags.holds(spoken, '其實這節聖經啊，OK，馬太福音三十二節，耶稣也是在談這個問題', HOLY)
    assert not tags.holds(spoken, '耶稣也是在談這個問題，談到離婚的問題。', HOLY)
    read = {'basis': 'verbatim', 'key': ['Matt', 19, 6], 'ref': 'Matt 19:6', 'quote': '...'}
    assert tags.holds(read, '他說神配合的，人不可分開，所以不能離婚', HOLY)
    assert not tags.holds(read, '夫妻是一體，神配合', HOLY)


def test_backfill_marks_only_existing_source_fragments(sample):
    from backend.pipeline import claim_flash_citation_tags as tags
    store, root, _ = sample
    holy = cit.Bible({('John', 8, 47): '出於神的，必聽神的話；你們不聽，因為你們沒有聽從祂的話，你根本沒信。'})
    before = store.get_record_state('source_fragments', 'F')['payload']
    evidence = store.get_record_state('evidence_steps', 'E')
    result = tags.run(store, 'S', root / 'tags', holy=holy, apply=True)
    assert result['status'] == 'applied' and result['introduced'] == 'not_judged' and not result['new_fragments']
    after = store.get_record_state('source_fragments', 'F')['payload']
    assert after['scripture_refs'] == ['John 8:47']
    assert {k: v for k, v in after.items() if k not in ('scripture_refs', 'revision')} == \
        {k: v for k, v in before.items() if k != 'revision'}
    assert store.get_record_state('evidence_steps', 'E') == evidence
    assert tags.run(store, 'S', root / 'tags', holy=holy, apply=True)['status'] == 'applied'
    again = tags.run(store, 'S', root / 'again', holy=holy, apply=True)
    assert again['status'] == 'verified' and not again['updates']


def test_reading_decides_the_book_of_a_carried_verse_number(tmp_path):
    holy = cit.Bible({('Lev', 18, 6): '你們都不可露骨肉之親的下體，親近他們。我是耶和華。',
                      ('Lev', 5, 19): '這是贖愆祭；他實在在耶和華面前有了罪。',
                      ('Matt', 5, 19): '所以，無論何人廢掉這誡命中最小的一條，又教訓人這樣做，他在天國要稱為最小的。'})
    paragraphs = {'S0001': {'text': '利未記十八章所禁戒的那些東西。'},
                  'S0002': {'text': '五章十九節說：「所以，無論何人廢掉這誡命中最小的一條，又教訓人這樣做。」'}}
    listing = cit.build({}, {'S': {'paragraphs': paragraphs}}, 'S', tmp_path, None, holy=holy)
    refs = {c['ref'] for c in listing['citations'] if c['paragraph_key'] == 'S0002'}
    assert refs == {'Matt 5:19'}


def test_reading_overrides_a_misspoken_chapter(tmp_path):
    holy = cit.Bible({('Dan', 2, 13): '於是命令發出，要殺戮哲士。',
                      ('Dan', 7, 13): '我在夜間的異象中觀看，見有一位像人子的，駕著天雲而來，被領到亙古常在者面前，'})
    paragraphs = {'S0001': {'text': '但以理書第七章。然後有第三個獸，第二章……然後第十三節：「我在夜間的異象中觀看，見有一位像人子的，駕著天雲而來。」'}}
    listing = cit.build({}, {'S': {'paragraphs': paragraphs}}, 'S', tmp_path, None, holy=holy)
    assert {c['ref'] for c in listing['citations'] if c['key'][2]} == {'Dan 7:13'}


def test_a_prayer_jev_recognizes_is_not_read_as_scripture(tmp_path):
    holy = cit.Bible({('2Cor', 1, 3): '願頌讚歸與我們的主耶穌基督的父神，就是發慈悲的父，賜各樣安慰的神。',
                      ('Matt', 16, 13): '耶穌到了該撒利亞腓立比的境內，就問門徒說：人說我人子是誰？'})
    paragraphs = {'S0001': {'text': '我們這樣禱告，奉我們的主耶穌基督的聖名求，阿們。'},
                  'S0002': {'text': '耶穌到了該撒利亞腓立比的境內，就問門徒。'}}
    for picks, expected in (({}, {'2Cor 1:3', 'Matt 16:13'}), ({'prayer': True}, {'Matt 16:13'})):
        root = tmp_path / str(len(picks))
        listing = cit.build({}, {'S': {'paragraphs': paragraphs}}, 'S', root, Jev(picks), holy=holy)
        assert {c['ref'] for c in listing['citations']} == expected
