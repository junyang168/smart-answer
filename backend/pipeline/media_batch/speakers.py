"""Speaker attribution stays a candidate; all spoken characters and timing remain unchanged."""

from pathlib import Path
import json
from .runtime import atomic, file_sha


RELEASE='audio-refinement-beta1'


def generate(out,*,workers=1):
    from backend.pipeline.transcript_speaker_batch import run

    out = Path(out)
    record = json.loads((out / "full_transcript.json").read_text())
    snapshot = out / "speaker-snapshot.json"
    rows = [dict(index=p["id"], text=p["text"]) for p in record["paragraphs"]]
    atomic(
        snapshot,
        dict(
            script=rows,
            context=dict(
                revision=0,
                reviews={},
                paragraph_bindings={
                    p["id"]: dict(source_paragraph_id=p["id"])
                    for p in record["paragraphs"]
                },
            ),
        ),
    )
    evidence = out.with_name(out.name + "-speakers")
    result = run(out, snapshot, evidence, workers=workers, thinking_level="LOW")
    if any(w["status"] != "complete" for w in result["windows"]):
        raise ValueError(
            "Speaker attribution incomplete; inspect saved speaker progress"
        )
    accepted, notices, resolved = assemble(record, evidence,with_resolved=True)
    candidate = dict(
        schema_version="media-batch-speakers.v1",
        source_record_sha256=file_sha(out / "full_transcript.json"),
        results=accepted,
        evidence_root=str(evidence),
        review_status="unreviewed_model_candidate",
        notices=notices,
        resolved_notices=resolved,
        assembly_policy="preserve-source-and-disclose.v1",
        pipeline_release=RELEASE,
    )
    atomic(out / "speaker-candidate.json", candidate)
    return dict(
        speaker_candidate_sha256=file_sha(out / "speaker-candidate.json"),
        paragraphs=len(accepted),
        review_notices=len(notices),
        pipeline_release=RELEASE,
    )


def require_candidate(out,record=None):
    """Beta 1 Editor admission requires complete source-bound speaker coverage."""
    out=Path(out)
    record=record or json.loads((out/'full_transcript.json').read_text())
    path=out/'speaker-candidate.json'
    if not path.is_file():raise ValueError('Beta 1 requires speaker attribution before Editor admission')
    candidate=json.loads(path.read_text())
    if candidate.get('pipeline_release')!=RELEASE:raise ValueError('Speaker candidate is not a Beta 1 result')
    # This validates every ID, role and exact character partition, not a ready flag.
    mapping={p['id']:p['id'] for p in record['paragraphs']}
    rows=[dict(index=p['id'],text=p['text']) for p in record['paragraphs']]
    label_rows(out,record,rows,mapping)
    return candidate


def enrich_existing_draft(store,item,out,actor,*,expected_version,expected_epoch,operation_id):
    """One versioned insertion-only upgrade; never overwrite user edits or reviews."""
    import copy
    from backend.api.sc_api.transcript_review import digest,ReviewError
    from backend.pipeline.transcript_editorial_soft_deletions import editor_paragraphs
    out=Path(out);record=json.loads((out/'full_transcript.json').read_text())
    require_candidate(out,record)
    editorial,hidden=editor_paragraphs(record)
    with store.lock(item):
        store.require(actor,item,write=True,epoch=expected_epoch)
        prior=store.receipt(item,operation_id,actor,{'version':expected_version,'source_candidate_sha256':file_sha(out/'speaker-candidate.json')})
        if prior:return prior
        rows,context=store.load(item);store.version(rows,context,expected_version)
        if context.get('source_record_sha256')!=digest(record):raise ReviewError(409,'講員結果與目前草稿來源不同，不覆寫。')
        mapping={v['source_paragraph_id']:key for key,v in context['paragraph_bindings'].items()}
        if any(p['id'] not in mapping for p in editorial):raise ReviewError(409,'原文段落綁定已改變，不覆寫。')
        raw=[dict(index=mapping[p['id']],text=p['text']) for p in record['paragraphs']]
        labeled,attribution=label_rows(out,record,raw,mapping,hidden_insertions=hidden)
        labels={p['index']:p['text'] for p in labeled}
        baseline={mapping[p['id']]:p['text'] for p in editorial if not p.get('excluded_only')}
        updated=copy.deepcopy(rows);changed=[];protected=[]
        for row in updated:
            key=row['index']
            if key not in baseline:continue
            if 'p:'+key in context['reviews'] or row['text']!=baseline[key]:
                protected.append(key)
                binding=context['paragraph_bindings'][key]
                attribution['notices'].append(dict(kind='speaker_labels_preserved_human_edit',paragraph_id=key,start_ms=binding['start_ms'],end_ms=binding['end_ms'],audio_quote='',reason='此段已有人工修改或核對，已保留；未自動插入講員標記。來源講員候選另存備查。',timing_precision='source_paragraph'))
                continue
            if row['text']!=labels[key]:
                row['text']=labels[key];changed.append(key)
        attribution.update(protected_paragraph_ids=protected,labels_inserted_paragraph_ids=changed)
        context=copy.deepcopy(context);context['speaker_attribution']=attribution
        request={'version':expected_version,'source_candidate_sha256':file_sha(out/'speaker-candidate.json')}
        return store.commit(item,actor,operation_id,request,updated,context,'audio_refinement_beta1_speaker_enrichment',extra=None)


