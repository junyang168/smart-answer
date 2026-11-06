"""SQL projection of every file, including sources with no transcription task yet."""


def snapshot(store, batch, offset=0, limit=30, search="", state="all"):
    with store.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        header = c.execute(
            "SELECT id,inventory_at,inventory_error,desired,config->>'title' AS title FROM media_ingest.batches WHERE id=%s",
            (batch,),
        ).fetchone()
        if not header:
            raise KeyError(batch)
        base = """WITH f AS (
          SELECT f.*,COALESCE(f.recording,a.recording) AS linked_recording FROM media_ingest.batch_files f
          LEFT JOIN media_ingest.assets a ON a.sha=f.sha WHERE f.batch=%s), latest AS (
          SELECT DISTINCT ON(t.recording) t.recording,b.id AS batch FROM media_ingest.tasks t
          JOIN media_ingest.batch_tasks bt ON bt.task=t.id JOIN media_ingest.batches b ON b.id=bt.batch
          WHERE t.recording IN (SELECT linked_recording FROM f)
          ORDER BY t.recording,b.created_at DESC,b.id), jobs AS (
          SELECT t.* FROM media_ingest.tasks t JOIN latest l ON l.recording=t.recording
          JOIN media_ingest.batch_tasks bt ON bt.task=t.id AND bt.batch=l.batch), g AS (
          SELECT recording,bool_and(state='complete') AS ready,bool_and(stage='existing') AS reused,
          bool_or(state='running') AS running,bool_or(state='running' AND heartbeat>now()-interval '30 seconds') AS live,
          array_agg(state) AS states,
          (array_agg(stage ORDER BY CASE WHEN state='running' THEN 0 WHEN state IN ('failed','unknown_outcome','needs_resolution','interrupted') THEN 1 WHEN state='pending' THEN 2 ELSE 3 END,
          array_position(ARRAY['existing','prepare','visuals','speech','final-align','finish','speakers','media','handoff'],stage)))[1] AS stage,
          (array_agg(error ORDER BY updated_at DESC) FILTER(WHERE error IS NOT NULL))[1] AS error
          FROM jobs GROUP BY recording), r AS (
          SELECT f.path,f.sha,f.bytes,f.duplicate_of,f.updated_at,COALESCE(rec.item,f.catalog_match->>'item') AS item,
          COALESCE(rec.info->>'sermon_title',f.catalog_match->>'title') AS title,rec.info->'provenance' AS provenance,
          f.catalog_match->>'manuscript' AS existing_manuscript,
          CASE WHEN g.ready THEN CASE WHEN g.reused THEN 'existing' ELSE 'handoff' END ELSE g.stage END AS stage,
          CASE WHEN f.state NOT IN ('pending','ready_to_plan','needs_assembly','needs_scope_review') THEN f.state
          WHEN g.ready AND g.reused THEN 'reused' WHEN g.ready THEN 'complete'
          WHEN g.running THEN CASE WHEN g.live THEN 'running' ELSE 'unverified' END
          WHEN 'unknown_outcome'=ANY(g.states) THEN 'unknown_outcome'
          WHEN 'needs_resolution'=ANY(g.states) THEN 'needs_resolution'
          WHEN 'failed'=ANY(g.states) THEN 'failed'
          WHEN 'interrupted'=ANY(g.states) THEN 'interrupted'
          WHEN g.recording IS NOT NULL THEN 'pending'
          WHEN rec.info->>'existing_transcript'='true' THEN 'reused' ELSE f.state END AS state,
          COALESCE(g.error,f.reason) AS reason
          FROM f LEFT JOIN g ON g.recording=f.linked_recording LEFT JOIN media_ingest.recordings rec ON rec.id=f.linked_recording)
        """
        counts = {
            r["state"]: r["n"]
            for r in c.execute(
                base + "SELECT state,count(*) AS n FROM r GROUP BY state", (batch,)
            )
        }
        where = " WHERE (%s='' OR strpos(lower(path||' '||COALESCE(title,'')),lower(%s))>0 OR strpos(lower(COALESCE(item,'')),lower(%s))>0) AND (%s='all' OR state=%s OR (%s='actionable' AND state NOT IN ('complete','reused','duplicate','excluded','existing_transcript')))"
        args = (batch, search, search, search, state, state, state)
        count = c.execute(
            base + "SELECT count(*) AS n FROM r" + where, args
        ).fetchone()["n"]
        rows = c.execute(
            base
            + "SELECT * FROM r"
            + where
            + " ORDER BY CASE WHEN state IN ('complete','reused','duplicate','excluded') THEN 1 ELSE 0 END,path LIMIT %s OFFSET %s",
            (*args, limit, offset),
        ).fetchall()
        return dict(
            batch=header,
            files=rows,
            counts=counts,
            total=sum(counts.values()),
            filtered_total=count,
            offset=offset,
            limit=limit,
            generated_at=c.execute("SELECT now() AS at").fetchone()["at"],
        )
