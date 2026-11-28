"""Extract and attach teaching slides to an existing Editor draft."""

import argparse
import copy
import fcntl
import json
import math
import re
import subprocess
import unicodedata
from pathlib import Path

import cv2
import numpy as np

from backend.api.sc_api.transcript_review import ReviewStore, digest
from backend.pipeline import slide_editor_alignment as alignment
from backend.pipeline.slide_source_ink import source_ink_svg
from backend.pipeline.editor_slide_display import measure
from backend.pipeline.media_operations import classify_slides
from backend.pipeline.slide_change_pilot import (
    file_sha,
    object_sha,
    read_json,
    sample_and_detect,
    save_json,
)
from backend.pipeline.slide_visual_evidence import extract, render_svg
from backend.pipeline.source_projection import visual_source_blocks


def snapshot(store, item, out, source):
    script, context = out / "script.json", out / "context.json"
    path = out / "snapshot.json"
    if path.exists():
        prior = read_json(path)
        if (prior["item"], prior["source"], prior["script"], prior["context"]) != (
            item,
            source,
            file_sha(script),
            file_sha(context),
        ):
            raise ValueError("Work directory belongs to another input")
        return prior
    with store.lock(item):
        rows, ctx = store.load(item)
        if ctx.get("source_video_sha256") and ctx["source_video_sha256"] != source:
            raise ValueError("Video does not match the Editor source")
        script.write_bytes((store.root / "script_review" / f"{item}.json").read_bytes())
        context.write_bytes((store.folder(item) / "context.json").read_bytes())
        version = digest(store.document_value(rows, ctx))
    record = dict(
        item=item,
        source=source,
        script=file_sha(script),
        context=file_sha(context),
        version=version,
    )
    save_json(path, record)
    return record


def audit_material(part, evidence, bindings):
    ids = {b["asset_id"] for b in bindings if b["important"]}
    dest = part / "fidelity"
    assets = []
    for asset in evidence["assets"]:
        if asset["asset_id"] in ids:
            asset = copy.deepcopy(asset)
            asset["original_image"] = str(part / "evidence" / asset["original_image"])
            if asset["svg"]:
                asset["svg"]["path"] = str(part / "evidence" / asset["svg"]["path"])
            assets.append(asset)
    if assets:
        save_json(dest / "slide_evidence.json", {**evidence, "assets": assets})
        alignment.verify_visual_fidelity(dest, auto_repair=False)


def produce(video, out, source):
    duration = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ],
        text=True,
    )
    sample_and_detect(
        video,
        out / "slides",
        start_ms=0,
        end_ms=math.ceil(float(duration) * 1000),
        include_initial=True,
    )
    save_json(out / "master.json", {"source_sha256": source})
    report = classify_slides(out)
    events = report["events"]
    for start in range(0, len(events), 18):
        part = out / f"part-{start // 18:04d}"
        batch = [
            {**e, "slide_image": str(out / "slides" / e["slide_image"])}
            for e in events[start : start + 18]
        ]
        save_json(part / "report.json", {"source_sha256": source, "events": batch})
        evidence = extract(
            part,
            part / "evidence",
            render_all_svg=True,
            white_background=True,
            thinking_level="LOW",
            repair_invalid_output=True,
        )
        decisions = alignment.align_and_filter_important_slides(
            part / "evidence",
            out / "script.json",
            part / "alignment",
            context_path=out / "context.json",
        )
        audit_material(part, evidence, decisions["bindings"])


def effective_alignment_requests(out):
    """Count one authorized answer, preserving its unknown parent as history."""
    from backend.pipeline.media_batch.timeout_recovery import covered
    from backend.pipeline.media_batch.credit_recovery import setup_covered
    for path in (out/'calls').glob('*/request.json'):
        request=read_json(path)
        if request.get('stage')!='slide_editor_alignment':continue
        state=path.with_name('state.json')
        if state.exists() and read_json(state).get('status')=='sent_outcome_unknown':
            if covered(out,path.parent,None):continue
            raise ValueError('Unresolved alignment request remains quarantined')
        if state.exists() and read_json(state).get("status")=="rejected":
            if setup_covered(out,path.parent):continue
            raise ValueError("Unresolved rejected alignment request remains quarantined")
        yield request


