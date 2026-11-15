"""#783 spike: check an agent-authored draft piece by piece. Writes no database.

    PYTHONPATH=. backend/.venv/bin/python scripts/spikes/claim_creation_agent_783.py <work> <round>

Reads <work>/section.json and <work>/rounds/draft-<round>.json; writes
<work>/rounds/checks-<round>.json. GPT questions and answers are saved under
<work>/gpt-root and replay on a rerun. The author (Claude Code) never checks
its own pieces: the program checks text, GPT answers one question per piece.

Structure rules (owner discussion 2026-11-15):
  R1 One assertion per piece: an Evidence, Observation or Claim states one thing
     that can be true or false on its own (two things joined by 並且/；are two pieces).
     A quoted reading stays one piece unless its parts are used differently.
  R2 Every Evidence and Observation has at least one link.  (program)
  R3 Each link uses the whole piece it starts from, not only part of it.
     One piece may link to several Claims.
  R4 No repeats: a Claim does not restate an Evidence/Observation linked to it,
     and an Evidence/Observation does not restate the Claim it links to.
  R5 An Observation is only what the text he reads says; his own words are Evidence.
  R6 A version, language or grammar term in a statement (和合本, 希臘文, 語態 ...)
     appears in its quotes.  (program)
R1, R3, R4, R5: Jev answers one narrow question per piece or link; GPT rules
only on what Jev does not pass. Every answer is saved.
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from backend.pipeline import claim_flash_common as f, claim_flash_citations as cit
from backend.pipeline import claim_flash_citation_tags as tags, claim_flash_flow as flow
from backend.pipeline.claim_model_capacity import ClaimModelCapacity

CAPACITY = '/Volumes/Jun SSD/wang-knowledge-platform-staging/transcript-claim-migration-546/nysc2018-1-beta437-v1/capacity.json'
VERDICT = {'type': 'object', 'additionalProperties': False, 'required': ['items'], 'properties': {'items': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['job_id', 'verdict', 'problem'],
        'properties': {'job_id': {'type': 'string'}, 'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
                       'problem': {'type': 'string'}}}}}}
GAPS = {'type': 'object', 'additionalProperties': False, 'required': ['items'], 'properties': {'items': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['job_id', 'gaps'],
        'properties': {'job_id': {'type': 'string'}, 'gaps': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['paragraph_key', 'sentence', 'why'],
            'properties': {'paragraph_key': {'type': 'string'}, 'sentence': {'type': 'string'}, 'why': {'type': 'string'}}}}}}}}}
COMMON = ('你核對從王教授講課逐字稿整理出的一項記錄。只依所給原文判斷，不用你自己的神學知識補充或評價他對不對。'
          '口語講課：措辭、語氣、程度的小差異不算錯；只有改變了誰做什麼、說了什麼判斷、或加入原文沒有的內容才算錯。'
          'pass 時 problem 寫空字串；fail 時用一句話指出具體哪裡錯。\n')
STATEMENT = COMMON + ('每個工作給一條 statement、它所根據的逐字引文（quotes）及這些引文所在的段落（context）。'
    '問題：statement 是否忠實說出 quotes 的內容——主詞、動作、對象、誰說的話都與原文一致，沒有加入 quotes 沒有的內容？')
CLAIM = COMMON + ('每個工作給一條 Claim（教授的論斷）、它所根據的逐字引文（quotes），以及整段講課原文（section）。'
    '問題：這是不是教授自己在這裡明說的結論——不是問題、不是別人的看法、不只是念經文——而且 statement 沒有超出 quotes 所說？')
LINK = COMMON + ('每個工作給兩條記錄（from、to）、連接類型（type：supports 支持、qualifies 限定）與理由，以及整段講課原文（section）。'
    '問題：在教授這段的論證裡，from 與 to 是否確實是這種關係、方向也對？')
GAP = ('你核對從王教授講課逐字稿整理出的記錄是否漏了內容。給你整段原文（section）、已存的逐字引文（fragments）與作者略過的段落及理由（skipped）。'
    '列出原文中承載教授論證的句子——他的結論、理由、對經文的觀察或解釋、例子——卻不在任何引文內、也不屬於合理略過的。'
    '念經文本身、轉場、口頭禪、重複、未展開的預告不必列。沒有就回傳空的 gaps。不要評價他的觀點。')


TERMS = ('和合本', '新譯本', '思高', '呂振中', '英文', '希臘文', '希伯來文', '亞蘭文', '語態', '被動', '主動', '時態', '不定過去式')
PASS = 'pass'
RULES = {
    'R1': ('one_assertion', '`statement` 是否只說一件可以單獨判斷真假的事？引用的一段經文算一件，除非經文的不同部分各有不同用途。',
           {PASS: '只說一件事。', 'fail': '說了兩件或更多可以分開判斷的事。', 'uncertain': '無法確定。'}),
    'R3': ('whole_used', '`link` 從 `from` 連到 `to`。這條連接是否用到 `from` 的全部內容，而不只其中一部分？',
           {PASS: '用到全部內容。', 'fail': '只用到其中一部分，其餘部分與這條連接無關。', 'uncertain': '無法確定。'}),
    'R4': ('no_repeat', '`from` 與 `to` 是否只是同一件事換句話說？',
           {PASS: '不是重複：兩者各說不同的事。', 'fail': '是重複：一條只是另一條換句話說。', 'uncertain': '無法確定。'}),
    'R5': ('reading_only', '`statement` 是否只說教授所念經文（或原文）本身的內容，不含教授自己的解釋、強調或評論？',
           {PASS: '只有經文本身的內容。', 'fail': '含有教授自己的話。', 'uncertain': '無法確定。'}),
}
RULE_GPT = COMMON + ('每個工作給一條結構規則（rule）、要核對的記錄（item）與整段講課原文（section）。'
    '問題：這記錄是否符合這條規則？')


def program_checks(section, draft):
    paragraphs = section['paragraphs']
    out = []
    for fid, fr in draft['fragments'].items():
        ok = fr['text'] in paragraphs.get(fr['paragraph_key'], '')
        out.append({'piece': fid, 'check': 'verbatim', 'verdict': 'pass' if ok else 'fail',
                    'problem': '' if ok else '不是該段原文的逐字片段'})
    for kind in ('observations', 'evidence', 'claims'):
        for rid, rec in draft[kind].items():
            missing = [x for x in rec['fragments'] if x not in draft['fragments']]
            if missing:
                out.append({'piece': rid, 'check': 'fragments_exist', 'verdict': 'fail', 'problem': f'缺片段 {missing}'})
    linked = {l['from'] for l in draft['links']}
    for rid in [*draft['observations'], *draft['evidence']]:
        ok = rid in linked
        out.append({'piece': rid, 'check': 'R2_has_link', 'verdict': 'pass' if ok else 'fail',
                    'problem': '' if ok else '沒有任何連接'})
    for kind in ('observations', 'evidence', 'claims'):
        for rid, rec in draft[kind].items():
            said = ''.join(draft['fragments'][x]['text'] for x in rec['fragments'] if x in draft['fragments'])
            extra = [t for t in TERMS if t in rec['statement'] and t not in said]
            out.append({'piece': rid, 'check': 'R6_terms_in_quotes', 'verdict': 'fail' if extra else 'pass',
                        'problem': f'引文沒有：{"、".join(extra)}' if extra else ''})
    # Every explicit citation in the section must sit in some fragment (#702 rule).
    listing = cit.build({}, {section['source_id']: {'paragraphs': {k: {'text': t} for k, t in paragraphs.items()}}},
                        section['source_id'], Path(section['work']) / 'citations', None)
    for c in listing['citations']:
        texts = [fr['text'] for fr in draft['fragments'].values() if fr['paragraph_key'] == c['paragraph_key']]
        ok = any(tags.holds(c, t, cit.default_bible()) for t in texts)
        out.append({'piece': f"{c['paragraph_key']} {c['ref']}", 'check': 'citation_in_fragment',
                    'verdict': 'pass' if ok else 'fail', 'problem': '' if ok else '引用不在任何片段內：' + c['quote'][:60]})
    return out


def model_checks(section, draft, ports, root, previous):
    paragraphs = section['paragraphs']
    full = '\n'.join(f'{k} {t.strip()}' for k, t in paragraphs.items() if t.strip())
    quotes = lambda ids: [draft['fragments'][x]['text'] for x in ids]
    context = lambda ids: {draft['fragments'][x]['paragraph_key']: paragraphs[draft['fragments'][x]['paragraph_key']] for x in ids}
    every = {**draft['observations'], **draft['evidence'], **draft['claims']}
    tasks = [
        ('statement_fidelity', STATEMENT, VERDICT,
         [{'job_id': r, 'statement': x['statement'], 'quotes': quotes(x['fragments']), 'context': context(x['fragments'])}
          for r, x in {**draft['observations'], **draft['evidence']}.items()]),
        ('claim_is_his', CLAIM, VERDICT,
         [{'job_id': r, 'statement': x['statement'], 'quotes': quotes(x['fragments']), 'section': full}
          for r, x in draft['claims'].items()]),
        ('link_holds', LINK, VERDICT,
         [{'job_id': f"{l['from']}->{l['to']}", 'from': every[l['from']]['statement'], 'to': every[l['to']]['statement'],
           'type': l['type'], 'reason': l['reason'], 'section': full} for l in draft['links']]),
        ('coverage_gaps', GAP, GAPS,
         [{'job_id': 'section', 'section': full, 'fragments': [x['text'] for x in draft['fragments'].values()],
           'skipped': draft.get('skipped', [])}]),
    ]
    out, asked = [], []
    for task, instruction, schema, jobs in tasks:
        # A piece unchanged since the last round keeps its result; only changed pieces are asked again.
        for job in jobs:
            old = previous.get((task, job['job_id']))
            if old and old['job'] == job:
                out += [{**r, 'inherited': True} for r in old['results']]
        jobs = [j for j in jobs if not (previous.get((task, j['job_id'])) or {}).get('job') == j]
        asked += [{'task': task, 'job': j} for j in jobs]
        if not jobs:
            continue
        for batch, reply in f.gpt(Path(root) / task, ports, task, jobs, schema, instruction, semantic_review=False):
            for item in reply['items']:
                job = next(j for j in batch if j['job_id'] == item['job_id'])
                if task == 'coverage_gaps':
                    results = [{'piece': g['paragraph_key'], 'check': task, 'verdict': 'fail', 'problem': g['sentence'] + ' —— ' + g['why']}
                               for g in item['gaps']] or [{'piece': 'section', 'check': task, 'verdict': 'pass', 'problem': ''}]
                else:
                    results = [{'piece': item['job_id'], 'check': task, 'verdict': item['verdict'], 'problem': item['problem']}]
                out += results
                previous[(task, item['job_id'])] = {'job': job, 'results': results}
    return out, asked, previous


def rule_checks(section, draft, ports, root):
    """Jev asks each rule question; anything Jev does not pass goes to GPT, whose verdict stands."""
    paragraphs = section['paragraphs']
    full = '\n'.join(f'{k} {t.strip()}' for k, t in paragraphs.items() if t.strip())
    quotes = lambda ids: [draft['fragments'][x]['text'] for x in ids]
    every = {**draft['observations'], **draft['evidence'], **draft['claims']}
    items = []  # (rule, piece, state)
    for rid, x in every.items():
        items.append(('R1', rid, {'statement': x['statement'], 'quotes': quotes(x['fragments'])}))
    for rid, x in draft['observations'].items():
        items.append(('R5', rid, {'statement': x['statement'], 'quotes': quotes(x['fragments'])}))
    for l in draft['links']:
        pair = {'from': every[l['from']]['statement'], 'to': every[l['to']]['statement'],
                'link': {'type': l['type'], 'reason': l['reason']}, 'from_quotes': quotes(every[l['from']]['fragments'])}
        items.append(('R3', f"{l['from']}->{l['to']}", pair))
        items.append(('R4', f"{l['from']}->{l['to']}", pair))
    out, flagged = [], []
    for rule, piece, state in items:
        name, text, criteria = RULES[rule]
        answer = f.jev(Path(root) / 'jev' / rule, ports, state, {'Q0': {'type': 'choice', 'instructions': text, 'criteria': criteria}})['Q0']
        if answer == PASS:
            out.append({'piece': piece, 'check': f'{rule}_{name}', 'verdict': 'pass', 'problem': '', 'by': 'jev'})
        else:
            flagged.append({'job_id': f'{rule}|{piece}', 'rule': text + ' 符合＝' + criteria[PASS], 'item': state, 'section': full})
    if flagged:
        for _, reply in f.gpt(Path(root) / 'rules', ports, 'structure_rules', flagged, VERDICT, RULE_GPT, semantic_review=False):
            for item in reply['items']:
                rule, piece = item['job_id'].split('|', 1)
                out.append({'piece': piece, 'check': f'{rule}_{RULES[rule][0]}', 'verdict': item['verdict'],
                            'problem': item['problem'], 'by': 'gpt (jev flagged)'})
    return out, len(items), len(flagged)


def main():
    load_dotenv('.env')
    work, n = Path(sys.argv[1]), sys.argv[2]
    section = {**json.loads((work / 'section.json').read_text()), 'work': str(work)}
    draft = json.loads((work / 'rounds' / f'draft-{n}.json').read_text())
    checks, asked = program_checks(section, draft), []
    memory = work / 'rounds' / 'answered.json'
    previous = {tuple(k.split('|', 1)): v for k, v in json.loads(memory.read_text()).items()} if memory.exists() else {}
    if all(c['verdict'] == 'pass' for c in checks if c['check'] in ('verbatim', 'fragments_exist')):
        ports, _ = flow.subscription_ports(work / 'gpt-root', ClaimModelCapacity(json.loads(Path(CAPACITY).read_text())))
        found, asked, previous = model_checks(section, draft, ports, work / 'gpt-root', previous)
        checks += found
        ruled, n_jev, n_gpt = rule_checks(section, draft, ports, work / 'gpt-root')
        checks += ruled
        print(n_jev, 'Jev rule questions;', n_gpt, 'sent to GPT')
        memory.write_text(json.dumps({'|'.join(k): v for k, v in previous.items()}, ensure_ascii=False, indent=1))
    (work / 'rounds' / f'checks-{n}.json').write_text(json.dumps({'checks': checks, 'asked': asked}, ensure_ascii=False, indent=1))
    print(len(asked), 'pieces asked this round')
    for c in checks:
        if c['verdict'] != 'pass':
            print(c['check'], c['piece'], c['problem'])
    print(sum(c['verdict'] == 'pass' for c in checks), 'pass /', len(checks))


if __name__ == '__main__':
    main()
