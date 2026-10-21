"""Offline two-sided calibration scorer. Never calls models or approves data.

Expected labels must not be given to an evaluated model. Unit tests verify the
scorer and call wiring, not whether GPT/Opus/Gemini actually understand cases.
"""
from copy import deepcopy
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def review_cases(dataset):
    if (dataset.get('schema_version') != 'wang_cvp_contextual_semantic_eval_v1'
            or dataset.get('synthetic_examples') is not True or dataset.get('not_professor_quotes') is not True):
        raise ValueError('calibration cases must be explicitly synthetic, not corpus evidence')
    rows=deepcopy(dataset['cases'])
    if not rows or len({r['id'] for r in rows})!=len(rows):
        raise ValueError('calibration case coverage')
    for row in rows:
        if row.pop('expected') not in {'valid','invalid','unresolved'}:
            raise ValueError('unknown calibration label')
    return {'synthetic_examples':True, 'not_professor_quotes':True, 'cases':rows}


def score(dataset, answers):
    review_cases(dataset)
    index={r['id']:r for r in dataset['cases']}
    if len(answers)!=len(index) or {a.get('id') for a in answers}!=set(index):
        raise ValueError('calibration answers must cover every case exactly once')
    false_accepts=[]; false_rejects=[]; unresolved_errors=[]
    for a in answers:
        row=index[a['id']]
        if (set(a)!={'id','verdict','quote','reason'} or a['verdict'] not in {'valid','invalid','unresolved'}
                or not a['quote'] or a['quote'] not in row['source'] or not a['reason'].strip()):
            raise ValueError('calibration answer lacks exact own-source quotation/reason')
        if a['verdict']!=row['expected']:
            target=false_accepts if a['verdict']=='valid' else false_rejects if row['expected']=='valid' else unresolved_errors
            target.append(row['id'])
    return {'dataset_sha256':sha256_json(dataset), 'answers_sha256':sha256_json(answers),
            'case_count':len(index), 'false_accepts':false_accepts, 'false_rejects':false_rejects,
            'unresolved_errors':unresolved_errors, 'passed':not (false_accepts or false_rejects or unresolved_errors),
            'production_approval':False}
