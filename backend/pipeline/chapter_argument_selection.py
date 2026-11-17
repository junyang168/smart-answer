"""Select complete top-level argument topics before compiling their source ranges."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.pipeline import chapter_exposition as exposition
from backend.pipeline.source_projection import project_script

RULES = '''你在為 NYSC 馬太福音釋經系列的按章聆聽頁選材。頁面按經文次序組織；target_section 是本次要處理的和合本小段，chapter_sections 是既有分段。
輸入包括這一講完整頂層論證、Claim 的 scripture_refs、論據作用、對應原文及本章經文。讀完前後文，判斷教授藉這一整組在解釋哪段馬太福音，然後決定是否放入 target_section。

判斷順序：
1. 看教授正在回答什麼經文問題，以及理由和結論怎樣相連。scripture_refs 經常只記他用來作證據的經文；不能把它當主解經文，也不能只按書卷、章節相交或範圍起點判歸屬。
2. 引別卷書、問答、故事、原文字詞或新舊約背景，只要實際用來解釋目標小段，就是這段釋經的一部分。要連同完整論證保留，不按引用的書卷另分，也不能因原文轉讀別卷就判離題。反之，主題相似而沒有服務目標經文的獨立討論，整組排除。
3. 每個完整頂層論證組是最小取捨單位，只屬輸入的這一講。不可重新按題材分組、跨講合組，或把同一組分給幾個頁面。跨幾個小標題或經文小段仍是同一論證時，整組判歸屬；不要為了吻合 target_section 截取一半。
4. 使用 chapter_sections 的既有分段，不新增 overview 或使徒行傳等額外的選材分類。每個小段下仍保留各講自己的完整材料。已定的經文歸屬沿用，不能因為提到別處經文就改掉。
5. 一般問答須直接關乎目標經文才收；沒有實際解釋的預告不算已完成釋經。教授尚未回答的問題保留為未回答，不借下一講或一般知識補答案。

placement：
- direct：本組直接講解目標小段，整組保留。
- context：本組是解釋目標小段所需的完整背景、證據或結構引言，整組保留。它也是本段釋經材料。
- omit：本組屬另一個既定小段、獨立離題討論或尚未展開的預告，不納入本小節。

既定案例與判準：
- 2011 馬太福音釋經（二）第一講，開頭完整的「先彌賽亞身份，再門徒使命」結構說明，已定歸 5:1–12。即使後半講到 5:13–7:29 仍整組保留；其他目標小段不再重複取它。此項不再請人重新批准。
- 用雅各書或詩篇說明八福的溫柔，隨太5:1–12保留。用約、摩西律法與基督律法、徒15會議及四項禁戒，回答太5:17–20的律法問題時，整組作本段論據保留；按實際原文作用判斷，不把所有徒15討論都固定歸馬太。
- 若一組實際在說明七章十二節、十三至二十七節、二十八至二十九節的三層結論，不能僅因提到整個登山寶訓就放進八福。

reason 指出本組原文實際在回答的馬太經文問題，以及為何 direct/context/omit。editor_review 只記原文和既有規則仍不能決定的具體整組取捨；已定案例、跨卷引用或 context 分類本身不構成待人工理由，沒有疑義傳空字串。
每組恰好返回一次 group_id、placement、reason、editor_review。不提供句子／片段級刪選，不改論點、論據或原文。'''

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['groups'], 'properties': {
    'groups': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['group_id', 'placement', 'reason', 'editor_review'], 'properties': {
            'group_id': {'type': 'string'}, 'placement': {'type': 'string', 'enum': ['direct', 'context', 'omit']},
            'reason': {'type': 'string'}, 'editor_review': {'type': 'string'}}}}}}


def topic_material(records, meta):
    """Read every complete topic from the instance's bound generation run."""
    candidate = Path(meta['candidate_path'])
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != meta['candidate_sha256']:
        raise ValueError('instance candidate changed')
    result = json.loads((candidate.parent / 'creation-result.json').read_text())
    [document] = records['source_documents']
    source = Path(document['source_path'])
    projection = project_script(json.loads(source.read_text())['script'])
    paragraphs = {f'S{i + 1:04d}': p for i, p in enumerate(projection.body_rows)}
    topics, owners = [], {}
    by_short = {c['claim_id'].rsplit('-', 1)[-1]: c for c in records.get('claims', [])}
    for (gid, shorts), group in zip(result['group_claim_ids'].items(), result['groups'], strict=True):
        keys = []
        for span in group['paragraph_ranges']:
            first, last = int(span['first'][1:]), int(span['last'][1:])
            keys.extend(f'S{i:04d}' for i in range(first, last + 1))
        for key in keys:
            if key not in paragraphs or key in owners:
                raise ValueError(f'unknown or multiply owned paragraph: {key}')
            owners[key] = gid
        claims = [by_short[c] for c in shorts if c in by_short]
        ids = {c['claim_id'] for c in claims}
        evidence_ids = {eid for c in claims for eid in c.get('evidence_step_ids', [])}
        steps = [s for s in records.get('evidence_steps', [])
                 if s['evidence_step_id'] in evidence_ids or f'-P{len(topics)+1:02d}-' in s['evidence_step_id']]
        claims = [{k: c[k] for k in ('claim_id', 'title', 'statement', 'scripture_refs',
                   'evidence_step_ids', 'opposed_position_ids') if k in c} for c in claims]
        steps = [{k: step[k] for k in ('evidence_step_id', 'statement', 'discourse_role',
                  'produced_claim_ids', 'scripture_refs') if k in step} for step in steps]
        topics.append({'group_id': gid, 'title': group['title'], 'claims': claims,
                       'evidence_steps': steps,
                       'relations': [r for r in records.get('knowledge_relations', [])
                                     if r['to_id'] in ids or r['from_id'] in evidence_ids or r['to_id'] in evidence_ids],
                       'paragraphs': [{'key': k, 'text': paragraphs[k]['text']} for k in keys]})
    if set(owners) != set(paragraphs):
        raise ValueError('argument groups do not cover the bound source')
    return topics, paragraphs, owners, document


