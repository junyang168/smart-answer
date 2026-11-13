"""Generate up to three playable themes per section from a chapter audio index."""
import argparse
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from backend.pipeline.chapter_exposition import chapter_client

ITEM = {"type": "object", "additionalProperties": False,
        "properties": {"text": {"type": "string"}, "cue": {"type": "integer"}},
        "required": ["text", "cue"]}


def candidates(section: dict) -> list[dict]:
    out = []
    for row in section["sermons"]:
        if not row.get("media_url"):
            continue
        for slide in row.get("slides") or []:
            if any(not clip.get("left_out") and clip["start"] <= slide["at"] < clip["end"]
                   for clip in row["stretches"]):
                out.append({"source_id": row["source_id"], "at": slide["at"],
                            "title": slide["title"], "points": [p["text"] for p in slide["points"]]})
    return out


def generate(index: dict, client: Any) -> dict:
    from backend.pipeline.chapter_slide_claims import digest

    sources = {section["passage"]: candidates(section) for section in index["sections"]}
    sources = {section: options for section, options in sources.items() if options}
    result = {}
    if not sources:
        return {"passage": index["passage"], "model": client.model, "sections": result}
    schema = {"type": "object", "additionalProperties": False, "required": list(sources),
              "properties": {
                  section: {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                      **ITEM, "properties": {**ITEM["properties"], "cue": {
                          "type": "integer", "enum": list(range(1, len(options) + 1))
                      }}
                  }} for section, options in sources.items()
              }}
    prompt = {section: [
        {"cue": number, "title": item["title"], "points": item["points"]}
        for number, item in enumerate(options, start=1)
    ] for section, options in sources.items()}
    answer = client.generate_json(
        "為每個經文段落寫一至三條簡短繁體中文主題，每條不超過 24 字。綜合整段內容，合併重複論證。"
        "主題概括教授講的內容，勿逐張列標題。每條選該段最能代表主題的原聲 cue。"
        "不要補入材料沒有的意思。每個輸入段落恰好回傳一次。",
        json.dumps(prompt, ensure_ascii=False), schema,
    )
    if set(answer) != set(sources):
        raise ValueError("Overview response must cover each playable section exactly once")
    for section, topics in answer.items():
        options = sources[section]
        if not 1 <= len(topics) <= 3:
            raise ValueError(f"Invalid topic count in {section}")
        entries = []
        for item in topics:
            cue, text = item["cue"], item["text"].strip()
            if type(cue) is not int or not 1 <= cue <= len(options) or not 1 <= len(text) <= 32:
                raise ValueError(f"Invalid topic in {section}: cue={cue}, text={text!r}")
            entries.append({"text": text, "source_id": options[cue - 1]["source_id"], "at": options[cue - 1]["at"]})
        if len({entry["text"] for entry in entries}) != len(entries):
            raise ValueError(f"Repeated topic in {section}")
        result[section] = {"source_sha256": digest(options), "topics": entries}
    return {"passage": index["passage"], "model": client.model, "sections": result}


def attach(index: dict, data_root: Path, *, notes: bool = False) -> dict:
    from backend.pipeline.chapter_slide_claims import digest

    path = data_root / "chapter_slides" / index["passage"] / (
        "overview-topics-notes.json" if notes else "overview-topics.json"
    )
    if not path.is_file():
        return index
    record = json.loads(path.read_text())
    for section in index.get("sections", []):
        entry = record["sections"].get(section["passage"])
        if entry and entry["source_sha256"] == digest(candidates(section)):
            section["topics"] = entry["topics"][:3]
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path, help="Saved chapter audio index JSON")
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol")
    args = parser.parse_args()
    result = generate(json.loads(args.index.read_text()), chapter_client(args.model))
    save(result, args.output)


def save(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        json.dump(result, tmp, ensure_ascii=False, indent=2)
    Path(tmp.name).replace(path)


if __name__ == "__main__":
    main()