def collect(run, snap):
    report = read_json(run / "slides/report.json")
    if report["source_sha256"] != snap["source"]:
        raise ValueError("Video identity mismatch")
    events = {e["id"]: e for e in report["events"]}
    if len(events) != len(report["events"]):
        raise ValueError("Duplicate classified event ID")
    decisions, assets = {}, {}
    for part in sorted(run.glob("part-*")):
        ev_file = part / "evidence/slide_evidence.json"
        ev = read_json(ev_file)
        aligned = read_json(part / "alignment/editor_slide_bindings.json")
        if (
            aligned["transcript_sha256"] != snap["script"]
            or aligned["slide_evidence_sha256"] != file_sha(ev_file)
            or ev["source_video_sha256"] != snap["source"]
        ):
            raise ValueError("Alignment input changed")
        covered = []
        for request in effective_alignment_requests(part/'alignment'):
            if request["context_sha256"] != snap["context"]:
                raise ValueError("Editor timing context changed")
            covered.extend(request["event_ids"])
        if sorted(covered) != sorted(b["event_id"] for b in aligned["bindings"]):
            raise ValueError("Incomplete alignment requests")
        audited = {}
        if any(b["important"] for b in aligned["bindings"]):
            audit_file = part / "fidelity/slide_evidence.json"
            audit = read_json(part / "fidelity/fidelity_audit.json")
            if file_sha(audit_file) != audit["slide_evidence_sha256"]:
                raise ValueError("Audit input changed")
            audited = {a["asset_id"]: a for a in read_json(audit_file)["assets"]}
            verdicts = {r["asset_id"]: r for r in audit["results"]}
            for aid, a in audited.items():
                a["audit_severity"] = verdicts[aid]["severity"]
                if (a["fidelity_review"]["status"] == "passed") != verdicts[aid][
                    "passed"
                ] or object_sha(a["content"]) != a["content_sha256"]:
                    raise ValueError("Fidelity result does not match audited content")
        for b in aligned["bindings"]:
            sid = b["event_id"]
            e = events[sid]
            if sid in decisions or (b["time_ms"], b["capture_ms"], b["asset_id"]) != (
                e["time_ms"],
                e["capture_ms"],
                "a" + e["slide_sha256"],
            ):
                raise ValueError(f"Duplicate or changed slide occurrence {sid}")
            decisions[sid] = b
            if b["important"]:
                a = copy.deepcopy(audited[b["asset_id"]])
                a["original_image"] = str(part / "fidelity" / a["original_image"])
                if a["svg"]:
                    a["svg"]["path"] = str(part / "fidelity" / a["svg"]["path"])
                if file_sha(Path(a["original_image"])) != e["slide_sha256"]:
                    raise ValueError(f"Original image changed for {sid}")
                assets[b["asset_id"]] = a
    skipped = set(events) - decisions.keys()
    selection = run / "selection.json"
    if skipped != (
        set(read_json(selection)["short_progressive_reveals"])
        if selection.exists()
        else set()
    ):
        raise ValueError("Incomplete event coverage")
    return events, decisions, assets


def svg_for(a):
    return render_svg(
        a["content"],
        a["image_width"],
        a["image_height"],
        a["image_sha256"],
        white_background=True,
        allow_text_only=True,
    )


def punctuation_width_only(severity, issues):
    """Conservatively accept a width-only report; all other words require review."""
    if severity != "minor" or not issues:
        return False
    for issue in issues:
        text = unicodedata.normalize("NFKC", issue).strip().lower()
        if not re.search(r"全形|半形|全角|半角|full[- ]?width|half[- ]?width", text):
            return False
        if not re.search(r"標點|标点|冒號|逗號|句號|punctuation|colon|comma", text):
            return False
        tokens = (
            r"標點符號|標點|标点符号|标点|冒號|逗號|句號|分號|問號|驚嘆號|"
            r"全形|半形|全角|半角|誤提取為|被擷取為|被轉為|轉為|混用|與|及|和|僅|部分|差異|"
            r"full[- ]?width|half[- ]?width|punctuation|colon|comma|instead of|only|and|"
            r"[\s,、.:;。]"
        )
        if re.sub(tokens, "", text):
            return False
    return True


