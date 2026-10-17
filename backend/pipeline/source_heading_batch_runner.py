"""Prepare missing editorial headings without extracting or rewriting Claims.

The batch uses the existing subtitle generator, application audit and CAS writer.
Its frozen manifest and per-source outputs make a resumed run consume the same
inputs, rather than turning a new inventory into a fresh model budget.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, record_content_sha, sha256_json,
)
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.sermon_subtitle_persistence import (
    apply_insertions, payload_with_rows, transcript_rows,
)
from backend.pipeline.source_heading_inventory import build_source_heading_inventory
from backend.pipeline.subtitle_generation import PROMPT_PATH
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable


SCHEMA = 'wang_source_heading_batch_v1'


def batch_manifest(inventory, *, actor_id, model, effort, priority_only=False, limit=None):
    if inventory.get('blocking_issues'):
        raise ValueError('heading inventory has unresolved current input integrity errors')
    if limit is not None and limit < 1:
        raise ValueError('source limit must be positive')
    sources = [r for r in inventory['generation_plan'] if not priority_only or r['priority']]
    if limit is not None:
        sources = sources[:limit]
    result = {
        'schema_version': SCHEMA, 'actor_id': actor_id,
        'query': inventory['query'], 'priority_only': priority_only, 'limit': limit,
        'model': model, 'effort': effort,
        'prompt_sha256': hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
        'sources': sources,
        'disclosure': 'Query matches schedule work; they do not prove semantic topic coverage.',
    }
    result['manifest_sha256'] = sha256_json(result)
    return result


def validate_manifest(manifest):
    if (manifest.get('schema_version') != SCHEMA
            or manifest.get('manifest_sha256') != sha256_json(
                {k: v for k, v in manifest.items() if k != 'manifest_sha256'})):
        raise ValueError('heading batch manifest changed')
    if manifest['prompt_sha256'] != hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest():
        raise ValueError('heading prompt changed; saved batch cannot be resumed')
    ids = [r['source_id'] for r in manifest['sources']]
    paths = [r['path'] for r in manifest['sources']]
    if len(set(ids)) != len(ids) or len(set(paths)) != len(paths):
        raise ValueError('heading batch contains duplicate sources or physical paths')


def apply_batch(store, manifest, *, output_dir, client, manager, execute=None):
    """Run one frozen source at a time; a failure never becomes completion."""
    from backend.pipeline.detailed_knowledge_extraction_runner import run_one
    from backend.pipeline.source_editorial_transition import (
        publish_transition, verify_current_source_binding,
    )
    from backend.pipeline.extraction_sections import leading_untitled_body_end
    from backend.pipeline.source_projection import project_script

    validate_manifest(manifest)
    if (client.model, client.reasoning_effort) != (manifest['model'], manifest['effort']):
        raise ValueError('heading client differs from frozen model settings')
    actor = manifest['actor_id']
    if not manager.can_persist_generated_subtitles(actor):
        raise PermissionError('heading batch requires an authorized sermon editor')
    execute = execute or run_one
    output_dir = Path(output_dir)
    write_immutable(output_dir / 'manifest.json', manifest)
    results = []
    for row in manifest['sources']:
        sid, source = row['source_id'], row['source']
        path = Path(row['path'])
        state = store.get_record_state('source_documents', sid)
        expected = row['source_pin']
        if (not state or state['retired']
                or state['revision'] != expected['revision']
                or state['content_sha256'] != expected['content_sha256']
                or record_content_sha(state['payload']) != expected['content_sha256']
                or state['payload'] != source):
            raise ValueError('registered source changed after heading inventory: ' + sid)
        if manager.authoritative_transcript_path(source['transcript_id']).resolve() != path.resolve():
            raise ValueError('heading source is no longer the authoritative transcript: ' + sid)
        verify_current_source_binding({'path': str(path), 'file_sha256': row['current_file_sha256']})

        def save_with_proof(actor_id, transcript_id, *, expected_source_sha256, insertions):
            # Save proof before CAS: a lost save reply can be reconciled against
            # the exact planned bytes, without altering source or review history.
            before_raw = path.read_bytes()
            if hashlib.sha256(before_raw).hexdigest() != expected_source_sha256:
                raise ValueError('source changed before heading application')
            before = json.loads(before_raw)
            stage = path.parent.name
            rows = transcript_rows(before, stage=stage)
            updated = apply_insertions(rows, insertions,
                source_sha256=expected_source_sha256, user_id=actor_id)
            after = payload_with_rows(before, updated, stage=stage)
            after_raw = json.dumps(after, ensure_ascii=False, indent=4).encode('UTF-8')
            publish_transition(source={**source, 'source_file_sha256': expected_source_sha256},
                before_raw=before_raw, after_raw=after_raw,
                authorization='User authorized source headings for #436; actor=' + actor_id)
            return manager.persist_generated_subtitles(actor_id, transcript_id,
                expected_source_sha256=expected_source_sha256, insertions=insertions)

        source_output = output_dir / 'sources' / hashlib.sha256(sid.encode()).hexdigest()[:24]
        try:
            status, saved = execute(path, output_dir=source_output, client=client,
                prompt='', reasoning_effort=client.reasoning_effort, force=False,
                write_back_subtitles=True, subtitle_actor_id=actor,
                subtitle_writer=save_with_proof,
                subtitle_authorizer=manager.can_persist_generated_subtitles,
                subtitles_only=True)
            if status not in {'created', 'skipped'} or Path(saved).resolve() != path.resolve():
                raise ValueError('unknown subtitle application result')
            current = verify_current_source_binding(
                {'path': str(path), 'file_sha256': row['current_file_sha256']})
            saved_payload = json.loads(path.read_bytes())
            projection = project_script(transcript_rows(saved_payload, stage=path.parent.name))
            if (not projection.body_rows or leading_untitled_body_end(
                    projection.headings, len(projection.body_rows)) is not None):
                raise ValueError('subtitle application did not produce a titled source')
            result = {'source_id': sid, 'status': 'completed', 'application': status,
                      'current_source': current}
        except Exception as exc:
            result = {'source_id': sid, 'status': 'incomplete',
                      'error': type(exc).__name__ + ': ' + str(exc)}
            results.append(result)
            _save_results(output_dir, manifest, results)
            raise
        results.append(result)
        _save_results(output_dir, manifest, results)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return _save_results(output_dir, manifest, results)


def _save_results(output_dir, manifest, results):
    result = {'schema_version': SCHEMA, 'manifest_sha256': manifest['manifest_sha256'],
              'planned': len(manifest['sources']), 'results': results,
              'completed': sum(r['status'] == 'completed' for r in results)}
    # Progress is a cache. The immutable source/application proofs authorize
    # replay, never this replaceable summary.
    path = output_dir / 'results.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--query', default='人子')
    parser.add_argument('--priority-only', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--actor-id')
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--effort', default='high')
    parser.add_argument('--codex-executable', help='Explicit local subscription CLI; no model fallback')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv()
    store = PostgresKnowledgeStore()
    saved = args.output_dir / 'manifest.json'
    if saved.exists():
        manifest = read_artifact(saved)
        validate_manifest(manifest)
        if (manifest['query'], manifest['priority_only'], manifest['limit'],
                manifest['actor_id'], manifest['model'], manifest['effort']) != (
                args.query, args.priority_only, args.limit,
                args.actor_id, args.model, args.effort):
            raise ValueError('resume options differ from frozen batch')
    else:
        inventory = build_source_heading_inventory(store, query=args.query,
            include_other_sources=not args.priority_only)
        write_immutable(args.output_dir / 'inventory.json', inventory)
        manifest = batch_manifest(inventory, actor_id=args.actor_id,
            model=args.model, effort=args.effort,
            priority_only=args.priority_only, limit=args.limit)
        write_immutable(saved, manifest)
    print(json.dumps({'planned': len(manifest['sources']), 'apply': args.apply,
                      'manifest_sha256': manifest['manifest_sha256']}, ensure_ascii=False), flush=True)
    if args.apply:
        if not args.actor_id:
            raise ValueError('--apply requires --actor-id')
        from backend.api.sc_api.sermon_manager import sermonManager
        client = CodexSubscriptionClient(model=args.model, reasoning_effort=args.effort,
                                         executable=args.codex_executable)
        apply_batch(store, manifest, output_dir=args.output_dir,
                    client=client, manager=sermonManager)


if __name__ == '__main__':
    main()
