#!/usr/bin/env python3
"""Independent passage/member or argument-group review; physical originals, stdlib only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

spec = importlib.util.spec_from_file_location('independent_grouping_reader', Path(__file__).with_name('review-exegesis-grouping.py'))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

PROMPT = '''你是独立释经编排审核员，直接读所给物理原件文字，不接受提议者理由作为证据。只审核教授解释哪段经文、段落成员及边界，不判断神学对错，不生成观点/CVP、不合并主张、不调和张力。
层次必须分清：第一层确认完整释经段落，允许多个子论证、重叠结构范围及跨章；不要因段内有多个论证就要求提前拆组。第二层才沿论证边界拆成<=20条的比较组。经审核primary不能被candidate/支持经文/最早引用自动取代。章级定位保留章级，不猜节号。
逐条审核本轮全部Claim。primary_reviews必须覆盖所有指定id：能依据物理原文核实的primary返回verified，并提供实际source_id、物理location和连续逐字quote；不能核实则unresolved，primary为null，明确缺什么。已有verified_primary不可无证据改写。
逐单元审核本轮全部target。findings必须覆盖全部target：pass、change或unresolved。change必须给出具体更正范围或逐条成员移动建议及原因；unresolved指出具体所缺原文。所有证据只能引用physical_sources里实际提供的文字，不引用Claim陈述冒充原件，不跨SVG标记拼接引文。model_text_parts内只有文字片段可引用，图形不可见。
catalog是整书卷的暂定段落目录，可提出把本轮Claim移到其中另一单元，或给出new_passage_key让改正者建立恰当单元；不得漏掉Claim，不把余项塞进其他/未知大桶。对跨段支持保留关系，不复制处理成员。
给出真实审核结果。need_more_context的location须指出所缺物理行或当前冻结段落键及需要的前后范围，不能泛泛要求118份全文。每个verified主归属、每个pass边界须至少一处提供过的物理原文证据。'''


def schema_for(ids, targets):
    evidence = dict(type='object', additionalProperties=False, required=['source_id', 'location', 'quote'],
        properties={k: dict(type='string') for k in ('source_id','location','quote')})
    primary = dict(type='object', additionalProperties=False, required=['status','primary','reason','evidence'], properties={
        'status':dict(type='string',enum=['verified','unresolved']), 'primary':dict(anyOf=[dict(type='string'),dict(type='null')]),
        'reason':dict(type='string'), 'evidence':dict(type='array',items={'$ref':'#/$defs/evidence'})})
    move = dict(type='object', additionalProperties=False, required=['claim_id','target_unit_id','new_passage_key','reason'], properties={
        'claim_id':dict(type='string'), 'target_unit_id':dict(anyOf=[dict(type='string'),dict(type='null')]),
        'new_passage_key':dict(anyOf=[dict(type='string'),dict(type='null')]), 'reason':dict(type='string')})
    finding = dict(type='object', additionalProperties=False,
        required=['status','reason','evidence','suggested_passage_key','moves','need_more_context'],properties={
        'status':dict(type='string',enum=['pass','change','unresolved']), 'reason':dict(type='string'),
        'evidence':dict(type='array',items={'$ref':'#/$defs/evidence'}),
        'suggested_passage_key':dict(anyOf=[dict(type='string'),dict(type='null')]),
        'moves':dict(type='array',items={'$ref':'#/$defs/move'}), 'need_more_context':dict(type='string')})
    return dict(type='object',additionalProperties=False,required=['primary_reviews','findings'],
        **{'$defs':dict(evidence=evidence,primary=primary,move=move,finding=finding)}, properties={
        'primary_reviews':dict(type='object',additionalProperties=False,required=ids,properties={k:{'$ref':'#/$defs/primary'} for k in ids}),
        'findings':dict(type='object',additionalProperties=False,required=targets,properties={k:{'$ref':'#/$defs/finding'} for k in targets})})


def compile_packet(request):
    frozen = reader.checked(Path(request['role_packet_path']))
    if frozen['artifact_sha256'] != request['role_packet_sha256']:
        raise ValueError('independent frozen graph binding mismatch')
    claim_index = {c['claim_id']:c for c in frozen['claims']}
    by_source, claims = {}, []
    for item in request['claims']:
        claim = claim_index[item['original_claim_id']]
        if claim['claim_content_sha256'] != item['claim_content_sha256']:
            raise ValueError('independent Claim version mismatch')
        by_source.setdefault(claim['source_id'], []).append((item,claim))
        claims.append({k:v for k,v in item.items() if k not in {'original_claim_id','claim_content_sha256'}})
    physical, quote_index = [], {}
    for source in request['sources']:
        sid = source['source_id']
        if sid not in by_source:
            continue
        data = Path(source['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != source['file_sha256']:
            raise ValueError('independent physical source drift')
        rows = reader.physical_paragraphs(data.decode())
        try: raw = json.loads(data)
        except json.JSONDecodeError: raw = None
        script = raw if isinstance(raw,list) else raw.get('script') if isinstance(raw,dict) else None
        positions = list(range(len(rows)))
        if isinstance(script,list):
            positions = [i for i in positions if not re.fullmatch(r'#{1,6}\s+.+', rows[i][1].strip()) and
                (not isinstance(script[i],dict) or (str(script[i].get('type','')).lower() not in {'subtitle','comment'} and
                not str(script[i].get('index','')).startswith('subtitle-')))]
        anchors, fragment_links = set(), []
        for item,claim in by_source[sid]:
            for step in claim['evidence_steps']:
                for fragment in step.get('fragments',[]):
                    excerpt = fragment['verbatim_excerpt']
                    matches = [i for i,(_,text) in enumerate(rows) if excerpt and excerpt in text and not re.search(r'<svg\b',excerpt,re.I)]
                    key = fragment.get('paragraph_key','')
                    if not matches and re.fullmatch(r'S\d{4}',key):
                        n = int(key[1:])-1
                        if 0 <= n < len(positions): matches = [positions[n]]
                    anchors.update(matches)
                    fragment_links.append(dict(claim_id=item['id'], frozen_key=key,
                        physical_locations=[rows[i][0] for i in matches], frozen_quote_is_verbatim=any(excerpt in rows[i][1] for i in matches)))
        radius = request.get('context_radius',1)
        chosen = {j for i in anchors for j in range(max(0,i-radius),min(len(rows),i+radius+1))}
        # Explicit targeted supplements, never capacity truncation.
        for extra in request.get('extra_context',[]):
            if extra['source_id']==sid:
                for loc in extra['locations']:
                    chosen.update(i for i,(key,_) in enumerate(rows) if key==loc)
        selected=[]
        for i in sorted(chosen):
            location,text = rows[i]
            clean = reader.exclude_svg(text)
            pieces = [clean] if isinstance(clean,str) else [p for p in clean['model_text_parts'] if isinstance(p,str)]
            if request.get('exact_fragments_only') and not any(
                extra['source_id']==sid and location in extra['locations'] for extra in request.get('extra_context',[])
            ):
                excerpts = {f['verbatim_excerpt'] for _,claim in by_source[sid]
                    for step in claim['evidence_steps'] for f in step.get('fragments',[])
                    if f['verbatim_excerpt'] and any(f['verbatim_excerpt'] in part for part in pieces)}
                if not excerpts:
                    continue
                pieces = sorted(excerpts, key=lambda quote: (text.index(quote),quote))
                clean = {'physical_excerpts': pieces, 'complete_paragraph': False}
            quote_index[(sid,location)] = pieces
            selected.append(dict(location=location,text=clean))
        for linked in source.get('linked_files',[]):
            if hashlib.sha256(Path(linked['path']).read_bytes()).hexdigest()!=linked['file_sha256']:
                raise ValueError('independent linked source drift')
        physical.append(dict(source_id=sid,file_sha256=source['file_sha256'],paragraph_count=len(rows),
            paragraphs=selected,fragment_locations=fragment_links,
            whole_source_included=not request.get('exact_fragments_only') and len(chosen)==len(rows)))
    return dict(stage=request['stage'],claims=claims,targets=request['targets'],catalog=request['catalog'],
        physical_sources=physical,previous_findings=request.get('previous_findings',{}),
        final_review=request.get('final_review',False)),quote_index


def validate(response, request, quote_index):
    if set(response['primary_reviews'])!={c['id'] for c in request['claims']} or set(response['findings'])!={t['unit_id'] for t in request['targets']}:
        raise ValueError('independent review omitted or added Claim/unit')
    errors=[]
    own_sources = {c['id']:c['source_id'] for c in request['claims']}
    for key,item in [*response['primary_reviews'].items(),*response['findings'].items()]:
        passing = item['status'] in {'verified','pass'}
        if not item['reason'] or (passing and not item['evidence']): errors.append(dict(key=key,error='missing reason/physical evidence'))
        if key in own_sources and passing and (not item['primary'] or any(e['source_id']!=own_sources[key] for e in item['evidence'])):
            errors.append(dict(key=key,error='verified primary requires a locator and own-source evidence'))
        for evidence in item['evidence']:
            if not evidence['quote'] or not any(evidence['quote'] in text for text in quote_index.get((evidence['source_id'],evidence['location']),[])):
                errors.append(dict(key=key,error='non_verbatim_or_unprovided_physical_evidence',evidence=evidence))
    return dict(response=response,evidence_errors=errors,validation_passed=not errors,
        status='pass' if not errors and all(i['status']=='verified' for i in response['primary_reviews'].values()) and
            all(i['status']=='pass' for i in response['findings'].values()) else 'needs_resolution',binding=request['artifact_sha256'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--compile-only',action='store_true');a=p.parse_args()
    root=a.output_dir;root.mkdir(exist_ok=False,parents=True);request=reader.checked(a.input)
    try:
        payload,index=compile_packet(request);schema=schema_for([c['id'] for c in request['claims']],[t['unit_id'] for t in request['targets']])
        body=json.dumps(reader.compact_packet(payload),ensure_ascii=False,separators=(',',':'));schema_text=json.dumps(schema,ensure_ascii=False,separators=(',',':'))
        prompt='无损text引用按texts表还原。physical_excerpts是独立打开原件后核实的连续摘录，不是完整段落；不足时必须请求具体物理位置。\n'+PROMPT
        if request.get('review_scope')=='one whole book':
            prompt+='\n本轮是整卷审核，全部Claim、全部段落及成员在一次请求中提供。保持全局判断，不分局部任务。逐条reason简洁，每处证据只引用足够证明判断的短连续原文，不重复长段。'
        env=dict(os.environ)
        for k in list(env):
            if k.startswith(('ANTHROPIC_','OPENAI_','AZURE_OPENAI_','CLAUDE_CODE_USE_')) or k in {'CLAUDE_CODE_OAUTH_TOKEN','CODEX_API_KEY'}:env.pop(k)
        env['CLAUDE_CODE_MAX_OUTPUT_TOKENS']=str(request.get('max_output_tokens',64000))
        cli=env.get('CLAUDE_EXECUTABLE') or shutil.which('claude') or 'claude'
        command=[cli,'--print','--safe-mode','--disable-slash-commands','--no-session-persistence','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','','--permission-mode','dontAsk','--model',request['model'],'--effort','high','--system-prompt',prompt,'--output-format','json','--json-schema',schema_text]
        size=len(body.encode())+sum(len(arg.encode()) for arg in command)
        reader.seal(root/'request.json',dict(payload=payload,schema=schema,prompt=prompt,model=request['model'],effort='high',request_bytes=size,
            max_request_bytes=request['max_request_bytes'],max_output_tokens=int(env['CLAUDE_CODE_MAX_OUTPUT_TOKENS']),
            input_sha256=request['artifact_sha256'],code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
        print(json.dumps(dict(stage='compiled',request_bytes=size,claim_count=len(request['claims']),unit_count=len(request['targets']))),flush=True)
        if a.compile_only:return
        if size>request['max_request_bytes']:raise ValueError('independent complete request exceeds byte limit; no truncation')
        auth=subprocess.run([cli,'auth','status'],env=env,text=True,capture_output=True,timeout=30);state=json.loads(auth.stdout)
        if auth.returncode or not state.get('loggedIn') or state.get('authMethod')!='claude.ai' or state.get('subscriptionType') not in {'pro','max','team','enterprise'}:raise ValueError('Claude subscription login required')
        try:result=subprocess.run(command,input=body,env=env,text=True,capture_output=True,timeout=request.get('timeout_seconds',900),cwd=root)
        except subprocess.TimeoutExpired as e:
            reader.seal(root/'transport.raw.json',dict(stdout=str(e.stdout or ''),stderr=str(e.stderr or ''),timeout=True));raise
        reader.seal(root/'transport.raw.json',dict(stdout=result.stdout,stderr=result.stderr,returncode=result.returncode))
        if result.returncode:raise ValueError('Opus subscription transport failed')
        wrapper=json.loads(result.stdout)
        if wrapper.get('is_error'):raise ValueError(str(wrapper.get('result')))
        response=wrapper.get('structured_output');response=json.loads(response) if isinstance(response,str) else response
        reader.seal(root/'response.json',dict(response=response))
        report=validate(response,request,index);reader.seal(root/'report.json',report|dict(model=request['model'],effort='high'))
        print(json.dumps(dict(stage='complete',status=report['status'],evidence_errors=len(report['evidence_errors']))),flush=True)
    except Exception as e:
        reader.seal(root/'failure.json',dict(error=str(e),error_type=type(e).__name__));raise
if __name__=='__main__':main()