def assemble(record, evidence,*,with_resolved=False):
    """Keep uncertain attribution and gap reports visible without editing speech.

    A missing-speech report is a model hypothesis, including deliberately excluded
    setup and overlapping context. It is never an instruction to insert words.
    Unusable turn partitions become unknown, never an inferred professor identity.
    Failed/unknown requests and missing paragraphs still prevent handoff.
    """
    import math

    plan = json.loads((evidence / "plan.json").read_text())
    saved={w['id']:json.loads((evidence/w['id']/'candidate.json').read_text()) for w in plan['windows']}
    all_valid={row['paragraph_id']:row for value in saved.values() for row in value['accepted']}
    attribution_files={row['paragraph_id']:evidence/wid/'candidate.json' for wid,value in saved.items() for row in value['accepted']}
    accepted, notices, resolved = [], [], []
    paragraphs = {p["id"]: p for p in record["paragraphs"]}
    for window in plan["windows"]:
        path = evidence / window["id"] / "candidate.json"
        value = saved[window['id']]
        if value["window_id"] != window["id"]:
            raise ValueError("Speaker window changed")
        valid = {p["paragraph_id"]: p for p in value["accepted"]}
        rejected = {p["paragraph_id"]: p for p in value["rejected"]}
        owned = [p["id"] for p in window["paragraphs"]]
        if (
            set(valid) & set(rejected)
            or set(valid) | set(rejected) != set(owned)
            or len(valid) + len(rejected)
            != len(value["accepted"]) + len(value["rejected"])
        ):
            raise ValueError("Speaker paragraph coverage differs from source")
        lo, hi = window["clip_start_ms"], window["clip_end_ms"]
        for pid in owned:
            p = paragraphs[pid]
            if pid in valid:
                row=valid[pid]
                # Saved model answers stay immutable; uncertain voice identity
                # is disclosed as unknown in the Editor candidate.
                accepted.append({**row,'turns':[{**t,'role':'unknown' if t['confidence']=='uncertain' else t['role']} for t in row['turns']]})
            else:
                reason = rejected[pid]["reason"]
                accepted.append(
                    dict(
                        paragraph_id=pid,
                        limitations=reason,
                        turns=[
                            dict(
                                text=p["text"],
                                role="unknown",
                                confidence="uncertain",
                                clip_start_s=(p["start_ms"] - lo) / 1000,
                                clip_end_s=(p["end_ms"] - lo) / 1000,
                                acoustic_basis="講員候選未通過文字保全檢查；保留原文，不推測講員。",
                            )
                        ],
                    )
                )
                notices.append(
                    dict(
                        kind="speaker_uncertain",
                        paragraph_id=pid,
                        start_ms=p["start_ms"],
                        end_ms=p["end_ms"],
                        audio_quote="",
                        reason=reason,
                        evidence_sha256=file_sha(path),
                        status="unreviewed_model_candidate",
                    )
                )
        for gap in value["missing_speech"]:
            start, end = gap["clip_start_s"], gap["clip_end_s"]
            valid_time = (
                all(type(n) in (int, float) and math.isfinite(n) for n in (start, end))
                and 0 <= start < end <= (hi - lo) / 1000
            )
            absolute_start, absolute_end = (
                (lo + round(start * 1000), lo + round(end * 1000))
                if valid_time
                else (lo, hi)
            )
            # A model's missing-speech hypothesis is redundant when the same
            # audible participant turn is already present at this location.
            import unicodedata
            compact=lambda text:''.join(c for c in unicodedata.normalize('NFKC',text) if c.isalnum())
            quote=compact(gap['audio_quote'])
            covered=next((p for p in paragraphs.values() if valid_time and len(quote)>=2
                          and p['start_ms']<=absolute_end+2000 and p['end_ms']>=absolute_start-2000
                          and any(t['role']=='audience' and t['confidence']=='clear' and compact(t['text'])==quote for t in all_valid.get(p['id'],{}).get('turns',[]))),None)
            if covered:
                resolved.append(dict(kind='missing_speech_already_attributed',paragraph_id=covered['id'],raw_gap=gap,evidence_sha256=file_sha(path),attribution_evidence_sha256=file_sha(attribution_files[covered['id']]),basis='同一位置已有逐字相符、聲音清楚的聽眾文字；原始模型提示保留，不要求重複補字。'))
                continue
            association_start = (
                lo + round(start * 1000)
                if type(start) in (int, float)
                and math.isfinite(start)
                and 0 <= start < (hi - lo) / 1000
                else absolute_start
            )
            association_end = absolute_end if valid_time else association_start
            nearest = min(
                paragraphs.values(),
                key=lambda p: max(
                    p["start_ms"] - association_end, association_start - p["end_ms"], 0
                ),
            )
            notices.append(
                dict(
                    kind="possible_missing_speech",
                    paragraph_id=nearest["id"],
                    start_ms=absolute_start,
                    end_ms=absolute_end,
                    audio_quote=gap["audio_quote"],
                    reason=gap["reason"],
                    evidence_sha256=file_sha(path),
                    status="unreviewed_model_candidate",
                    timing_precision=(
                        "model_estimate" if valid_time else "context_window_only"
                    ),
                    reported_clip_range_s=[start, end],
                )
            )
    if [p["paragraph_id"] for p in accepted] != list(paragraphs):
        raise ValueError("Speaker candidate must cover every paragraph once")
    return (accepted, notices,resolved) if with_resolved else (accepted, notices)


