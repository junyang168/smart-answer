"""Read-only, source-bound three-way recovery of a browser's durable edit queue.

The subsequent write still uses the normal claim/CAS/journal path. No recovered
confirmation is rebound to a different playback range.
"""
import copy
import re
from .transcript_review import ReviewError,read,digest,file_hash


def base_snapshot(store,item,version,rows,context):
    if not isinstance(version,str) or not re.fullmatch('[a-f0-9]{64}',version):
        raise ReviewError(400,'本機草稿缺少有效來源版本；修改仍保留')
    if version==digest(store.document_value(rows,context)):
        return {'script':copy.deepcopy(rows),'context':copy.deepcopy(context)},[]
    expected=None;later=[]
    for number in range(context['revision'],0,-1):
        path=store.folder(item)/'revisions'/f'{number:08d}.json';snapshot=read(path)
        if not snapshot or (expected and file_hash(path)!=expected):
            raise ReviewError(409,'歷史版本不完整，不能安全合併；本機修改仍保留')
        if number==context['revision'] and store.document_value(snapshot['script'],snapshot['context'])!=store.document_value(rows,context):
            raise ReviewError(409,'目前稿件與歷史版本不一致；本機修改仍保留')
        operation_id=snapshot.get('operation_id')
        if operation_id:
            if not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',operation_id):
                raise ReviewError(409,'歷史操作識別不正確；本機修改仍保留')
            journal=read(store.folder(item)/'operations'/f'{operation_id}.json',{})
            if journal.get('state')!='committed' or journal.get('result',{}).get('revision_sha256')!=digest(snapshot):
                raise ReviewError(409,'歷史版本未通過驗證；本機修改仍保留')
        if digest(store.document_value(snapshot['script'],snapshot['context']))==version:
            return snapshot,later
        later.append(snapshot);expected=snapshot.get('parent_revision_sha256')
        if number>1 and not expected:raise ReviewError(409,'找不到完整歷史鏈；本機修改仍保留')
    raise ReviewError(409,'找不到本機草稿的原始版本；請下載備份後比較，修改仍保留')


