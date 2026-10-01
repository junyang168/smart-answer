"""Subscription-only structured transport; preserve CLI bytes before parsing."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.exegesis_grouping_packet import pack, compact_json, INSTRUCTION


def write_new(path: Path, value: dict) -> dict:
    value = {**value}
    value['artifact_sha256'] = sha256_json(value)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return value


def serialize_request(*, provider, executable, model, effort, prompt, payload, schema, directory):
    """Exact generation wire, shared by runtime and read-only capacity measurement."""
    directory = Path(directory).resolve()
    wire_payload = pack(payload)
    body = compact_json(wire_payload)
    prompt = INSTRUCTION + prompt
    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    raw = directory / 'last-message.raw.txt'
    if provider == 'gpt':
        wire = ('Perform structured extraction without tools or file changes. Return only JSON.\n'
                + prompt + '\n===== USER INPUT =====\n' + body)
        command = [executable, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                   '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never', '--model', model,
                   '--config', f'model_reasoning_effort="{effort}"', '--output-schema',
                   str(directory / 'schema.json'), '--output-last-message', str(raw), '-']
    else:
        wire = body
        command = [executable, '--print', '--safe-mode', '--disable-slash-commands',
                   '--no-session-persistence', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                   '--tools', '', '--permission-mode', 'dontAsk', '--model', model, '--effort', effort,
                   '--system-prompt', prompt, '--output-format', 'json', '--json-schema', schema_text]
    # Include every serialized argument plus stdin and externally supplied GPT schema.
    argv_bytes = sum(len(arg.encode()) for arg in command)
    size = len(wire.encode()) + argv_bytes
    if provider == 'gpt':
        size += len(schema_text.encode())
    # Three payload sizes: pretty (human artifact), compact without interning
    # (dedup contribution isolated), and the interned wire actually sent.
    return dict(wire_payload=wire_payload, body=body, prompt=prompt, schema_text=schema_text,
                raw=raw, wire=wire, command=command, size=size, argv_bytes=argv_bytes,
                prompt_bytes=len(prompt.encode()), schema_bytes=len(schema_text.encode()),
                pretty_payload_bytes=len(json.dumps(payload, ensure_ascii=False, indent=2).encode()),
                compact_uninterned_payload_bytes=len(compact_json(payload).encode()),
                wire_payload_bytes=len(body.encode()),
                prompt_carried_in='wire' if provider == 'gpt' else 'argv',
                schema_carried_in='file_argument' if provider == 'gpt' else 'argv',
                interned=wire_payload is not payload)


def call(*, provider, model, effort, prompt, payload, schema, directory, max_bytes, timeout=900):
    """Exactly one CLI generation, no API fallback or semantic repair."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    if provider not in {'gpt', 'claude'} or not model:
        raise ValueError('explicit subscription provider/model required')
    client = (CodexSubscriptionClient(model=model, reasoning_effort=effort) if provider == 'gpt'
              else ClaudeSubscriptionClient(model=model, reasoning_effort=effort))
    serialized = serialize_request(provider=provider, executable=client.executable, model=model,
        effort=effort, prompt=prompt, payload=payload, schema=schema, directory=directory)
    wire_payload, body, prompt, schema_text, raw, wire, command, size = (
        serialized[k] for k in ('wire_payload', 'body', 'prompt', 'schema_text', 'raw', 'wire', 'command', 'size'))
    request = write_new(directory / 'request.json', dict(provider=provider, model=model, effort=effort,
        prompt=prompt, payload=payload, wire_payload_sha256=sha256_json(wire_payload),
        pretty_payload_bytes=serialized['pretty_payload_bytes'],
        compact_uninterned_payload_bytes=serialized['compact_uninterned_payload_bytes'],
        wire_payload_bytes=serialized['wire_payload_bytes'], interned=serialized['interned'],
        prompt_bytes=serialized['prompt_bytes'], schema_bytes=serialized['schema_bytes'], argv_bytes=serialized['argv_bytes'],
        schema=schema, request_bytes=size, max_request_bytes=max_bytes, byte_fit_is_not_token_fit=True,
        prompt_sha256=sha256_json({'prompt': prompt}), payload_sha256=sha256_json(payload), command=command))
    try:
        if size > max_bytes:
            raise ValueError(f'complete request exceeds byte limit: {size} > {max_bytes}; no truncation')
        (directory / 'schema.json').write_text(schema_text, encoding='utf-8')
        if provider == 'gpt':
            client._verify_chatgpt_login()
        else:
            client._verify_subscription_login()
        try:
            result = subprocess.run(command, input=wire, capture_output=True, text=True,
                                    env=client.environment, cwd=directory, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            write_new(directory / 'transport.raw.json', dict(stdout=decode(exc.stdout), stderr=decode(exc.stderr), timeout=True))
            raise
        write_new(directory / 'transport.raw.json', dict(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode))
        if result.returncode:
            raise ValueError(f'subscription generation exit {result.returncode}')
        if provider == 'gpt':
            response = json.loads(raw.read_text(encoding='utf-8'))
        else:
            wrapper = json.loads(result.stdout)
            if wrapper.get('is_error'):
                raise ValueError(str(wrapper.get('result')))
            response = wrapper.get('structured_output')
            if isinstance(response, str):
                response = json.loads(response)
        if not isinstance(response, dict):
            raise ValueError('structured response must be an object')
        write_new(directory / 'response.json', {'request_sha256': request['artifact_sha256'], 'response': response})
        return response
    except Exception as exc:
        write_new(directory / 'failure.json', dict(request_sha256=request['artifact_sha256'],
            error_type=type(exc).__name__, error=str(exc)))
        raise