def display_assets(assets, out):
    svgs = {}
    for aid, a in assets.items():
        audit = a["fidelity_review"]
        # Require a sole width-of-punctuation finding. Keep the original
        # negative verdict and OCR text available to reviewers.
        width_only = punctuation_width_only(a.get("audit_severity"), audit["issues"])
        if (audit["status"] != "passed" and not width_only) or not a["svg"]:
            continue
        for region in a["content"]["text_regions"]:
            x, y, r, b = region["bbox"]
            chars = region["text"]
            if (
                a["content"]["has_diagram"]
                and 2 <= len(chars) <= 5
                and all(unicodedata.east_asian_width(c) in {"W", "F"} for c in chars)
                and (b - y) * a["image_height"] > 1.7 * (r - x) * a["image_width"]
            ):
                region["text"] = "\n".join(chars)
        svgs[aid] = svg_for(a)
        if width_only or missing_highlight(a):
            traced = source_ink_svg(
                cv2.imread(a["original_image"]), a["content"], a["image_sha256"]
            )
            if traced:
                svgs[aid] = traced
                a["display_note"] = "source-glyphs.v1"
            elif width_only:
                del svgs[aid]
    checks = measure(svgs)
    for aid, issues in checks.items():
        shifts = {}
        a = assets[aid]
        for box in issues["outside"]:
            if -6 <= box["y"] < 0:
                shifts[box["id"]] = max(
                    shifts.get(box["id"], 0),
                    math.ceil((1 - box["y"]) * 1000 / a["image_height"]),
                )
        if shifts:
            for r in a["content"]["text_regions"]:
                if r["id"] in shifts and r["bbox"][3] + shifts[r["id"]] <= 1000:
                    r["bbox"][1] += shifts[r["id"]]
                    r["bbox"][3] += shifts[r["id"]]
            svgs[aid] = svg_for(a)
    checks = measure(svgs)
    for aid, svg in svgs.items():
        a = assets[aid]
        path = out / f"{aid}.svg"
        path.write_text(svg, encoding="utf-8")
        facts = visual_source_blocks(svg, segment_index="S0001")[0]
        a["content_sha256"] = object_sha(a["content"])
        a["svg"].update(
            path=str(path),
            sha256=file_sha(path),
            canonical_sha256=facts.canonical_sha256,
            literal_facts=list(facts.facts),
        )
    return checks


def comparison_text(asset):
    text = unicodedata.normalize("NFKC", asset["visible_text"])
    return "".join(c for c in text if c.isalnum() or unicodedata.combining(c))


