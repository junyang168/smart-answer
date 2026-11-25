"""Read a teaching unit across recordings without renumbering its source sentences."""

from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.pipeline import claim_creation_flash as creation
from backend.pipeline import detailed_knowledge_extraction_runner as extraction
from backend.pipeline import evidence_refresh as artifacts
from backend.pipeline.extraction_sections import Section
from backend.pipeline.source_projection import project_script, validate_visual_source_attestations


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceRange(StrictModel):
    first: str
    last: str


class UnitSource(StrictModel):
    source_id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ranges: list[SourceRange] = Field(min_length=1)
    visual_attestations: dict[str, str] = Field(default_factory=dict)


class UnitManifest(StrictModel):
    sources: list[UnitSource] = Field(min_length=1)


@dataclass
class Reading:
    sentences: list
    sources: list
    paths: dict
    empty_paragraphs: list

    def verify(self):
        for path, expected in self.paths.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"unit source changed: {path}")


def read_unit(manifest_path):
    path = Path(manifest_path).resolve()
    manifest = UnitManifest.model_validate_json(path.read_bytes())
    return _read_sources(manifest, path.parent)


def _read_sources(manifest, base_dir):
    result = Reading([], [], {}, [])
    identities = set()
    for item in manifest.sources:
        source_path = (base_dir / item.file).resolve()
        if item.source_id in identities or source_path in result.paths:
            raise ValueError("duplicate unit source")
        identities.add(item.source_id)
        raw = source_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.sha256:
            raise ValueError(f"unit source changed: {source_path}")
        result.paths[source_path] = item.sha256
        source = json.loads(raw)
        projection = project_script(source["script"])
        validate_visual_source_attestations(projection, item.visual_attestations)
        visuals = {v.locator: v for v in projection.visual_blocks}
        paragraphs = projection.body_rows
        sentences = extraction.section_sentences(source, Section(1, 0, len(paragraphs), "complete"))
        # Endpoints can be full paragraphs or original sentence IDs. Empty rows
        # have an address as well, even though there is nothing to send to a model.
        by_row = {}
        for sentence in sentences:
            by_row.setdefault(sentence.segment_index.split("/")[0], []).append(sentence)
        entries, bounds = [], {}
        for index, paragraph in enumerate(paragraphs):
            key = extraction.segment_locator(index)
            start = len(entries)
            for sentence in by_row.get(key, [None]):
                if sentence:
                    bounds[sentence.sentence_id] = (len(entries), len(entries))
                entries.append((key, paragraph, sentence))
            bounds[key] = (start, len(entries) - 1)
        previous = -1
        for span in item.ranges:
            if span.first not in bounds or span.last not in bounds:
                raise ValueError(f"unknown source range: {span}")
            start, end = bounds[span.first][0], bounds[span.last][1]
            if start <= previous or end < start:
                raise ValueError("source ranges must be ordered and disjoint")
            previous = end
            for key, paragraph, sentence in entries[start:end + 1]:
                anchor = dict(source_id=item.source_id, paragraph=key, time=paragraph.get("start_timeline"))
                if sentence is None:
                    result.empty_paragraphs.append(anchor)
                    continue
                text = sentence.text
                if sentence.source_modality == "visual":
                    text = creation.diagram_text(visuals[sentence.segment_index])
                result.sentences.append(dict(**anchor, sentence_id=sentence.sentence_id,
                    start=sentence.char_start, end=sentence.char_end, modality=sentence.source_modality, text=text))
        result.sources.append(dict(source_id=item.source_id, file_sha256=item.sha256,
            body_sha256=projection.body_sha256, ranges=[r.model_dump() for r in item.ranges],
            visual_attestations=item.visual_attestations))
    if not result.sentences:
        raise ValueError("unit has no source sentences")
    return result


TEXT = {"type": "string"}
INTEGER = {"type": "integer"}
PLAN = creation.schema("unit_plan", {"runs": creation.array(creation.object_schema({
    "first": INTEGER, "group": TEXT, "question": TEXT})), "questions": creation.array(
    creation.object_schema({"at": INTEGER, "question": TEXT}))})
PROMPT = (
    "通读给定连续教学材料，依教授实际要回答的问题划论证组。每句前的整数是位置。"
    "runs只列本批每次换组时的起句（含首句），按升序。group是可重用的简短组名，question是该组的论题。"
    "同一问题的开题、续讲、释证和归结应重用同一组名，可中间插入其他组。独立插问或独立教导分组。"
    "录音换讲不是换组依据。场务可单列。questions列教授实际留待后来回答的问题和首次提出它的句号，"
    "包括留作业和留到下讲才解的困难；即使本批后面已回答也保留开题位置。不要预写答案。"
)


