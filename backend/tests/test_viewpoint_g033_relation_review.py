from copy import deepcopy
import pytest
from backend.pipeline.viewpoint_g033_relation_review import RELATIONS, validate_choices, Review

def case():
    context={'sources':[{'source_id':'OWN','paragraphs':[{'paragraph_key':'S0001','text':'鹽和光講門徒的使命。鹽能調味，也能防腐。'}]}]}
    proposal={'relations':[{'relation_id':rid,'disposition':'proposed_relation','relation_type':'specializes',
        'reason':'synthetic source-bound test only','quotes':[{'source_id':'OWN','paragraph_key':'S0001','exact_text':'鹽和光講門徒的使命。'}]} for rid in sorted(RELATIONS)]}
    return context,proposal

def test_exact_quotes_population_and_nonmutation():
    c,p=case(); before=deepcopy((c,p)); assert validate_choices(c,p)==p; assert (c,p)==before

@pytest.mark.parametrize('disposition',['no_supported_relation','needs_new_scope'])
def test_unsupported_relation_is_not_retirement(disposition):
    c,p=case()
    for row in p['relations']: row.update(disposition=disposition,relation_type=None)
    assert validate_choices(c,p)==p
    assert all('effective_state' not in row for row in p['relations'])

@pytest.mark.parametrize('bad',['missing','duplicate','foreign','quote','source','paragraph','empty_reason','empty_quote','empty_quotes','type_missing','type_on_hold','new_endpoint','claim_edit'])
def test_drift_and_scope_refused(bad):
    c,p=case(); row=p['relations'][0]
    if bad=='missing': p['relations'].pop()
    if bad=='duplicate': p['relations'][1]=deepcopy(row)
    if bad=='foreign': row['relation_id']='FOREIGN'
    if bad=='quote': row['quotes'][0]['exact_text']='Salt equals light'
    if bad=='source': row['quotes'][0]['source_id']='FOREIGN'
    if bad=='paragraph': row['quotes'][0]['paragraph_key']='S0999'
    if bad=='empty_reason': row['reason']=' '
    if bad=='empty_quote': row['quotes'][0]['exact_text']=''
    if bad=='empty_quotes': row['quotes']=[]
    if bad=='type_missing': row['relation_type']=None
    if bad=='type_on_hold': row['disposition']='needs_new_scope'
    if bad=='new_endpoint': row['target_viewpoint_id']='NEW'
    if bad=='claim_edit': row['claim_statement']='changed'
    with pytest.raises(ValueError): validate_choices(c,p)

def test_review_rejects_extra_write_instructions():
    with pytest.raises(ValueError):
        Review.model_validate({'verdicts':[],'apply':True})