def same_frame(left, right, prior, current):
    if left.shape == right.shape:
        diff = cv2.absdiff(left, right).max(axis=2)
        if np.count_nonzero(diff > 45) / diff.size < 0.0005:
            return True
    if comparison_text(prior) != comparison_text(current):
        return False

    # OCR text alone would collapse deliberate changes of emphasis. Compare
    # full-colour thumbnails after smoothing the one-pixel crop jitter.
    def thumbnail(frame):
        resized = cv2.resize(frame, (160, 120), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(resized, (3, 3), 0)

    a, b = thumbnail(left), thumbnail(right)
    diff = cv2.absdiff(a, b).max(axis=2)
    return float(np.mean(diff)) < 5 and np.count_nonzero(diff > 50) < 0.003 * diff.size


def preserves_previous_page(prior, later):
    """A later build must retain all prior text at the same coloured locations."""
    if any(a["content"]["has_diagram"] for a in (prior, later)):
        return False
    old_lines = [
        comparison_text({"visible_text": r["text"]})
        for r in prior["content"]["text_regions"]
    ]
    new = comparison_text(later)
    if len(new) <= len(comparison_text(prior)) or len(old_lines) < 2:
        return False
    # Completing a missing scripture citation can supersede the incomplete
    # view. A new teaching line can have independent value in a progressive build.
    added_citation = any(
        re.fullmatch(r"[一-龥]{1,8}\s*\d+\s*[:：]\s*\d+(?:[-–]\d+)?", r["text"].strip())
        and comparison_text({"visible_text": r["text"]}) not in comparison_text(prior)
        for r in later["content"]["text_regions"]
    )
    if not added_citation:
        return False
    pos = 0
    for line in old_lines:
        start = new.find(line, pos)
        if not line or start < 0:
            return False
        pos = start + len(line)
    width, height = prior["image_width"], prior["image_height"]

    def original(asset):
        im = cv2.imread(asset["original_image"])
        return cv2.GaussianBlur(cv2.resize(im, (width, height)), (3, 3), 0)

    old, new_frame = original(prior), original(later)
    for region in prior["content"]["text_regions"]:
        x, y, r, b = (
            np.array(region["bbox"]) * [width, height, width, height] / 1000
        ).astype(int)
        patch = old[y:b, x:r]
        if not patch.size or patch.std(axis=(0, 1)).max() < 12:
            return False
        near = new_frame[max(0, y - 3) : b + 3, max(0, x - 3) : r + 3]
        if cv2.matchTemplate(near, patch, cv2.TM_CCOEFF_NORMED).max() < 0.92:
            return False
    return True


def missing_highlight(asset):
    """Identify yellow source ink that no yellow OCR region accounts for."""
    frame = cv2.imread(asset["original_image"])
    h, s, v = cv2.split(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV))
    yellow = (h >= 19) & (h <= 36) & (s > 90) & (v > 155)
    height, width = h.shape
    boxes = []
    for region in asset["content"]["text_regions"]:
        x, y, right, bottom = region["bbox"]
        box = (
            slice(round(y * height / 1000), round(bottom * height / 1000)),
            slice(round(x * width / 1000), round(right * width / 1000)),
        )
        colour = region["color"].lstrip("#")
        pixel = np.array(
            [[[int(colour[k : k + 2], 16) for k in (4, 2, 0)]]], dtype=np.uint8
        )
        hue, saturation, _ = cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]
        if 16 <= hue <= 40 and saturation > 48:
            yellow[box] = False
        else:
            boxes.append(box)
    for box in boxes:
        count = int(yellow[box].sum())
        if count > max(30, height * width * 0.001) and count > 0.22 * np.count_nonzero(
            v[box] > 155
        ):
            return True
    return False