def plan_unit(reading, root, ports):
    """Split transport by actual request size while preserving unit/group identity."""
    runs, questions = [], []
    start = 0
    batch = 0
    while start < len(reading.sentences):
        batch += 1
        prior = {r["group"]: r["question"] for r in runs}
        prior_questions = [dict(**q, quote=reading.sentences[q["at"] - 1]["text"]) for q in questions]

        def payload(end):
            lines = []
            previous_source = None
            for i in range(start, end):
                row = reading.sentences[i]
                if previous_source != row["source_id"]:
                    lines.append(["source", row["source_id"]])
                    previous_source = row["source_id"]
                lines.append([i + 1, row["text"]])
            return {"earlier_groups": prior, "earlier_questions": prior_questions, "sentences": lines}

        def size(end):
            req = dict(identity=ports.identity["gpt"], system=PROMPT,
                       input={"task": "creation_unit_plan", **payload(end)}, schema=PLAN)
            return len(json.dumps(req, ensure_ascii=False).encode())

        low, high = start + 1, len(reading.sentences)
        if size(low) > creation.f.GPT_BYTES:
            raise ValueError("unit planning request cannot fit one sentence with its reading context")
        while low < high:
            mid = (low + high + 1) // 2
            if size(mid) <= creation.f.GPT_BYTES:
                low = mid
            else:
                high = mid - 1
        end = low
        batch_dir = root / "plan" if (batch == 1 and end == len(reading.sentences)) else root / f"plan-{batch:02d}"
        answer = creation.call(batch_dir, ports, "creation_unit_plan", payload(end), PLAN,
                               PROMPT, reading.verify)
        new = answer["runs"]
        starts = [r["first"] for r in new]
        if (not starts or starts[0] != start + 1 or starts != sorted(set(starts)) or starts[-1] > end
                or any(not r["group"].strip() or not r["question"].strip() for r in new)):
            raise ValueError("invalid unit group boundaries")
        for q in answer["questions"]:
            if not start < q["at"] <= end or not q["question"].strip():
                raise ValueError("unbound unit question")
        runs.extend(new)
        questions.extend(answer["questions"])
        start = end
    return {"runs": runs, "questions": questions}


def run(manifest_path, root, ports):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "unit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest_path = Path(manifest_path).resolve()
        manifest = UnitManifest.model_validate_json(manifest_path.read_bytes())
        reading = _read_sources(manifest, manifest_path.parent)
        artifacts.save(root / "unit-sources.json", {"sources": reading.sources,
            "sentences": reading.sentences, "empty_paragraphs": reading.empty_paragraphs})
        for item in manifest.sources:
            item.file = str((Path(manifest_path).resolve().parent / item.file).resolve())
        artifacts.save(root / "unit-manifest.json", manifest.model_dump())
        plan = plan_unit(reading, root, ports)
        reading.verify()
        result = dict(version="claim_unit_plan_v1", status="unit_planned", **plan,
                      sources_sha256=artifacts.digest(reading.sources),
                      sentences_sha256=artifacts.digest(reading.sentences), production_writes=0)
        artifacts.save(root / "unit-plan.json", result)
        artifacts.save(root / "unit-plan-receipt.json", {"plan_sha256": artifacts.digest(result)})
        return result


@dataclass
class UnitContext:
    contract = "unit_context_source_spans_v1"

    reading: Reading
    plan: dict
    source_id: str

    @classmethod
    def load(cls, folder, source_id, source_path):
        folder = Path(folder)
        reading = read_unit(folder / "unit-manifest.json")
        plan = json.loads((folder / "unit-plan.json").read_bytes())
        receipt = json.loads((folder / "unit-plan-receipt.json").read_bytes())
        if receipt != {"plan_sha256": artifacts.digest(plan)}:
            raise ValueError("saved unit plan changed")
        if (plan["version"] != "claim_unit_plan_v1" or plan["status"] != "unit_planned"
                or plan["sources_sha256"] != artifacts.digest(reading.sources)
                or plan["sentences_sha256"] != artifacts.digest(reading.sentences)):
            raise ValueError("unit plan and current sources differ")
        match = [s for s in reading.sources if s["source_id"] == source_id]
        if not match or match[0]["file_sha256"] != hashlib.sha256(Path(source_path).read_bytes()).hexdigest():
            raise ValueError("current lecture is not the planned unit source")
        return cls(reading, plan, source_id)

    def for_paragraphs(self, owned):
        """Supply the original lines for the same question, including later lectures."""
        sentences = self.reading.sentences
        runs = self.plan["runs"]
        sections = [(r, sentences[r["first"] - 1:end]) for r, end in
                    zip(runs, [r["first"] - 1 for r in runs[1:]] + [len(sentences)], strict=True)]
        groups = {r["group"] for r, rows in sections
                  if any(row["source_id"] == self.source_id and row["paragraph"] in owned for row in rows)}
        if not groups:
            return {}
        # Group context is read-only. The existing extraction owns only its own
        # lecture sentences and cannot attach another lecture's words as its own.
        passages = []
        owned_spans = []
        ordinals = set()
        for run, rows in sections:
            if run["group"] not in groups:
                continue
            for i, row in enumerate(rows, run["first"]):
                ordinals.add(i)
                local = row["source_id"] == self.source_id and row["paragraph"] in owned
                target = owned_spans if local else passages
                if (not target or target[-1]["last_position"] != i - 1
                        or target[-1]["group"] != run["group"]
                        or target[-1]["source_id"] != row["source_id"]
                        or target[-1]["paragraph"] != row["paragraph"]
                        or target[-1]["end_char"] != row["start"]
                        or target[-1]["modality"] != row["modality"]):
                    target.append(dict(source_id=row["source_id"], paragraph=row["paragraph"],
                        group=run["group"], first_sentence=row["sentence_id"], modality=row["modality"],
                        start_char=row["start"]))
                target[-1].update(last_sentence=row["sentence_id"], last_position=i, end_char=row["end"])
                if not local:
                    target[-1]["text"] = target[-1].get("text", "") + row["text"]
        questions = []
        for question in self.plan["questions"]:
            if question["at"] in ordinals:
                row = sentences[question["at"] - 1]
                questions.append({"question": question["question"], "source_id": row["source_id"],
                                  "sentence_id": row["sentence_id"], "quote": row["text"]})
        return {"unit_context": {"instruction": "只引用本组负责的原句。其他讲的原话供理解承接；"
                 "留题须保留其原处，即使这里读到了后讲答案。",
                 "groups": sorted(groups), "questions": questions, "owned_spans": owned_spans,
                 "context_passages": passages}}
