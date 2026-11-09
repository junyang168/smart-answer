"""Source-local Claim identity, shared by read inputs and transactional guards."""
import json

from .knowledge_models import evidence_fragment_ids


def belongs_to_source(claim,records,sid):
    aliases={sid,records['source_documents/'+sid]['payload'].get('transcript_id') or sid}
    occurrences=claim.get('occurrences',[])
    if occurrences:return all(o.get('source_id') in aliases for o in occurrences)
    eids=claim.get('evidence_step_ids',[])
    if not eids:return False
    for eid in eids:
        evidence=records.get('evidence_steps/'+eid)
        if not evidence or evidence.get('retired'):return False
        fids=evidence_fragment_ids(evidence['payload'])
        if not fids or any(not records.get('source_fragments/'+fid)
                or records['source_fragments/'+fid].get('retired')
                or records['source_fragments/'+fid]['payload'].get('source_id')!=sid for fid in fids):return False
    return True


def load_source_claims(cursor,sid,records=None):
    """Explicit occurrences, plus complete one-source owned Evidence chains.

    A caller's existing source census can supply the argument rows. The SQL
    guard builds the same narrow census inside its locked transaction. All
    containment queries are collection-scoped and batched for the GIN index.
    """
    working=dict(records) if records is not None else {}
    def fetch(sql,args):
        cursor.execute('SELECT collection,object_id,revision,content_sha256,payload FROM '
            'wang_knowledge.objects WHERE retired_at IS NULL AND '+sql,args)
        return {col+'/'+oid:{'revision':rev,'content_sha256':sha,'payload':p,'retired':False}
            for col,oid,rev,sha,p in cursor.fetchall()}
    if records is None:
        working.update(fetch("collection='source_documents' AND object_id=%s",(sid,)))
        working.update(fetch("collection='source_fragments' AND payload @> %s::jsonb",(json.dumps({'source_id':sid}),)))
        fids=sorted(k.split('/',1)[1] for k in working if k.startswith('source_fragments/'))
        for start in range(0,len(fids),8):
            needles=[json.dumps({field:[fid] if field.endswith('ids') else fid})
                for fid in fids[start:start+8] for field in ['source_fragment_id','source_fragment_ids']]
            working.update(fetch("collection='evidence_steps' AND payload @> ANY(%s::jsonb[])",(needles,)))
    document=working.get('source_documents/'+sid)
    if not document:raise ValueError('source Claim membership requires a registered source')
    aliases=sorted({sid,document['payload'].get('transcript_id') or sid})
    result=fetch("collection='claims' AND payload @> ANY(%s::jsonb[])",
        ([json.dumps({'occurrences':[{'source_id':alias}]}) for alias in aliases],))
    eids=sorted(k.split('/',1)[1] for k in working if k.startswith('evidence_steps/'))
    for start in range(0,len(eids),8):
        candidates=fetch("collection='claims' AND (NOT payload ? 'occurrences' OR payload->'occurrences'='[]'::jsonb) "
            "AND payload @> ANY(%s::jsonb[])",([json.dumps({'evidence_step_ids':[eid]}) for eid in eids[start:start+8]],))
        result.update({k:r for k,r in candidates.items() if belongs_to_source(r['payload'],working,sid)})
    return result