def label_rows(source, record, rows, mapping, *, hidden_insertions=None):
    path = Path(source) / "speaker-candidate.json"
    if not path.exists():
        return rows, None
    candidate = json.loads(path.read_text())
    if candidate["source_record_sha256"] != file_sha(
        Path(source) / "full_transcript.json"
    ):
        raise ValueError("Speaker candidate source changed")
    from backend.pipeline.transcript_speaker_pilot import validate
    from backend.pipeline.transcript_speaker_batch import insert_labels

    by_id = {p["id"]: p for p in record["paragraphs"]}
    results = candidate["results"]
    if [p["paragraph_id"] for p in results] != list(by_id):
        raise ValueError("Speaker candidate must cover every paragraph once")
    texts = {}
    for row in results:
        p = by_id[row["paragraph_id"]]
        # Speaker clip times were checked in their original window at generation. Re-check exact text and roles here.
        validate(
            {k: v for k, v in row.items() if k != "paragraph_id"},
            p["text"],
            record["duration_ms"] / 1000,
        )
        if "".join(t["text"] for t in row["turns"]) != p["text"]:
            raise ValueError("Speaker candidate changes spoken text")
        if any(
            t["role"] not in ("professor", "audience", "unknown") for t in row["turns"]
        ):
            raise ValueError("Invalid speaker role")
        turns = row["turns"]
        if hidden_insertions:
            from backend.pipeline.transcript_editorial_soft_deletions import (
                turns_with_hidden_spans,
            )

            turns = turns_with_hidden_spans(
                p["text"], turns, hidden_insertions.get(p["id"], [])
            )
        marked = "".join(t["text"] for t in turns)
        texts[mapping[p["id"]]] = insert_labels(marked, turns)
    return [{**r, "text": texts[r["index"]]} for r in rows], dict(
        source_sha256=file_sha(path),
        pipeline_release=candidate.get('pipeline_release'),
        status="unreviewed_model_candidate",
        spoken_text_unchanged=True,
        notices=[
            {**n, "paragraph_id": mapping[n["paragraph_id"]]}
            for n in candidate.get("notices", [])
        ],
    )
