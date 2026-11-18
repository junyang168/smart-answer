"""Publish a reviewed listening plan and its evaluated chapter summary together.

Prepare against the production data root and installed, inactive slide bundles.
Use --apply with the saved result to verify that nothing has changed and switch
the chapter with one atomic write.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from backend.pipeline import chapter_listening_catalog, chapter_listening_units, chapter_title
from backend.pipeline.chapter_slide_claims import digest, RecordedClient

SCHEMA = 'chapter-unit-summary.v1'
TITLE_PROMPT = """為王教授的馬太福音逐節聆聽目錄寫一句繁體中文摘要，最多 32 字。
按順序涵蓋每個標為 Unit 的主要主題，使用能讓讀者辨認題材的具體詞，可以合併相近主題。舊版保留段落可在需要時補充。
只根據聆聽清單內有投影片的材料。區分本段解釋的主題與引用作證據的經文。
如果同一 Unit 連接兩章，保留經文跨度，例如「太6:8–7:11」，並概括這個論證。
不用「教授認為」，不加句號、評語或作者的新觀點。"""
EVAL_PROMPT = """獨立審核書卷目錄上的一句章摘要。輸入的 title 就是完整待審摘要，沒有另外的正文。
逐一檢查每個標為 Unit 的主要主題是否可從摘要辨認；相近主題可以合在一起，
但不能只寫一個籠統標籤來掩蓋缺漏。核對跨章 Unit 的範圍是否如實標示。
摘要中的每一項都要有上線材料支持；舊版保留段落也可以支持摘要。
列出每個 Unit 的 id、是否涵蓋及理由。若摘要夾帶下次預告、將引用當主要主題、
錯述跨章範圍或誤表教授的立場，列入 problems。沒有問題才返回空清單。"""
EVAL_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['units', 'problems'],
    'properties': {
        'units': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'covered', 'reason'],
            'properties': {'id': {'type': 'string'}, 'covered': {'type': 'boolean'},
                           'reason': {'type': 'string'}}}},
        'problems': {'type': 'array', 'items': {'type': 'string'}},
    },
}


def path_for(data_root: Path, passage: str) -> Path:
    return data_root / 'chapter_exposition' / passage / 'listening-units.json'


def plan_sha(plan):
    return digest({k: v for k, v in plan.items() if k != 'chapter_summary'})


def summary_material(index, plan):
    rows = {}
    for section in index['sections']:
        passage = chapter_listening_catalog.parent_passage(section)
        proposed = plan['units'].get(passage)
        plays = [{'title': slide['title'], 'points': [item['text'] for item in slide['points']]}
                 for recording in section['sermons'] if recording['seconds'] > 0
                 for slide in recording.get('slides', [])]
        if plays:
            row = rows.setdefault(passage, {'id': passage, 'unit': proposed is not None,
                            'heading': proposed['title'] if proposed else section['label'], 'subunits': []})
            row['subunits'].append({'title': section['label'], 'slides': plays})
    return list(rows.values())


def check_summary(title, material, evaluation):
    problems = chapter_title.check(title)
    wanted = {row['id'] for row in material if row['unit']}
    rows = evaluation['units']
    seen = {row['id'] for row in rows}
    if not wanted or seen != wanted or len(rows) != len(seen):
        problems.append('Eval must assess each published Unit exactly once')
    if any(row['covered'] is not True or not row['reason'].strip() for row in rows):
        problems.append('Eval found an omitted Unit or missing reason')
    for row in material:
        if row['unit'] and '-to-' in row['id']:
            _, start_ch, start, _, end_ch, end = row['id'].split('-')
            if f'{start_ch}:{start}–{end_ch}:{end}' not in title.replace('-', '–'):
                problems.append(f'Cross-chapter range missing: {row["id"]}')
    return problems + evaluation['problems']


def _project(data_root, passage, plan):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import chapter_topics, original_audio_index
    from backend.pipeline.chapter_listening_eval import playback_card_problems

    legacy = chapter_topics.attach(
        original_audio_index.build_index(PostgresKnowledgeStore(), data_root, passage), data_root)
    index = chapter_listening_catalog.compose(
        legacy, chapter_listening_units.build(data_root, passage, plan=plan))
    if problems := playback_card_problems(index):
        raise ValueError(f'Listening card Eval did not pass: {problems}')
    material = summary_material(index, plan)
    if {row['id'] for row in material if row['unit']} != plan['units'].keys():
        raise ValueError('Every Unit must have playable reviewed slides')
    return material


def prepare(data_root, passage, plan, client):
    path = path_for(data_root, passage)
    before = digest(json.loads(path.read_text())) if path.exists() else None
    material = _project(data_root, passage, plan)
    input_text = json.dumps(material, ensure_ascii=False)
    title = client.generate_json(TITLE_PROMPT, input_text, chapter_title.RESPONSE_SCHEMA)['title'].strip().rstrip('。')
    eval_input = {'title': title, 'material': material}
    evaluation = client.generate_json(EVAL_PROMPT, json.dumps(eval_input, ensure_ascii=False), EVAL_SCHEMA)
    chapter = chapter_title.chapters(data_root)[passage]
    return {'schema_version': SCHEMA, 'passage': passage, 'previous_plan_sha256': before,
            'plan_sha256': plan_sha(plan), 'material_sha256': digest(material),
            'legacy_slides_sha256': chapter_title._slides_shas(chapter, data_root),
            'title': title, 'evaluation': evaluation, 'eval_input_sha256': digest(eval_input),
            'problems': check_summary(title, material, evaluation)}


def publish(data_root, passage, plan, summary):
    path = path_for(data_root, passage)
    before = path.read_bytes() if path.exists() else None
    if (summary['schema_version'] != SCHEMA or summary['passage'] != passage
            or summary['plan_sha256'] != plan_sha(plan)
            or summary['previous_plan_sha256'] != (digest(json.loads(before)) if before else None)):
        raise ValueError('Summary approval belongs to a different listening plan')
    material = _project(data_root, passage, plan)
    chapter = chapter_title.chapters(data_root)[passage]
    if (digest(material) != summary['material_sha256']
            or summary['eval_input_sha256'] != digest({'title': summary['title'], 'material': material})
            or summary['legacy_slides_sha256'] != chapter_title._slides_shas(chapter, data_root)):
        raise ValueError('Playable slides changed after summary Eval')
    problems = check_summary(summary['title'], material, summary['evaluation'])
    if problems or summary['problems']:
        raise ValueError(f'Summary Eval did not pass: {problems or summary["problems"]}')
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.listening-units.', delete=False) as file:
            staged = Path(file.name)
            file.write(json.dumps({**plan, 'chapter_summary': summary}, ensure_ascii=False, indent=2).encode())
            os.fchmod(file.fileno(), path.stat().st_mode & 0o777 if path.exists() else 0o644)
        if (path.read_bytes() if path.exists() else None) != before:
            raise ValueError('Live listening plan changed while publishing')
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def load_summary(data_root, passage):
    path = path_for(data_root, passage)
    if not path.is_file():
        return None
    plan = json.loads(path.read_text())
    summary = plan.get('chapter_summary')
    if (summary and summary.get('schema_version') == SCHEMA and summary.get('passage') == passage
            and summary.get('plan_sha256') == plan_sha(plan)
            and not summary.get('problems') and not chapter_title.check(summary['title'])):
        chapter = chapter_title.chapters(data_root).get(passage)
        if chapter and summary['legacy_slides_sha256'] == chapter_title._slides_shas(chapter, data_root):
            return summary['title']
    return None


def main():
    from backend.api.config import DATA_BASE_PATH
    from backend.pipeline.chapter_exposition import chapter_client

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('passage')
    parser.add_argument('plan', type=Path)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--model', default=chapter_title.DEFAULT_MODEL)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    data_root = Path(DATA_BASE_PATH)
    if args.apply:
        publish(data_root, args.passage, plan, json.loads(args.receipt.read_text()))
    else:
        if args.receipt.exists():
            parser.error('The receipt already exists')
        result = prepare(data_root, args.passage, plan, RecordedClient(
            chapter_client(args.model), args.receipt.parent / (args.receipt.stem + '-calls')))
        with args.receipt.open('x') as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
        print(json.dumps({'title': result['title'], 'problems': result['problems']}, ensure_ascii=False))
        return int(bool(result['problems']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
