"""Editor-accessible read/control plane. GET never starts or resumes processing."""

import json
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from backend.pipeline.media_batch.store import Store, Conflict
from backend.pipeline.media_batch.runtime import capacity_status
from .transcript_review import ReviewError
from .transcript_review_router import authenticated_actor
from .roles import user_roles


class Control(BaseModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    revision: int = Field(ge=1)
    action: str = Field(pattern=r"^(pause|resume|retry)$")


def create_media_batch_router(root, store_factory=Store):
    async def editor(request: Request):
        actor = authenticated_actor(request, await request.body())
        try:
            config = json.loads((Path(root) / "config/config.json").read_text())
        except (OSError, ValueError):
            raise HTTPException(503, "無法讀取角色設定")
        if not user_roles(config, actor).intersection(
            {"admin", "editor", "beta_tester"}
        ):
            raise HTTPException(403, "需要編輯權限")
        return actor

    router = APIRouter(prefix="/media_batches", dependencies=[Depends(editor)])

    @router.get('/sermons')
    def sermons(response: Response, search: str = '', series: str = '', state: str = 'actionable',
                source: str = Query('',pattern='^(|missing|single|multiple)$'),
                offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), snapshot_revision: str | None = None):
        from backend.pipeline.media_batch.sermon_monitor import snapshot
        response.headers['Cache-Control'] = 'private, no-store'
        try:
            return snapshot(store_factory(), root, search=search, series=series, status=state,source=source,
                            offset=offset, limit=limit, expected=snapshot_revision)
        except Conflict as error:
            raise HTTPException(409, str(error)) from error
        except Exception as error:
            import logging
            logging.getLogger(__name__).exception('Sermon pipeline projection unavailable')
            raise HTTPException(503, '管線索引尚未建立或暫時不可讀，請明確同步目錄') from error

    @router.post('/sermons/sync')
    def sync_sermons():
        from backend.pipeline.media_batch.sermon_monitor import refresh, settings
        try:
            return refresh(store_factory(), root, settings(root))
        except (ValueError, OSError, Conflict) as error:
            raise HTTPException(409, str(error)) from error

    class PlanRequest(BaseModel):
        items: list[str] = Field(min_length=1, max_length=20)
        budget_usd: float = Field(gt=0, le=1000)
        max_calls: int = Field(gt=0, le=10000)
        operation_id: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
        mode: str = Field(default='editor_draft',pattern=r'^(editor_draft|candidate|playback|slides|rough)$')
        expected_versions: dict[str,str] = Field(default_factory=dict)

    @router.post('/sermons/plan')
    def plan_sermons(body: PlanRequest, actor: str = Depends(editor)):
        from backend.pipeline.media_batch.sermon_monitor import settings
        from backend.pipeline.media_batch.gemini_plan import create
        from backend.pipeline.media_batch.runtime import file_sha
        try:
            cfg=settings(root);store=store_factory();jobs=[]
            from backend.pipeline.media_batch.store import key
            from psycopg.types.json import Jsonb
            identity=(str(Path(root).resolve()),actor,body.operation_id)
            request_sha=key(body.model_dump())
            with store.connection() as c:
                saved=c.execute('SELECT request_sha,response FROM media_ingest.plan_requests WHERE root=%s AND actor=%s AND operation=%s',identity).fetchone()
            if saved:
                if saved['request_sha']!=request_sha:raise Conflict('Operation ID reused with different plan input')
                return saved['response']
            with store.connection() as c:
                rows=list(c.execute('SELECT item,value FROM media_ingest.sermon_index WHERE root=%s AND item=ANY(%s)',
                                    (str(Path(root).resolve()),body.items)))
            if len(rows)!=len(set(body.items)):raise ValueError('Selected sermons are missing from the index')
            for row in rows:
                sources=row['value']['sources']
                if len(sources)!=1:raise ValueError('來源缺失或多版本待核實：'+row['item'])
                path=Path(sources[0]['path'])
                wanted={'editor_draft':['editor_draft','playback','slides'],'candidate':['candidate','playback'],
                        'playback':['playback'],'slides':['slides'],'rough':['rough']}[body.mode]
                jobs.append({'item':row['item'],'source':str(path),'source_sha256':file_sha(path),
                             'deliverables':wanted,'expected_version':body.expected_versions.get(row['item'])})
            cfg.update(budget_usd=body.budget_usd,max_calls=body.max_calls,operation_id=body.operation_id)
            batch=create(store,{'schema_version':'gemini-media-jobs.v1','jobs':jobs},cfg,actor)
            response={'batch':batch,'items':body.items,'budget_usd':body.budget_usd,'max_calls':body.max_calls,
                    'mode':body.mode,'status':'planned','message':'計畫已建立，尚未啟動模型或安裝媒體',
                    'jobs':[{'item':j['item'],'source_sha256':j['source_sha256'],'deliverables':j['deliverables'],
                             'adoption':'initialize_empty_draft' if body.mode=='editor_draft' else 'register_candidate' if body.mode=='candidate' else 'none',
                             'media_destination':'核實後沿用來源，或另存版本化播放檔' if 'playback' in j['deliverables'] else '沿用既有稿件版本' if body.mode=='slides' else '未校對粗稿，尚無 Editor 交付'} for j in jobs]}
            with store.connection() as c:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key(identity),))
                saved=c.execute('SELECT request_sha,response FROM media_ingest.plan_requests WHERE root=%s AND actor=%s AND operation=%s',identity).fetchone()
                if saved:
                    if saved['request_sha']!=request_sha:raise Conflict('Operation ID reused with different plan input')
                    return saved['response']
                c.execute('INSERT INTO media_ingest.plan_requests VALUES(%s,%s,%s,%s,%s)',(*identity,request_sha,Jsonb(response)))
            return response
        except PermissionError as error:
            raise HTTPException(403,str(error)) from error
        except ReviewError as error:
            raise HTTPException(error.status,error.message) from error
        except (ValueError, OSError, Conflict) as error:
            raise HTTPException(409, str(error)) from error

    @router.post('/sermons/start/{batch}')
    def start_sermons(batch: str, actor: str = Depends(editor)):
        from backend.pipeline.media_batch.service import start
        store=store_factory()
        with store.connection() as c:
            row=c.execute('SELECT config FROM media_ingest.batches WHERE id=%s',(batch,)).fetchone()
        if not row:raise HTTPException(404,'找不到計畫')
        if row['config'].get('actor')!=actor:raise HTTPException(403,'請由此計畫建立者啟動')
        try:return start(store,batch)
        except (ValueError, Conflict) as error:raise HTTPException(409,str(error)) from error

    @router.get('/sermons/{item}/candidates/{candidate_id}')
    def gemini_candidate(item: str,candidate_id: str,response: Response,actor: str=Depends(editor)):
        import re
        from .transcript_review import ReviewStore,read,digest
        review=ReviewStore(Path(root))
        try:
            review.require(actor,item)
            if not re.fullmatch('[0-9a-f]{64}',candidate_id):raise HTTPException(404,'找不到候選')
            value=read(Path(root)/'gemini_transcript_candidates'/item/f'{candidate_id}.json')
            if not value:raise HTTPException(404,'找不到候選')
            if digest(value)!=candidate_id:raise HTTPException(409,'候選版本不同')
            response.headers['Cache-Control']='private, no-store'
            return {k:value[k] for k in ('schema_version','source_sha256','duration_ms','layout')}
        except ReviewError as error:raise HTTPException(error.status,error.message) from error

    def timing_artifacts(batch,recording):
        from backend.pipeline.slide_change_pilot import read_json,file_sha
        with store_factory().connection() as c:
            task=c.execute("SELECT t.payload,t.result FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.recording=%s AND t.stage='finish'",(batch,recording)).fetchone()
        if not task or not (task.get('result') or {}).get('timing_report'):
            raise HTTPException(404,'此講道尚無時間核驗問題報告')
        try:
            binding=task['result']['timing_report'];folder=Path(task['payload']['out'])/'timing-review'
            if (binding['media_file'] not in ('playback.mp3','playback.mp4') or
                binding['mime'] not in ('audio/mpeg','video/mp4') or
                binding['source_sha256']!=task['payload']['info']['source_sha']):raise ValueError('Invalid source binding')
            for name,key in [('report.json','json_sha256'),('report.html','html_sha256'),(binding['media_file'],'media_sha256')]:
                if file_sha(folder/name)!=binding[key]:raise ValueError('Changed diagnostic artifact')
            report=read_json(folder/'report.json')
            if report['schema_version']!='transcript-timing-review-report.v1' or report['source_sha256']!=binding['source_sha256']:raise ValueError('Invalid report')
            return folder,binding
        except (OSError,ValueError,KeyError):
            raise HTTPException(409,'報告或音訊版本已變更，請重新產生核驗報告')

    @router.get('/{batch}/timing/{recording}')
    def timing_report(batch:str,recording:str):
        import secrets
        from urllib.parse import quote
        folder,binding=timing_artifacts(batch,recording)
        page=(folder/'report.html').read_text();nonce=secrets.token_urlsafe(24)
        old=f'{binding["media_file"]}?sha256={binding["media_sha256"]}'
        media=f'/api/sc_api/media_batches/{quote(batch,safe="")}/timing/{quote(recording,safe="")}/media'
        if page.count(f'src="{old}"')!=1 or page.count('<script>')!=1:raise HTTPException(409,'報告頁面結構不一致')
        page=page.replace(f'src="{old}"',f'src="{media}"').replace('<script>',f'<script nonce="{nonce}">')
        return Response(page,media_type='text/html',headers={
            'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':f"default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; base-uri 'none'; frame-ancestors 'none'"})

    @router.api_route('/{batch}/timing/{recording}/media',methods=['GET','HEAD'])
    def timing_media(batch:str,recording:str):
        folder,binding=timing_artifacts(batch,recording)
        return FileResponse(folder/binding['media_file'],media_type=binding['mime'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.get("")
    def get(
        response: Response,
        batch: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
    ):
        try:
            response.headers["Cache-Control"] = "private, no-store"
            store = store_factory()
            result = store.snapshot(batch, offset, limit)
            if result["selected"]:
                with store.connection() as c:
                    config = c.execute(
                        "SELECT config FROM media_ingest.batches WHERE id=%s",
                        (result["selected"]["id"],),
                    ).fetchone()["config"]
                result["capacity"] = capacity_status(config["capacity_root"])
                from backend.pipeline.media_batch.disk import status

                try:
                    result["disk"] = status(store, config)
                except (OSError, ValueError):
                    result["disk"] = {
                        name: dict(unavailable=True)
                        for name in ("artifact_root", "media_root")
                    }
            if result["selected"]:
                from backend.pipeline.media_batch.preflight_report import load

                report = load(root, result["selected"]["id"])
                if report:
                    result["preflight"] = report["summary"]
            return result
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except (ValueError, OSError):
            raise HTTPException(503, "批次服務尚未設定或無法讀取")
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Media batch read failed")
            raise HTTPException(503, "暫時無法讀取批次台帳")

    @router.get("/{batch}/preflight")
    def preflight(batch: str):
        from backend.pipeline.media_batch.preflight_report import load

        try:
            report = load(root, batch)
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, "查重對照表版本不一致，請重新產生")
        if not report:
            raise HTTPException(404, "此批次尚無查重對照表")
        return Response(
            report["html"],
            media_type="text/html",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{batch}/rough/{recording}")
    def rough_text(batch: str, recording: str):
        from backend.pipeline.media_batch.rough import render
        from backend.pipeline.media_batch.runtime import file_sha

        with store_factory().connection() as c:
            task = c.execute(
                "SELECT t.payload,t.result FROM media_ingest.tasks t JOIN media_ingest.batch_tasks b ON b.task=t.id WHERE b.batch=%s AND t.recording=%s AND t.stage='rough_asr' AND t.state='complete'",
                (batch, recording),
            ).fetchone()
        if not task:
            raise HTTPException(404, "粗逐字稿尚未完成")
        out = Path(task["payload"]["out"])
        if file_sha(out / "rough.json") != task["result"]["rough_sha256"]:
            raise HTTPException(409, "粗逐字稿版本不一致")
        return Response(
            render(out),
            media_type="text/html",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/{batch}/control")
    def control(batch: str, body: Control):
        try:
            return dict(
                revision=store_factory().control(
                    batch, body.operation_id, body.revision, body.action
                )
            )
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except Conflict as e:
            raise HTTPException(409, str(e))
        except Exception:
            raise HTTPException(503, "控制指令未確認，請重新讀取狀態")

    @router.get("/{batch}/files")
    def files(
        batch: str,
        response: Response,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
        search: str = Query("", max_length=200),
        state: str = Query(
            "all",
            pattern=r"^(all|actionable|rough_complete|waiting_download|ready_to_plan|needs_assembly|needs_scope_review|pending|running|complete|existing_transcript|reused|duplicate|excluded|failed|needs_resolution|unknown_outcome|unverified|interrupted)$",
        ),
    ):
        from backend.pipeline.media_batch.files import snapshot

        response.headers["Cache-Control"] = "private, no-store"
        try:
            return snapshot(store_factory(), batch, offset, limit, search, state)
        except KeyError:
            raise HTTPException(404, "找不到批次")
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Media file inventory read failed")
            raise HTTPException(503, "暫時無法讀取檔案清單")

    return router
