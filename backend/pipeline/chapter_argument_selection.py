"""Select complete top-level argument topics before compiling their source ranges."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.pipeline import chapter_exposition as exposition
from backend.pipeline.source_projection import project_script

RULES = '''依論證層為目標經文選材，一個頂層論證主題（group）是一個不可拆開的選材單位。
先理解本組在回答什麼問題、主要結論、論據怎樣接到結論，再判斷整組是否服務於 target_section。
- direct：整組直接解釋目標經文，保留完整主題及其論據。
- context：整組提供理解目標經文所需的背景或整體結構，整組保留為引言／背景。即使講到目標範圍以外的經文，也不得從組內切掉。
- omit：整組在回答別的問題，不納入目標小節。
經文清單包含被解釋的經文和用作證據的經文，不可只憑章節相交或起點決定。
整體結構主題如「先彌賽亞身份，再門徒使命」只可整組保留或整組排除，不能只保留講八福的半段。
同樣，若一組在解釋七章十二節、十三至二十七節、二十八至二十九節的三層結論，不因它提到登山寶訓或五章就放入八福。
context 的取捨是編輯提案：editor_review 寫明需同工確認的具體整組取捨；沒有疑義則空字串。
不修改論點、不重新生成論據、不刪改原文，不以其他組的相似主題取代本組。
每組恰好返回一次 group_id、placement、reason、editor_review，不提供句子／片段級的刪選。'''
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
                       'text': para['text'], 'argument_group_id': owners[key]})
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
    return {'passed': all(c['pass'] for c in checks), 'checks': checks,
            'held_for_later': case.get('held_for_later')}


def decide(chapter, target_section, *, conn, change_set_id, data_base_dir, client, lecture):
    from backend.pipeline.chapter_slide_claims import instance_records, RecordedClient, digest
    records, meta = instance_records(conn, change_set_id)
    topics, paragraphs, owners, document = topic_material(records, meta)
    if document["title"] != lecture:
        raise ValueError("ChangeSet 與指定講道不同")
    root = Path(data_base_dir).parent / 'selection'
    request = {'target_section': target_section, 'lecture': document['title'], 'topics': topics}
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
