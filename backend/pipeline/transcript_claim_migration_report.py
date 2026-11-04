"""Read-only, source-bound comparison artifacts; never qualification authority."""
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline import transcript_claim_migration as migration


def compare(store, root):
    root=Path(root).resolve();manifest=migration.load(root)
    frozen=migration.read(manifest['records']['path'])
    before={cm.source.key(row['collection'],row['object_id']):{
        k:v for k,v in row.items() if k not in {'collection','object_id'}} for row in frozen}
    payloads={key:state['payload'] for key,state in before.items()}
    owned={key for key,state in before.items() if key.split('/',1)[0] in cm.CLAIM_COLLECTIONS
        and cm.own_source_ids(state['payload'],payloads)=={manifest['source_id']}}
    report=migration.read(root/'current-report.json') if (root/'current-report.json').exists() else None
    created=report.get('created_claim_ids',[]) if report else []
    owned.update('claims/'+cid for cid in [*manifest['claim_ids'],*created])
    source_key='source_documents/'+manifest['source_id']
    current={cm.source.key(*key):state for key,state in store.read_current_records(
        [cm.source.split_key(key) for key in sorted(owned|{source_key})]).items()}
    if (not current.get(source_key) or current[source_key]['retired'] or
            current[source_key]['payload'].get('source_file_sha256')!=manifest['after']['file_sha256']):
        raise ValueError('comparison source epoch changed')
    def fields(old,new):
        return sorted(key for key in set(old)|set(new) if old.get(key)!=new.get(key))
    claims=[]
    for cid in dict.fromkeys([*manifest['claim_ids'],*created]):
        key='claims/'+cid;old=before.get(key);new=current.get(key)
        a=(old or {}).get('payload',{});b=(new or {}).get('payload',{})
        changed=fields(a,b)
        claims.append({'claim_id':cid,'before_statement':a.get('statement'),
            'after_statement':b.get('statement'),'before_pin':pin(old),'after_pin':pin(new),
            'changed_fields':changed,'retired':bool(new and new['retired']),
            'statement_changed':a.get('statement')!=b.get('statement'),
            'saved_verification':(report or {}).get('results',{}).get(cid)})
    dependents=[];fragment_status={}
    snapshot=cm.source.source_snapshot(cm.source.FileBinding.model_validate(manifest['after']))
    for key in sorted(owned):
        if key.startswith('claims/'):continue
        old=before.get(key);new=current.get(key)
        if new and key.startswith('source_fragments/') and not new['retired']:
            f=new['payload'];p=snapshot['paragraphs'].get(str(f.get('paragraph_key')),{});quote=f.get('verbatim_excerpt')
            fragment_status[key]=bool(quote and quote in p.get('text','')
                                      and f.get('source_sha256')==snapshot['body_sha256'])
        if pin(old)==pin(new):continue
        a=(old or {}).get('payload',{});b=(new or {}).get('payload',{})
        changed=fields(a,b)
        dependents.append({'record_key':key,'before_pin':pin(old),'after_pin':pin(new),
            'changed_fields':changed,'before':{k:a.get(k) for k in changed},
            'after':{k:b.get(k) for k in changed}})
    value={'schema_version':'wang_transcript_claim_comparison_v1',
        'generated_at':datetime.now(timezone.utc).isoformat(),'item':manifest['item'],
        'beta_revision':manifest['beta_revision'],'migration_manifest':migration.binding(root/'manifest.json'),
        'before_source':manifest['before'],'after_source':manifest['after'],
        'claims':claims,'dependent_changes':dependents,'fragment_current_source_match':fragment_status,
        'created_inventory_available':report is not None,'qualification_checked_by_this_report':False,
        'saved_verification_at':datetime.fromtimestamp((root/'current-report.json').stat().st_mtime,timezone.utc).isoformat() if report else None,
        'all_data_migrated':False}
    from backend.api.sc_api.transcript_review import atomic
    atomic(root/'comparison.json',value)
    (root/'comparison.html').write_text(render(value))
    return {'comparison':str(root/'comparison.html'),'data':str(root/'comparison.json'),
            'claim_count':len(claims),'statement_changes':sum(c['statement_changed'] for c in claims),
            'dependent_changes':len(dependents),'qualification_checked':False}


def pin(state):
    return [state['revision'],state['content_sha256'],state['retired']] if state else None


def render(value):
    def cell(text):return escape(str(text)) if text is not None else '—'
    rows=[]
    for c in value['claims']:
        status='缺少記錄' if c['after_pin'] is None else '已退役（保留歷史）' if c['retired'] else '陳述有變更' if c['statement_changed'] else '陳述相同'
        rows.append('<tr><td>'+cell(c['claim_id'])+'</td><td>'+cell(c['before_statement'])+'</td><td>'+
            ('已退役' if c['retired'] else cell(c['after_statement']))+'</td><td>'+status+
            '<br><small>'+cell(', '.join(c['changed_fields']))+'</small></td></tr>')
    extra=[]
    for row in value['dependent_changes']:
        extra.append('<details><summary>'+cell(row['record_key'])+' · '+cell(', '.join(row['changed_fields']))+
            '</summary><div class="pair"><pre>'+cell(json.dumps(row['before'],ensure_ascii=False,indent=2))+
            '</pre><pre>'+cell(json.dumps(row['after'],ensure_ascii=False,indent=2))+'</pre></div></details>')
    matched=sum(value['fragment_current_source_match'].values());total=len(value['fragment_current_source_match'])
    return '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Claim 移植對照</title><style>body{font:16px/1.65 system-ui,sans-serif;color:#182232;background:#f7f9fc;margin:24px}main{max-width:1500px;margin:auto}table{border-collapse:collapse;width:100%;background:white}th,td{border:1px solid #cad3de;padding:12px;text-align:left;vertical-align:top;overflow-wrap:anywhere}td:first-child{width:16%;font-size:13px}th{background:#e9eff6}small{color:#536174}pre{white-space:pre-wrap;overflow-wrap:anywhere;flex:1;min-width:0;padding:12px;background:white}.pair{display:flex;gap:12px}details{margin:12px 0}h1{font-size:24px}@media(max-width:700px){body{margin:10px}.pair{display:block}td,th{padding:6px;font-size:13px}}</style><main><h1>'+cell(value['item'])+' · Claim 移植對照</h1><p>Beta '+cell(value['beta_revision'])+' · 產生時間 '+cell(value['generated_at'])+'</p><p>本表是內部歷史與當前資料的對照；不判定意思等價，不新增內容批准，也不表示整篇移植完成。舊文字只作歷史比對。資格由正式審查與回讀另行核實。</p><p>本表所讀同源片段中，'+str(matched)+' / '+str(total)+' 與新版有效正文逐字及版本相符；未相符不等於全部仍被 Claim 使用，須由正式遷移清點判定。</p><table><thead><tr><th>Claim ID</th><th>舊陳述</th><th>目前陳述</th><th>資料差異</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table><h2>相關資料變更（左舊、右新）</h2>'+''.join(extra)+'</main>'