def prepare_recovery(store,actor,item,request):
    with store.lock(item):
        store.require(actor,item,write=True,epoch=request.get('epoch'))
        rows,context=store.load(item);store.version(rows,context,request.get('version'))
        effective_version=request.get('base_version')
        operations=request.get('pending_operations')
        if not isinstance(operations,list) or not operations or len(operations)>2000:
            raise ReviewError(400,'沒有有效的本機待存修改')
        packet=request.get('pending_packet')
        if packet:
            if not isinstance(packet,dict) or not isinstance(packet.get('operations'),list):
                raise ReviewError(400,'無效待存操作')
            packet_ops=packet.get('operations',[])
            if packet.get('version')!=effective_version or packet.get('epoch')!=request.get('epoch') or not packet_ops or operations[:len(packet_ops)]!=packet_ops:
                raise ReviewError(409,'待存操作與原始版本或認領不同；本機修改仍保留')
            receipt=store.receipt(item,packet.get('operation_id'),actor,packet)
            if receipt:
                effective_version=receipt['version'];operations=operations[len(packet_ops):]
        base,later=base_snapshot(store,item,effective_version,rows,context)
        if any(s.get('kind') in ('claim','unclaim','takeover','adopt','import') for s in later):
            raise ReviewError(409,'草稿期間認領已變更，不能自動合併；本機修改仍保留')
        old=copy.deepcopy(base['context']);new=copy.deepcopy(context)
        for field in ('source_video_sha256','source_record_sha256'):
            if old.get(field)!=new.get(field):raise ReviewError(409,'原始講道來源已更換；本機修改仍保留')
        before={p['index']:copy.deepcopy(p) for p in base['script']};now={p['index']:copy.deepcopy(p) for p in rows}
        rebased=[];recheck=[]
        def conflict(key):raise ReviewError(409,f'第 {key} 項已有其他修改，不能自動覆蓋；本機修改仍保留，請下載備份後比較')
        def paragraph(key):
            if key not in before or key not in now:conflict(key)
            a=old['paragraph_bindings'].get(key);b=new['paragraph_bindings'].get(key)
            if before[key]['type']!=now[key]['type']:conflict(key)
            if before[key]['type']=='subtitle' and a is None and b is None:return
            if not a or not b or a.get('source_paragraph_id')!=b.get('source_paragraph_id'):conflict(key)
        for value in operations:
            if not isinstance(value,dict):raise ReviewError(400,'無效本機修改')
            op=copy.deepcopy(value);kind=op.get('kind');key=op.get('id')
            if not isinstance(kind,str) or (kind!='metadata' and not isinstance(key,str)):
                raise ReviewError(400,'無效本機修改目標')
            if kind=='subtitles':
                from backend.pipeline.source_projection import spoken_source_rows
                if op.get('source_version')!=effective_version or spoken_source_rows(list(before.values()))!=spoken_source_rows(list(now.values())):conflict('小標題來源')
                op['source_version']=digest(store.document_value(rows,context))
            elif kind=='remove_subtitle':
                if key not in before or before[key].get('type')!='subtitle':conflict(key)
                if key not in now:continue
                if before[key]!=now[key]:conflict(key)
                before.pop(key);now.pop(key)
            elif kind=='paragraph_timing':
                paragraph(key)
                if (before[key].get('type')=='subtitle'
                    or old['paragraph_bindings'][key]!=new['paragraph_bindings'][key]
                    or before[key]['text']!=now[key]['text']):
                    conflict(key)
                old_state=(old.get('timing_quality') or {}).get('paragraph_states',{}).get(key)
                new_state=(new.get('timing_quality') or {}).get('paragraph_states',{}).get(key)
                if old_state!=new_state:conflict(key)
            elif kind in ('text','paragraph_review','bookmark'):
                paragraph(key)
                if kind=='text':
                    if not isinstance(op.get('text'),str):raise ReviewError(400,'無效文字修改')
                    if now[key]['text'] not in (before[key]['text'],op['text']):conflict(key)
                    for target,ctx in [(before,old),(now,new)]:
                        if target[key]['text']!=op['text']:ctx['reviews'].pop('p:'+key,None)
                        target[key]['text']=op['text']
                elif kind=='paragraph_review':
                    if before[key]['type']=='subtitle':conflict(key)
                    a={'text':before[key]['text'],**old['paragraph_bindings'][key]};b={'text':now[key]['text'],**new['paragraph_bindings'][key]}
                    if op.get('content_sha256')!=digest(a) or op.get('decision') not in ('confirmed','question','pending'):conflict(key)
                    if a!=b:
                        if a['text']!=b['text']:conflict(key)
                        if op['decision']=='confirmed':
                            recheck.append(key);continue
                        op['content_sha256']=digest(b)
                    existing=new['reviews'].get('p:'+key,{})
                    if existing.get('sha256')==digest(b) and existing.get('decision')==op['decision']:continue
            elif kind=='metadata':
                if not isinstance(op.get('value'),dict):raise ReviewError(400,'無效標題簡介修改')
                for field,value in op['value'].items():
                    if new['editorial_metadata'].get(field) not in (old['editorial_metadata'].get(field),value):conflict('標題與簡介')
                    old['editorial_metadata'][field]=new['editorial_metadata'][field]=copy.deepcopy(value)
            elif kind=='ocr':
                a=old['visual_assets'].get(key);b=new['visual_assets'].get(key)
                if not a or not b or a['image_sha256']!=b['image_sha256']:conflict(key)
                ar=next((r for r in a['content']['text_regions'] if r['id']==op.get('region_id')),None)
                br=next((r for r in b['content']['text_regions'] if r['id']==op.get('region_id')),None)
                if not ar or not br or not isinstance(op.get('text'),str) or br['text'] not in (ar['text'],op['text']):conflict(key)
                ar['text']=br['text']=op['text']
            elif kind in ('representative','placement','hidden','group_review'):
                a=next((g for g in old['groups'] if g['id']==key),None);b=next((g for g in new['groups'] if g['id']==key),None)
                if not a or not b or store.group_target(old,a)!=store.group_target(new,b):conflict(key)
                if kind=='representative':a['slide_id']=b['slide_id']=op.get('slide_id')
                elif kind=='placement':a['before_paragraph_id']=b['before_paragraph_id']=op.get('paragraph_id')
                elif kind=='hidden':
                    for field in ('hidden','reason'):a[field]=b[field]=op.get(field)
                elif op.get('content_sha256')!=digest(store.group_target(old,a)):conflict(key)
            else:raise ReviewError(400,'不支援的本機修改；草稿仍保留')
            rebased.append(op)
        return {'version':digest(store.document_value(rows,context)),'epoch':request['epoch'],
                'operations':rebased,'recheck_paragraph_ids':list(dict.fromkeys(recheck)),
                'base_version':request['base_version'],'read_only':True}