def assemble(run, snap, out):
    events, decisions, assets = collect(run, snap)
    checks = display_assets(assets, out)
    paragraph_ids = read_json(out / "context.json")["paragraph_bindings"]
    paragraphs = alignment.load_transcript_paragraphs(
        out / "script.json", context_path=out / "context.json"
    )
    numbers = {p["paragraph_id"]: n for n, p in enumerate(paragraphs, 1)}
    selected, occurrences, report, kept = [], [], [], {}
    by_paragraph = {}
    for sid, e in events.items():
        b = decisions.get(sid)
        reason = "previously filtered short reveal"
        if b:
            reason = b["category"]
        if b and b["important"]:
            aid, pid = b["asset_id"], b["aligned_paragraph_id"]
            a = assets[aid]
            reasons = a["fidelity_review"]["issues"]
            check = checks.get(aid)
            if pid not in paragraph_ids or not b["helps_reader_understand"]:
                reason = "missing placement"
            elif not check:
                reason = "fidelity: " + (" / ".join(reasons) or "no verified SVG")
            elif a.get("display_note") != "source-glyphs.v1" and missing_highlight(a):
                reason = "source highlight missing from SVG"
            elif check["overlaps"] or check["outside"]:
                reason = "layout: " + json.dumps(check, ensure_ascii=False)
            else:
                frame = cv2.imread(a["original_image"])
                peers = by_paragraph.setdefault(pid, [])
                duplicate = next(
                    (
                        other
                        for other, image, prior in peers
                        if same_frame(image, frame, prior, a)
                    ),
                    None,
                )
                if duplicate:
                    reason = "duplicate of " + duplicate
                else:
                    peers.append((sid, frame, a))
                    reason = "attached"
                    kept[aid] = a
                    selected.append(b)
                    occurrences.append(
                        {
                            k: b[k]
                            for k in ("event_id", "asset_id", "time_ms", "capture_ms")
                        }
                        | {"kind": e["kind"]}
                    )
        report.append(
            dict(
                event_id=sid,
                time_ms=e["time_ms"],
                capture_ms=e["capture_ms"],
                reason=reason,
                title=e["label"],
                display_note=assets[b["asset_id"]].get("display_note")
                if b and b["important"]
                else None,
                helps_reader_understand=b["helps_reader_understand"] if b else "",
                editor_paragraph=numbers.get(b["aligned_paragraph_id"]) if b else None,
                paragraph=b["aligned_paragraph_id"] if b else None,
            )
        )
    superseded = {}
    positions = {row["event_id"]: i for i, row in enumerate(report)}
    for old, new in zip(selected, selected[1:]):
        sid, nid = old["event_id"], new["event_id"]
        interval = report[positions[sid] + 1 : positions[nid] + 1]
        if (
            old["aligned_paragraph_id"] == new["aligned_paragraph_id"]
            and old["category"] != "independent_build_step"
            and all(events[r["event_id"]]["kind"] == "build" for r in interval)
            and preserves_previous_page(kept[old["asset_id"]], kept[new["asset_id"]])
        ):
            superseded[sid] = nid
            report[positions[sid]]["reason"] = "duplicate of " + nid
    selected = [b for b in selected if b["event_id"] not in superseded]
    occurrences = [o for o in occurrences if o["event_id"] not in superseded]
    used = {b["asset_id"] for b in selected}
    kept = {k: v for k, v in kept.items() if k in used}
    save_json(
        out / "slide_evidence.json",
        dict(
            source_video_sha256=snap["source"],
            assets=list(kept.values()),
            occurrences=occurrences,
        ),
    )
    save_json(out / "editor_slide_bindings.json", dict(bindings=selected))
    save_json(out / "report.json", dict(events=report, selected=len(selected)))
    return len(selected)


def run(args):
    store = ReviewStore(args.root.resolve())
    review = store.folder(args.item)
    source = file_sha(args.video)
    record = review / "source-record.json"
    if record.is_file():
        media = read_json(record)
        if (
            media.get("legacy_media_filename")
            and media["legacy_media_filename"] != args.video.name
        ):
            raise ValueError("Video filename differs from the Editor source record")
        if (
            media.get("legacy_media_size")
            and media["legacy_media_size"] != args.video.stat().st_size
        ):
            raise ValueError("Video size differs from the Editor source record")
    if args.sha256 != source:
        raise ValueError("Video does not match the expected source SHA")
    out = args.out.resolve()
    if out.is_relative_to(store.root.resolve()) or (
        args.replay and out == args.replay.resolve()
    ):
        raise ValueError("Use a separate work directory for generated files")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "workflow.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        snap = snapshot(store, args.item, out, source)
        if args.replay is None:
            produce(args.video, out, source)
        count = assemble(args.replay.resolve() if args.replay else out, snap, out)
        if args.apply and count:
            alignment.import_bindings_to_review(
                out,
                out / "editor_slide_bindings.json",
                review,
                expected_version=snap["version"],
            )
        return {"selected": count, "report": str(out / "report.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("video", "out", "root"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--item", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument(
        "--replay",
        type=Path,
        help="Read existing model stages without calling providers",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Versioned attachment to the Editor draft"
    )
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False))


if __name__ == "__main__":
    main()
