"""Exact, non-semantic proof for adding transcript subtitle rows.

A proof never grants Claim approval or changes source/Claim records. It retains
both physical generations and proves that every pre-existing JSON field and row
survived unchanged. Consumers must explicitly supply the admitted proof binding;
no sibling-file discovery or generic SHA bypass is allowed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.sermon_subtitle_persistence import verify_saved_result
from backend.pipeline.source_projection import heading_level, project_script

VERSION = 'wang_source_editorial_transition_v1'


def file_binding(path):
    path = Path(path).resolve()
    return {'path': str(path), 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _read(bound):
    path = Path(bound['path'])
    if not path.is_absolute():
        raise ValueError('editorial proof requires absolute file paths')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != bound['file_sha256']:
        raise ValueError('editorial proof physical SHA drift')
    return json.loads(raw)


def _rows(payload):
    rows = payload.get('script') if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('editorial proof requires transcript JSON rows')
    # Existing editor IDs are provenance, not guaranteed unique. Never
    # renumber them to add metadata: insertion_facts proves the full original
    # sequence exactly, while verify_saved_result checks only NEW IDs for
    # uniqueness and preserves all repeated original IDs and fields.
    if any(row.get('index') is None for row in rows):
        raise ValueError('editorial proof requires existing physical row indexes')
    return rows


def insertion_facts(before, after):
    """Return reproducible facts; reject any edit besides inserted headings."""
    old, new = _rows(before), _rows(after)
    if isinstance(before, dict) != isinstance(after, dict):
        raise ValueError('editorial transition changed transcript wrapper')
    if isinstance(before, dict) and sha256_json({k:v for k,v in before.items() if k != 'script'}) != sha256_json({
            k:v for k,v in after.items() if k != 'script'}):
        raise ValueError('editorial transition changed transcript metadata')
    known = {str(row['index']) for row in old}
    inserted = [row for row in new if str(row['index']) not in known]
    if not inserted:
        raise ValueError('editorial transition inserted no headings')
    verify_saved_result(old, new, expected_insertions=len(inserted))
    if sha256_json(old) != sha256_json([row for row in new if str(row['index']) in known]):
        raise ValueError('editorial transition changed pre-existing JSON rows')
    for row in inserted:
        if (set(row) - {'index','type','text','user_id'} or row.get('type') != 'subtitle'
                or not str(row['index']).startswith('subtitle-')
                or heading_level(row.get('text')) not in {2,3}):
            raise ValueError('editorial transition inserted an invalid subtitle row')
    left, right = project_script(old), project_script(new)
    if (left.body_rows != right.body_rows or left.spoken_rows != right.spoken_rows
            or left.body_sha256 != right.body_sha256
            or left.visual_content_sha256 != right.visual_content_sha256):
        raise ValueError('editorial transition changed body, coordinates or visual content')
    return {'inserted_rows': inserted, 'body_sha256': left.body_sha256,
            'spoken_text_sha256': left.spoken_text_sha256,
            'visual_content_sha256': left.visual_content_sha256,
            'before_editorial_structure_sha256': left.editorial_structure_sha256,
            'after_editorial_structure_sha256': right.editorial_structure_sha256}


def build_proof(*, source, before, after, authorization):
    """Build a sealed proof from frozen before/after file bindings.

    ``before`` may point to an immutable saved copy; ``source_path`` always
    remains the logical source. ``source`` is an explicitly frozen descriptor,
    either registered or file-only; this function never registers a source or
    grants Claim/CVP authority. The caller owns admission and save.
    """
    if (source.get('source_type') != 'sermon_transcript'
            or source.get('locator_space') != 'spoken_body_v1'):
        raise ValueError('editorial transition requires an explicit body-coordinate transcript descriptor')
    if not authorization or before['file_sha256'] != source.get('source_file_sha256'):
        raise ValueError('editorial transition lacks exact registered before generation/authority')
    facts = insertion_facts(_read(before), _read(after))
    if facts['body_sha256'] != source.get('source_body_sha256'):
        raise ValueError('editorial transition body differs from registered source')
    if before['file_sha256'] == after['file_sha256']:
        raise ValueError('editorial transition requires different physical generations')
    value = {'schema_version': VERSION, 'source_id': source['source_id'],
             'source_path': source['source_path'], 'before': before, 'after': after,
             'authorization': authorization, 'facts': facts,
             'semantic_approval': False, 'claim_qualification_created': False}
    return value | {'artifact_sha256': sha256_json(value)}


def verify_proof(proof_binding, *, source, current_binding=None):
    """Re-open all generations; return current binding only after exact proof."""
    proof = _read(proof_binding)
    expected = build_proof(source=source, before=proof['before'], after=proof['after'],
                           authorization=proof['authorization'])
    if proof != expected:
        raise ValueError('editorial transition proof seal or facts drift')
    current = current_binding or file_binding(source['source_path'])
    if (str(Path(current['path']).resolve()) != str(Path(source['source_path']).resolve())
            or current['file_sha256'] != proof['after']['file_sha256']):
        raise ValueError('editorial transition current physical generation drift')
    # ``after`` can also be a frozen archive. Compare bytes by SHA against the
    # actual current logical path; never redirect packet reads to an archive.
    if file_binding(current['path']) != current:
        raise ValueError('editorial transition live binding drift')
    return current


def transition_root(root=None):
    if root is not None:
        return Path(root)
    from backend.api.config import DATA_BASE_PATH
    return DATA_BASE_PATH / 'wang-knowledge-platform/staging/source-editorial-transitions/v1'


def receipt_path(binding, *, root=None):
    logical = str(Path(binding['path']).resolve())
    digest = binding['file_sha256']
    import re
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('editorial transition requires an exact SHA256')
    return transition_root(root) / hashlib.sha256(logical.encode()).hexdigest() / (digest + '.json')


def _write_bytes_once(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != raw:
                    raise ValueError('immutable editorial transition artifact differs')
            else:
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def publish_transition(*, source, before_raw, after_raw, authorization, root=None):
    """Publish exact frozen proof before the caller's compare-and-swap save.

    An interrupted save is harmless: the receipt applies only to the exact
    planned after SHA. No SQL record or semantic authority is changed here.
    """
    before_sha = hashlib.sha256(before_raw).hexdigest()
    after_sha = hashlib.sha256(after_raw).hexdigest()
    logical = str(Path(source['source_path']).resolve())
    bound = {'path': logical, 'file_sha256': before_sha}
    path = receipt_path(bound, root=root)
    # Validate first so even a rejected plan does not leave partial archives.
    insertion_facts(json.loads(before_raw), json.loads(after_raw))
    if before_sha != source.get('source_file_sha256'):
        raise ValueError('editorial transition before bytes differ from registered generation')
    archives = path.parent / 'raw'
    before_path, after_path = archives / (before_sha+'.json'), archives / (after_sha+'.json')
    _write_bytes_once(before_path, before_raw)
    _write_bytes_once(after_path, after_raw)
    registered = dict(source, source_path=logical)
    proof = build_proof(source=registered, before=file_binding(before_path),
                        after=file_binding(after_path), authorization=authorization)
    receipt = {'source': registered, 'proof': proof}
    receipt['artifact_sha256'] = sha256_json(receipt)
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2).encode()
    _write_bytes_once(path, encoded)
    return file_binding(path)


def verify_current_source_binding(binding, *, root=None):
    """Resolve only subtitle insertions from an exact old source binding.

    Actual old bytes, full JSON payload equality, stable row coordinates and
    live after bytes provide authority, not the presence of a receipt. The
    bounded chain supports another later subtitle insertion without rewriting
    either old proofs or original qualification requests.
    """
    path = Path(binding['path'])
    if not path.is_absolute():
        raise ValueError('editorial transition requires absolute source path')
    current = file_binding(path)
    expected = dict(binding)
    seen = set()
    for _ in range(16):
        if expected['file_sha256'] == current['file_sha256']:
            return current
        digest = expected['file_sha256']
        if digest in seen:
            raise ValueError('editorial transition cycle')
        seen.add(digest)
        try:
            receipt = json.loads(receipt_path(expected, root=root).read_bytes())
        except OSError as exc:
            raise ValueError('source physical SHA drift without an exact editorial transition') from exc
        if receipt.get('artifact_sha256') != sha256_json({k:v for k,v in receipt.items() if k!='artifact_sha256'}):
            raise ValueError('editorial transition receipt seal drift')
        source, proof = receipt['source'], receipt['proof']
        if (str(Path(source['source_path']).resolve()) != str(path.resolve())
                or source['source_file_sha256'] != digest):
            raise ValueError('editorial transition wrong source generation/path')
        rebuilt = build_proof(source=source, before=proof['before'], after=proof['after'],
                              authorization=proof['authorization'])
        if rebuilt != proof:
            raise ValueError('editorial transition proof seal or facts drift')
        expected = {'path':str(path), 'file_sha256':proof['after']['file_sha256']}
    raise ValueError('editorial transition chain exceeds 16 steps')
