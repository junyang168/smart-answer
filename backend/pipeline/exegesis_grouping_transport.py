"""Subscription-only structured transport; preserve CLI bytes before parsing."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def write_new(path: Path, value: dict) -> dict:
    value = {**value}
    value['artifact_sha256'] = sha256_json(value)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return value


def call(*, provider, model, effort, prompt, payload, schema, directory, max_bytes, timeout=900):
    """Exactly one CLI generation, no API fallback or semantic repair."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    if provider not in {'gpt', 'claude'} or not model:
        raise ValueError('explicit subscription provider/model required')
    client = (CodexSubscriptionClient(model=model, reasoning_effort=effort) if provider == 'gpt'
              else ClaudeSubscriptionClient(model=model, reasoning_effort=effort))
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    raw = directory / 'last-message.raw.txt'
    if provider == 'gpt':
        wire = ('Perform structured extraction without tools or file changes. Return only JSON.\n'
                + prompt + '\n===== USER INPUT =====\n' + body)
        command = [client.executable, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                   '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never', '--model', model,
                   '--config', f'model_reasoning_effort="{effort}"', '--output-schema',
                   str(directory / 'schema.json'), '--output-last-message', str(raw), '-']
    else:
        wire = body
        command = [client.executable, '--print', '--safe-mode', '--disable-slash-commands',
                   '--no-session-persistence', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                   '--tools', '', '--permission-mode', 'dontAsk', '--model', model, '--effort', effort,
                   '--system-prompt', prompt, '--output-format', 'json', '--json-schema', schema_text]
    # Include every serialized argument plus stdin and externally supplied GPT schema.
    size = len(wire.encode()) + sum(len(arg.encode()) for arg in command)
    if provider == 'gpt':
        size += len(schema_text.encode())
    request = write_new(directory / 'request.json', dict(provider=provider, model=model, effort=effort,
        prompt=prompt, payload=payload, schema=schema, request_bytes=size, max_request_bytes=max_bytes,
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
