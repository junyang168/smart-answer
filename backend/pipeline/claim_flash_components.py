"""Source-blind statement decomposition followed by source-bound Jev checks."""
from copy import deepcopy
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline import claim_flash_common as f, evidence_refresh as e
from backend.pipeline import claim_flash_questions as jevq

CONTRACT = 'claim_flash_components_v2'
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


def check(root, ports, record_type, record, material, components, *, fragment_only=False):
    # Do not let existing classifications answer their own verification questions.
    visible = {k:v for k,v in record.items() if k in {'statement','text','title','scripture_refs'}}
    state = {'record_type': record_type, 'record': visible,
             'original_text_excerpts': material['original_text_excerpts'],
             **({} if fragment_only else {'source_context': {p['paragraph_key']:p['text'] for p in material.get('nearby_paragraphs',[])}})}
    questions = {'record_fidelity': jevq.record_fidelity()}
    if components is not None:
        state['components'] = {str(i): c for i, c in enumerate(components)}
        questions['decomposition_fidelity'] = jevq.decomposition()
        for i,item in enumerate(components):
            questions['component_'+str(i)] = jevq.component(item)
            if fragment_only:
                questions['component_'+str(i)]['instructions'] = questions['component_'+str(i)]['instructions'].replace(
                    '`source_context` 只消歧，不補未掛載的論據。','只用所列SourceFragments，不補未掛載的論據。')
    text=record.get('statement',record.get('text',record.get('title','')))
    for field in jevq.ROLES:
        if record.get(field):
            questions[field+'_classification'] = jevq.role(field,text,speaker=record.get('speaker'))
    request = {'model': e.MODEL, 'state': state, 'questions': questions}
    if len(json.dumps(request, ensure_ascii=False).encode()) > f.JEV_BYTES:
        raise ValueError('complete component check exceeds Flash payload limit; no call issued')
    answers = f.jev(root, ports, state, questions)
    for field in jevq.ROLES:
        if field+'_classification' in answers:
            chosen=answers[field+'_classification']
            answers[field+'_consistency']=('uncertain' if chosen in {'uncertain','mixed'} else
                                          'matched' if chosen==record[field] else 'different')
    return answers


def needs_disposition(answers):
    return any(v != ('supported' if k.startswith('component_') else
                     'matched' if k.endswith('_consistency') else 'faithful')
               for k,v in answers.items() if not k.endswith('_classification'))
