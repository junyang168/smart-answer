import json

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from backend.sunday_assistant import subscription


def test_strict_schema_and_subscription_tool_arguments(monkeypatch):
    seen = []
    class Client:
        def generate_json(self, system, payload, schema):
            seen.append((system, json.loads(payload), schema))
            if 'tools' in json.loads(payload):
                return {'tool': 'lookup', 'arguments_json': '{"day":"2026-11-22"}'}
            assert schema['additionalProperties'] is False
            assert schema['required'] == ['ok']
            return {'ok': True}
    monkeypatch.setattr(subscription, 'client', Client)
    value = subscription.structured_content(
        [{'role': 'system', 'content': 'check'}, {'role': 'user', 'content': 'data'}],
        {'json_schema': {'schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean', 'default': False}}}}},
    )
    assert json.loads(value) == {'ok': True}
    @tool
    def lookup(day: str):
        """Read a Sunday."""
        raise AssertionError('Inference must not execute tools')
    model = subscription.SubscriptionToolModel().bind_tools([lookup])
    reply = model.invoke([HumanMessage('11/22')])
    assert reply.tool_calls[0]['args'] == {'day': '2026-11-22'}
    assert reply.response_metadata['model_provider'] == 'codex_subscription'
    assert seen[-1][1]['tools'][0]['function']['name'] == 'lookup'
