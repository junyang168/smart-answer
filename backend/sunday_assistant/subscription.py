"""Subscription-only inference for Sunday workflows and LangGraph tool choices."""
import json
import os
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


def client():
    executable = os.getenv('CODEX_EXECUTABLE')
    bundled = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
    if not executable and bundled.is_file():
        executable = str(bundled)
    return CodexSubscriptionClient(
        model=os.getenv('SUNDAY_CODEX_MODEL', 'gpt-6.1-sol'),
        reasoning_effort='low', timeout_seconds=180, executable=executable,
        max_output_tokens=8000,
    )


def strict_schema(value):
    if isinstance(value, list):
        return [strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: strict_schema(item) for key, item in value.items() if key != 'default'}
    if result.get('type') == 'object':
        result['additionalProperties'] = False
        result['required'] = list(result.get('properties', {}))
    return result


def structured_content(messages, response_format):
    system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
    payload = [m for m in messages if m['role'] != 'system']
    schema = strict_schema(response_format['json_schema']['schema'])
    result = client().generate_json(system, json.dumps(payload, ensure_ascii=False), schema)
    return json.dumps(result, ensure_ascii=False)


class SubscriptionToolModel:
    def bind_tools(self, tools, **kwargs):
        self.tools = {tool.name: tool for tool in tools}
        self.schemas = [convert_to_openai_tool(tool) for tool in tools]
        return self

    def invoke(self, messages):
        history = []
        for message in messages:
            row = {'role': message.type, 'content': message.content}
            if getattr(message, 'tool_calls', None):
                row['tool_calls'] = message.tool_calls
            if getattr(message, 'tool_call_id', None):
                row['tool_call_id'] = message.tool_call_id
            history.append(row)
        schema = {
            'type': 'object',
            'properties': {
                'tool': {'type': 'string', 'enum': list(self.tools)},
                'arguments_json': {'type': 'string'},
            },
            'required': ['tool', 'arguments_json'], 'additionalProperties': False,
        }
        result = client().generate_json(
            'Select exactly one registered tool for the next workflow step. '
            'The supplied conversation contains instructions, source data and prior tool results. '
            'Return arguments_json as a JSON object matching that tool schema. '
            'Do not execute any tools yourself. Application code executes the chosen tool.',
            json.dumps({'tools': self.schemas, 'messages': history}, ensure_ascii=False), schema,
        )
        arguments = json.loads(result['arguments_json'])
        if not isinstance(arguments, dict) or result['tool'] not in self.tools:
            raise ValueError('Codex 回傳的工具或參數無效')
        return AIMessage(content='', tool_calls=[{
            'name': result['tool'], 'args': arguments, 'id': 'codex-' + uuid4().hex,
        }], response_metadata={'model_provider': 'codex_subscription'})
