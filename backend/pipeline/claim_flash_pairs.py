"""Pair-only semantics. A relevant premise need not entail an entire Claim."""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.pipeline import claim_flash_common as f, evidence_refresh as e
from backend.api.canonical_repository.knowledge_models import knowledge_relation_endpoints
from backend.pipeline import claim_flash_questions as jevq

TYPES=('supports','explains','qualifies','refutes','contrasts','contextualizes','corroborates','answers','applies')


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    job_id: str
    action: Literal['set','remove','keep','needs_source']
    relation_type: str
    direction: Literal['a_to_b','b_to_a']
    reason: str=Field(min_length=1)


class Decisions(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    items: list[Decision]


def node(key,records,sources):
    p=records[key]['payload'];col=key.split('/')[0]
    result={'object_key':key,'record_type':col,'statement':p.get('statement',p.get('text',p.get('title',''))),
            'scripture_refs':p.get('scripture_refs',[]),'attribution':p.get('attribution'),
            'speaker':p.get('speaker'),'stance':p.get('stance')}
    if col in f.LOWER:
        result['original_text_excerpts']=f.value(key,records,sources)['original_text_excerpts']
    elif col=='claims':
        result['evidence']=[{'object_key':'evidence_steps/'+eid,
            'statement':records['evidence_steps/'+eid]['payload']['statement'],
            'original_text_excerpts':f.value('evidence_steps/'+eid,records,sources)['original_text_excerpts']}
            for eid in p.get('evidence_step_ids',[]) if 'evidence_steps/'+eid in records]
    return result


def allowed_pair(a,b,kind):
    ac,bc=a.split('/')[0],b.split('/')[0]
    if kind=='claim':
        return ac==bc=='claims'
    if kind=='attachment':
        return (ac=='claims' and bc in {'observations','evidence_steps'}) or (bc=='claims' and ac in {'observations','evidence_steps'})
    return ac in f.LOWER and bc in f.LOWER


def options(a,b,kind):
    result={}
    for direction in ['a_to_b','b_to_a']:
        src,dst=(a,b) if direction=='a_to_b' else (b,a)
        for typ in TYPES:
            if kind=='claim' and typ in {'answers','applies'}:
                continue
            if kind!='claim':
                fr,to=knowledge_relation_endpoints(typ, flash=True)
                if src.split('/')[0] not in fr or dst.split('/')[0] not in to:
                    continue
            result[direction+'_'+typ]=direction+': '+typ
    return {**result,'none':'沒有實質直接關係。','uncertain':'無法判定。'}


def forbidden(src,dst,typ,records):
    for k,r in records.items():
        if not k.startswith('claim_relation_constraints/'):
            continue
        p=r['payload'];pair=(p['source_id'],p['target_id'])
        if typ in p.get('forbidden_relation_types',[]) and ((src,dst)==pair or p.get('bidirectional') and (dst,src)==pair):
            return True
    return False


def review(records,sources,edge_keys,proposed,root,ports,*,kind,implicit=()):
    cases=[];seen=set()
    def find(oid):
        matches=[k for k in records if k.split('/')[0] in f.LOWER|{'claims'} and k.split('/',1)[1]==oid]
        if len(matches)!=1:
            raise ValueError('pair endpoint missing or ambiguous: '+oid)
        return matches[0]
    for key in edge_keys:
        p=records[key]['payload'];a,b=find(p['from_id']),find(p['to_id'])
        if not allowed_pair(a,b,kind):
            raise ValueError('pair stage endpoint type differs')
        cases.append({'job_id':key,'a':a,'b':b,'original':p,'key':key})
        seen.add(frozenset([a,b]))
    for item in implicit:
        a,b=item['a'],item['b']
        duplicate=next((case for case in cases if case['a']==a and case['b']==b
                        and case['original'].get('relation_type')=='supports'),None)
        if duplicate:
            duplicate['implicit']=True
            continue
        if not allowed_pair(a,b,kind):
            raise ValueError('illegal implicit pair')
        cases.append({'job_id':'I-'+e.digest([a,b])[:24], 'a':a,'b':b,
                      'original':{'from_id':a.split('/',1)[1], 'to_id':b.split('/',1)[1],
                                  'relation_type':'supports'}, 'key':None, 'implicit':True})
        seen.add(frozenset([a,b]))
    for pair in proposed:
        a,b=pair['a'],pair['b']
        if a==b or a not in records or b not in records or not allowed_pair(a,b,kind):
            raise ValueError('unprovided or illegal pair')
        if frozenset([a,b]) not in seen:
            cases.append({'job_id':'P-'+e.digest([a,b])[:24],'a':a,'b':b,'original':None,'key':None})
            seen.add(frozenset([a,b]))
    updates={};retiring=[];new=[];pending=[];outcomes=[];jobs=[];resolved=[];implicit_changes=[]
    for case in cases:
        a,b=case['a'],case['b'];original=case['original'];opts=options(a,b,kind)
        state={'A':node(a,records,sources),'B':node(b,records,sources),'existing_relation':original}
        questions={'relation':jevq.relation(opts)}
        if original and original.get('reason'):
            questions['reason_valid']=jevq.reason()
        ans=f.jev(root,ports,state,questions);chosen=ans['relation']
        if chosen=='uncertain' or (chosen not in {'none','uncertain'} and
                (not original or chosen!='a_to_b_'+original['relation_type'] or ('reason_valid' in ans and ans['reason_valid']!='grounded'))):
            jobs.append({**case,'state':state,'precheck':ans,'allowed_choices':opts})
        else:
            resolved.append((case,'remove' if chosen=='none' else 'keep',chosen,original.get('reason','') if original else '無直接關係。','jev_precheck'))
    for batch,raw in f.gpt(root,ports,'pair_'+kind,jobs,Decisions.model_json_schema(),
            '這是連接處置，不重審節點。GPT 最終決定可覆蓋 Jev；每個 job_id 恰好一次。'
            '選 set 並給 relation_type、direction 及教授論證中的簡短 reason；已有原關係完全保留可 keep。'
            '不成立 remove；仍無法判定 needs_source。只能使用 allowed_choices，不能補外部論據。'):
        items=Decisions.model_validate(raw).items
        f.exactly([x.model_dump() for x in items],[j['job_id'] for j in batch],'job_id')
        for item in items:
            case=next(j for j in batch if j['job_id']==item.job_id)
            selected=item.direction+'_'+item.relation_type
            if item.action=='set' and selected not in case['allowed_choices']:
                raise ValueError('GPT chose illegal pair type or direction')
            if item.action=='keep' and not case['original']:
                raise ValueError('cannot keep a nonexistent relation')
            resolved.append((case,item.action,selected,item.reason,'gpt_disposition'))
    for case,action,choice,reason,basis in resolved:
        original=case['original'];key=case['key']
        outcomes.append({'job_id':case['job_id'],'action':action,'basis':basis,'reason':reason})
        if action=='needs_source':
            pending.append({'job_id':case['job_id'],'reason':reason});continue
        if action=='keep':
            continue
        if original and original.get('review_status') in f.PROTECTED:
            pending.append({'job_id':case['job_id'],'reason':'protected_relation'});continue
        if action=='remove':
            if case.get('implicit'):implicit_changes.append({'a':case['a'],'b':case['b'],'action':'remove'})
            if key:retiring.append(key)
            continue
        direction,typ=choice.rsplit('_',1)
        a,b=(case['a'],case['b']) if direction=='a_to_b' else (case['b'],case['a'])
        src,dst=a.split('/',1)[1],b.split('/',1)[1]
        if forbidden(src,dst,typ,records):
            pending.append({'job_id':case['job_id'],'reason':'explicit_relation_constraint'});continue
        if case.get('implicit'):
            implicit_changes.append({'a':case['a'],'b':case['b'],'action':'remove'})
        post={**deepcopy(original or {}),'from_id':src,'to_id':dst,'relation_type':typ,'reason':reason,
              'record_namespace':f.VERSION}
        if key:
            if post!=original:updates[key]=post
        else:new.append(post)
    # The store has one active edge per exact typed signature. Collapsed corrections
    # reuse the surviving edge rather than fail SQL or spend a model call on identity.
    collection='claim_relations' if kind=='claim' else 'knowledge_relations'
    occupied={}
    keys=sorted(k for k in records if k.startswith(collection+'/') and k not in retiring)
    keys.sort(key=lambda k:k in updates)  # Preserve an unchanged existing edge first.
    for key in keys:
        payload=updates.get(key,records[key]['payload'])
        signature=(payload['from_id'],payload['to_id'],payload['relation_type'])
        if signature in occupied:
            if key not in updates:
                continue  # An unrelated historical duplicate is outside this review's authority.
            updates.pop(key,None);retiring.append(key)
            outcomes.append({'job_id':key,'action':'retire_duplicate','basis':'program_exact_signature',
                             'surviving_key':occupied[signature]})
        else:occupied[signature]=key
    additions=[]
    for post in new:
        signature=(post['from_id'],post['to_id'],post['relation_type'])
        if signature not in occupied:
            additions.append(post);occupied[signature]='new'
    return {'updates':updates,'retiring_keys':retiring,'new_relations':additions,
            'pending':pending,'outcomes':outcomes,'implicit_changes':implicit_changes}
