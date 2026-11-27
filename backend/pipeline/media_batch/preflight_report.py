"""Private, SHA-bound preflight reports; never take a filesystem path from a client."""

import hashlib
import json
from pathlib import Path


def load(root, batch):
    config = Path(root) / "config/media-batch-preflight.json"
    if not config.is_file():
        return None
    binding = json.loads(config.read_text()).get(batch)
    if not binding:
        return None
    values = []
    for kind in ("json", "html"):
        raw = Path(binding[kind]["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding[kind]["sha256"]:
            raise ValueError("Preflight report changed")
        values.append(raw)
    report = json.loads(values[0])
    if report.get("schema_version") != "media-preflight-report.v1":
        raise ValueError("Invalid preflight report")
    return dict(
        summary=dict(
            href=f"/api/sc_api/media_batches/{batch}/preflight",
            source_files=report["source_files"],
            counts=report["counts"],
            model_calls=report["model_calls"],
        ),
        html=values[1],
    )
