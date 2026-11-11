"""Record the visually inspected uniform survey, before detector execution.

This is a provenance fixture, not an automatic ground-truth generator.
"""
from pathlib import Path
from backend.pipeline.slide_change_pilot import read_json, save_json, file_sha

OUT = Path('output/slide-pilot/s210307-first15m')
OBSERVATIONS = [
    (0, 2080, 'new_slide', '首次可見投影片，另計初始取得'),
    (214240, 216320, 'new_slide', '編輯模式換到 ζηλοῦτε 語氣說明'),
    (237120, 239200, 'build', '播放模式由空白顯示 ζηλοῦτε 標題'),
    (243360, 245440, 'build', 'Indicative mood 記述式'),
    (262080, 264160, 'build', '你們切慕'),
    (274560, 276640, 'build', 'Imperative mood 命令式'),
    (278720, 280800, 'build', '你們要切慕'),
    (307840, 309920, 'new_slide', '林前14:12 經文'),
    (322400, 324480, 'build', '林前14:12 標頭'),
    (397280, 399360, 'build', '哥林多人不會切慕聖靈和邪靈'),
    (407680, 409760, 'build', 'πνεῦμα 靈=屬靈的恩賜'),
    (443040, 445120, 'build', '讓步式的命令'),
    (497120, 499200, 'new_slide', '林前12:1 關於屬靈的'),
    (534560, 536640, 'build', 'χάρισμα 恩賜'),
    (596960, 599040, 'new_slide', '毛匪要解放台灣'),
    (688480, 690560, 'build', '中央日報'),
    (694720, 696800, 'build', '蔣總統要反攻大陸'),
    (709280, 711360, 'build', '人民日報'),
    (736320, 738400, 'build', '毛主席要解放台灣'),
    (771680, 773760, 'build', '另一行中央日報'),
    (775840, 777920, 'build', '蔣幫要反攻大陸'),
    (794560, 796640, 'build', '另一行人民日報'),
    (807040, 809120, 'build', '毛幫要解放台灣'),
    (869440, 871520, 'new_slide', '切慕屬靈的恩賜及黃色說明'),
    (873600, 875680, 'build', '林前14:1 標頭'),
]

if __name__ == '__main__':
    target = OUT / 'reference.json'
    if target.exists() or (OUT / 'candidates.json').exists():
        raise SystemExit('Reference already frozen or detector already run')
    manifest = read_json(OUT / 'manifest.json')
    reference = {
        'schema_version': 'slide-visual-reference.v1',
        'source_sha256': manifest['identity']['source_sha256'],
        'manifest_sha256': file_sha(OUT / 'manifest.json'),
        'annotation_method': 'Codex visual inspection of all 15 uniform survey pages before detection',
        'human_confirmed': False,
        'survey_index_sha256': file_sha(OUT / 'survey/index.json'),
        'survey_page_sha256': {p: file_sha(OUT / p) for p in read_json(OUT / 'survey/index.json')['pages']},
        'limitations': ['Not independently confirmed by a human coworker',
                        'Uniform survey spacing is 2080ms: short events may be missed; boundary is an interval',
                        'One selected development clip, not a held-out generalization benchmark'],
        'excluded': ['Temporary blank backgrounds without readable content', 'Cursor and PowerPoint navigation UI'],
        'events': [{'id': f'r-{i:03d}', 'earliest_ms': low, 'latest_ms': high,
                    'kind': kind, 'note': note, 'initial_acquisition': i == 0}
                   for i, (low, high, kind, note) in enumerate(OBSERVATIONS)]}
    save_json(target, reference)
    save_json(Path(__file__).with_name('s210307_first15m_reference.json'), reference)
    print({'reference_sha256': file_sha(target), 'events': len(reference['events'])})
