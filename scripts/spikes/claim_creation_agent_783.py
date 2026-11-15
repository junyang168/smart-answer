"""#783 spike: check an agent-authored draft piece by piece. Writes no database.

    PYTHONPATH=. backend/.venv/bin/python scripts/spikes/claim_creation_agent_783.py <work> <round>

Reads <work>/section.json and <work>/rounds/draft-<round>.json; writes
<work>/rounds/checks-<round>.json. GPT questions and answers are saved under
<work>/gpt-root and replay on a rerun. The author (Claude Code) never checks
its own pieces: the program checks text, GPT answers one question per piece.
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
        memory.write_text(json.dumps({'|'.join(k): v for k, v in previous.items()}, ensure_ascii=False, indent=1))
    (work / 'rounds' / f'checks-{n}.json').write_text(json.dumps({'checks': checks, 'asked': asked}, ensure_ascii=False, indent=1))
    print(len(asked), 'pieces asked this round')
    for c in checks:
        if c['verdict'] != 'pass':
            print(c['check'], c['piece'], c['problem'])
    print(sum(c['verdict'] == 'pass' for c in checks), 'pass /', len(checks))


if __name__ == '__main__':
    main()
