"""Explicit independent purpose verdicts, not inferred Evidence or edited raw."""
from copy import copy
from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import sha256_json

VERSION='wang_claim_citation_purpose_review_v1'
PROMPT='''本包新增 claim_citation_purpose_review，取代「所有診斷引文必須整句成為 Evidence」這項粗略要求，其他批准與來源門檻不變。
除了 proposal.dispositions 的全部 verdict，還須對該 receipt.items 的每個 record_key 各回一個 verdict。
此額外 verdict 的 approved=true 僅表示：exact_quote 中未實際綁定的其餘文字只是診斷背景，不是擬寫 Claim/Evidence 的新支持依據或獨立結論。
請獨立核對完整自身正文、实际 fragment/anchor/Evidence/Claim 後像；若該未綁文字確為支持主張所必需、或新 Evidence 聲稱它、或無法確定，approved=false 並具體說明。
不得改寫提案、增加 fragment 或把背景當 Evidence。所有新 Claim 必須由實際 eligible bound Evidence 支持；所有原 consumer verdict 仍完整。
此輪是原未執行的獨立審核，不是追加糾正；任何拒絕均不批准寫入。'''


def items(frozen, proposal, post):
    context=frozen.get('claim_issue_context',{})
    if not context.get('complete_input_repair'):
        return []
    from backend.pipeline.claim_issue_management import Issue,bound_fragments,HOLDS
    issues={Issue.model_validate(i).issue_id:i for i in context['issues']}
    result=[]
    for assessment in proposal.claim_assessments:
        if assessment.cause!='evidence_binding':continue
        claim=post[c.key('claims',issues[assessment.issue_id]['claim_id'])]
        usable=[post[c.key('evidence_steps',eid)] for eid in claim['evidence_step_ids']
            if post[c.key('evidence_steps',eid)].get('valid_for_identity_review') is not False
            and post[c.key('evidence_steps',eid)].get('support_eligibility') in {'eligible','eligible_candidate','eligible_with_label'}
            and post[c.key('evidence_steps',eid)].get('review_status') not in HOLDS]
        fragments=bound_fragments(usable,lambda fid:post[c.key('source_fragments',fid)])
        full=0;pending=[]
        for index,cite in enumerate(assessment.citations):
            own=[f for f in fragments if f['source_id']==cite.source_id and f['paragraph_key']==cite.paragraph_key]
            if any(cite.exact_quote in f['verbatim_excerpt'] for f in own):full+=1;continue
            prefixes=[f for f in own if f.get('verbatim_excerpt') and cite.exact_quote.startswith(f['verbatim_excerpt'])]
            if not prefixes:raise ValueError('unbound diagnosis lacks exact own eligible bound prefix')
            pending.append({'record_key':f'claim_citation_purpose/{assessment.issue_id}/{index}',
                'claim_id':claim['claim_id'],'citation':cite.model_dump(),
                'bound_prefixes':[{'fragment_id':f['fragment_id'],'exact_quote':f['verbatim_excerpt']} for f in prefixes],
                'requested_verdict':'diagnostic_context_only_not_new_evidence'})
        if pending and not full:raise ValueError('diagnostic purpose cannot replace actual bound supporting citations')
        result+=pending
    return result


def receipt(frozen, proposal, post):
    values=items(frozen,proposal,post)
    return {'schema_version':VERSION,'items':values,'automatic_content_approval':False,
            'proposal_unchanged':True,'unbound_quotes_are_not_evidence':True} if values else None


def scoped_adapter(adapter,packet):
    value=packet.get('claim_citation_purpose_review')
    if not value:return adapter
    if value.get('schema_version')!=VERSION or not packet.get('claim_issue_context',{}).get('complete_input_repair'):
        raise ValueError('citation purpose review requires bounded complete-input context')
    result=copy(adapter);result._base_prompt=adapter._base_prompt+'\n'+PROMPT
    result._prompt=adapter._prompt+'\n'+PROMPT
    result.prompt_sha256=sha256_json({'prompt':result._prompt})
    return result