def compile_selection(chapter, target_section, topics, paragraphs, owners, answer, document, source_sha):
    """Expand one decision into every paragraph of its topic; no partial-group option."""
    if target_section not in chapter.sections:
        raise ValueError('target_section must be a configured chapter subsection')
    rows = answer.get('groups', [])
    decisions = {r['group_id']: r for r in rows}
    if len(decisions) != len(rows) or set(decisions) != {g['group_id'] for g in topics}:
        raise ValueError('one decision required for every complete topic')
    for row in rows:
        if set(row) != {'group_id', 'placement', 'reason', 'editor_review'}:
            raise ValueError('topic decision cannot contain paragraph or sentence selections')
        if row['placement'] not in {'direct', 'context', 'omit'} or not row['reason'].strip():
            raise ValueError('invalid topic placement or missing reason')
    pieces, expanded = [], []
    for key, para in paragraphs.items():
        if not para['text'].strip():
            continue
        row = decisions[owners[key]]
        uid = f'U{len(pieces)+1:03d}'
        pieces.append({'id': uid, 'start': float(para['start_time']), 'end': float(para['end_time']),
                       'text': para['text'], 'argument_group_id': owners[key],
                       'source_paragraph_key': key})
        keep = row['placement'] != 'omit'
        expanded.append({'id': uid, 'keep': keep, 'verses': [target_section] if keep else [],
                         'kind': '釋經' if keep else None, 'reason': row['reason'],
                         'argument_group_id': owners[key], 'placement': row['placement']})
    return {'schema_version': exposition.SCHEMA_VERSION, 'passage': chapter.passage,
            'target_section': target_section, 'lecture': document['title'], 'source_id': document['source_id'],
            'transcript_file': document['title'] + '.json', 'transcript_sha256': source_sha,
            'created_at': datetime.now(timezone.utc).isoformat(), 'pieces': pieces,
            'decisions': expanded, 'raw_decisions': rows, 'claims_by_piece': {},
            'selection_unit': 'complete_argument_topic',
            'editor_review': [r for r in rows if r['editor_review'].strip()]}


