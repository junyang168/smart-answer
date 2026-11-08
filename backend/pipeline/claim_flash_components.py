"""Source-blind statement decomposition followed by source-bound Jev checks."""
from copy import deepcopy
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline import claim_flash_common as f, evidence_refresh as e

CONTRACT = 'claim_flash_components_v1'
RECORDS_PER_JOB = 4
PROMPT = (
    '本次只拆解 statement，不審真假，不修訂，不判錯，不找論據。沒有原文和舊審閱意見。'
    '每個並列實質成分都要保留，包括並列動詞、對象、否定、程度、模態和適用範圍。'
    '只按原句拆，不替概括詞枚舉原句沒列出的例子或業務，不補常識或猜錯字。'
    '每個 component 保留足夠上下文可單獨理解，但不得加強或減弱原句。'
    '條件或推論須保留整個關係；拆出其中的行為時用 embedded_description，'
    '不可把「若P則Q」拆成已發生P或已發生Q。應／可以／如果願意等原有語氣不得互換。'
    '單一簡單判斷可只有一項，不為拆而拆。不得給支持判定或修正建議。'
    '每個 job_id、object_key 恰好回覆一次，只輸出 role、text，無理由或其他欄位。')


class Component(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    role: Literal['judgment', 'conditional_relation', 'evidential_relation',
                  'normative_recommendation', 'embedded_description']
    text: str = Field(min_length=1)


class Parts(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    object_key: str
    components: list[Component] = Field(min_length=1)


class Bundle(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str
    items: list[Parts] = Field(min_length=1)


class Bundles(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    items: list[Bundle]


def decompose(root, ports, statements):
    """Only text is sent; identical statements share decomposition, not source verdicts."""
    aliases = {}
    for key, text in sorted(statements.items()):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('empty statement to decompose: '+key)
        aliases.setdefault(text, []).append(key)
    nodes = [{'object_key': keys[0], 'statement': text} for text, keys in aliases.items()]
    jobs, batch = [], []
    for node in nodes:
        if batch and (len(batch) >= RECORDS_PER_JOB or
                      len(json.dumps(batch+[node], ensure_ascii=False).encode()) > 3000):
            jobs.append({'job_id': 'parts-'+str(len(jobs)), 'records': batch});batch=[]
        batch.append(node)
    if batch:
        jobs.append({'job_id': 'parts-'+str(len(jobs)), 'records': batch})
    result = {}
    for batch, raw in f.gpt(Path(root)/'components', ports, 'lower_decomposition', jobs,
                            Bundles.model_json_schema(), PROMPT, semantic_review=False):
        replies = Bundles.model_validate(raw).items
        f.exactly([x.model_dump() for x in replies], [j['job_id'] for j in batch], 'job_id')
        for reply in replies:
            job = next(j for j in batch if j['job_id'] == reply.job_id)
            f.exactly([x.model_dump() for x in reply.items],
                      [n['object_key'] for n in job['records']], 'object_key')
            for item in reply.items:
                parts = [c.model_dump() for c in item.components]
                if any(not c['text'].strip() for c in parts) or len({c['text'] for c in parts}) != len(parts):
                    raise ValueError('empty or duplicate component')
                text = statements[item.object_key]
                for alias in aliases[text]:
                    result[alias] = deepcopy(parts)
    if set(result) != set(statements):
        raise ValueError('decomposition scope differs')
    e.save(Path(root)/'components.json', result)
    return result


def check(root, ports, record_type, record, material, components):
    state = {'record_type': record_type, 'record': record,
             'original_text_excerpts': material['original_text_excerpts'],
             'source_context': {p['paragraph_key']:p['text'] for p in material.get('nearby_paragraphs',[])}}
    questions = {'record_fidelity': f.yn(
        '本記錄是否有任何實質內容、歸屬或經文用途不受完整原文清單支持？'
        'Question 只核對提問，不檢查 answer_claim_ids；不要求支持材料成為獨立 Claim。')}
    if components is not None:
        state['components'] = {str(i): c for i, c in enumerate(components)}
        questions['decomposition_fidelity'] = f.yn(
            'components 是否漏掉、增加或改動 record.statement 的實質意思、條件、模態或範圍？'
            '只與 statement 比，不用原文修正拆分；embedded_description 不聲稱特定行為已發生。')
        questions['attribution_fidelity'] = f.yn(
            'speaker／stance 與 statement 表達的判斷及來源是否矛盾？'
            '教授採納的否定判斷本身不能因包含反方內容就標成被反對的判斷。')
        for i in state['components']:
            questions['component_'+i] = f.c.question(
                '只判 components.'+i+'。完整 original_text_excerpts 是否共同支持這項的全部實質內容？'
                '保留條件／推論／建議語氣；embedded_description 核對條件描述，不要求證明行為已發生。'
                'source_context 只可解消指代或話題，不可補未掛載的實質前提、答案、行為或條件。'
                '允許忠實同義概括，不以其他 component 正確代替本項支持，不補外部常識。',
                {'supported': '全部實質內容有據。', 'unsupported': '至少一個實質成分無據，不必明確相反。',
                 'contradicted': '原文明確相反。', 'uncertain': '材料含混或不足。'})
    request = {'model': e.MODEL, 'state': state, 'questions': questions}
    if len(json.dumps(request, ensure_ascii=False).encode()) > f.JEV_BYTES:
        raise ValueError('complete component check exceeds Flash payload limit; no call issued')
    return f.jev(root, ports, state, questions)


def needs_disposition(answers):
    return any(v != ('supported' if k.startswith('component_') else 'no')
               for k, v in answers.items())