def evaluate(record, case):
    """Grade whole-topic placement separately from mechanical source expansion."""
    if (record['lecture'], record['target_section']) != (case['lecture'], case['target_section']):
        raise ValueError('Eval case does not match the selected lecture and passage')
    actual = {r['group_id']: r for r in record['raw_decisions']}
    checks = []
    for gid, expected in case['expected'].items():
        row = actual.get(gid)
        expanded = [d for d in record['decisions'] if d['argument_group_id'] == gid]
        pieces = [p for p in record['pieces'] if p['argument_group_id'] == gid]
        checks.append({'group_id': gid, 'expected': expected['accepted_placements'],
                       'actual': row['placement'] if row else None,
                       'pass': bool(row and row['placement'] in expected['accepted_placements']
                                    and len(expanded) == len(pieces) and expanded
                                    and all(d['keep'] == (row['placement'] != 'omit') for d in expanded)),
                       'meaning': expected['meaning']})
        if expected.get('editor_review_empty') and row and row['editor_review'].strip():
            checks[-1]['pass'] = False
            checks[-1]['unexpected_editor_review'] = row['editor_review']
    if 'expected_playback_runs' in case:
        from itertools import groupby
        sub_units = {s['group_id']: s['id'] for s in case['sub_units']}
        kept = [(p.get('source_paragraph_key'), sub_units.get(p['argument_group_id']))
                for p, d in zip(record['pieces'], record['decisions'], strict=True) if d['keep']]
        runs = [{'sub_unit_id': sid, 'paragraphs': [key for key, _ in rows]}
                for sid, rows in groupby(kept, key=lambda row: row[1])]
        checks.append({'meaning': 'Owner golden Sub-unit playback order and complete source coverage',
                       'actual': runs, 'expected': case['expected_playback_runs'],
                       'pass': runs == case['expected_playback_runs']})
    return {'passed': all(c['pass'] for c in checks), 'checks': checks,
            'held_for_later': case.get('held_for_later')}


def decide(chapter, target_section, *, conn, change_set_id, data_base_dir, client, lecture):
    from backend.pipeline.chapter_slide_claims import instance_records, RecordedClient, digest
    records, meta = instance_records(conn, change_set_id)
    topics, paragraphs, owners, document = topic_material(records, meta)
    if document["title"] != lecture:
        raise ValueError("ChangeSet 與指定講道不同")
    root = Path(data_base_dir).parent / 'selection'
    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.chapter_material import verse_range
    from backend.pipeline.original_audio_index import verse_texts
    book, number, low, high = verse_range(chapter.passage)
    request = {'series': chapter.series, 'chapter_sections': list(chapter.sections),
               'chapter_verses': verse_texts(DATA_BASE_PATH, book, number, low, high),
               'target_section': target_section, 'source_id': document['source_id'],
               'lecture': document['title'], 'topics': topics}
    frozen = {'system': RULES, 'request': request, 'schema': SCHEMA}
    root.mkdir(parents=True, exist_ok=True)
    request_path, answer_path = root / 'input.json', root / 'answer.json'
    if request_path.exists():
        if json.loads(request_path.read_text()) != frozen:
            raise ValueError('saved selection input changed; use a new explicit trial')
        if not answer_path.exists():
            raise ValueError('previous selection has no complete answer; inspect its saved call before retrying')
    request_path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2))
    if answer_path.exists():
        answer = json.loads(answer_path.read_text())
    else:
        answer = RecordedClient(client, root / 'calls').generate_json(
            RULES, json.dumps(request, ensure_ascii=False), SCHEMA)
        answer_path.write_text(json.dumps(answer, ensure_ascii=False, indent=2))
    source_sha = exposition.file_sha256(Path(document['source_path']))
    record = compile_selection(chapter, target_section, topics, paragraphs, owners, answer, document, source_sha)
    record.update({'instance_change_set': change_set_id, 'model': client.model,
                   'selection_input_sha256': digest(frozen), 'prompt_sha256': digest(RULES)})
    path = exposition.record_path(data_base_dir, chapter.passage, document['title'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record
