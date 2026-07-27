import importlib.util
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "api" / "openai_client.py"


def load_openai_client_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content=json.dumps({"ok": True}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class OpenAIClientModelTests(unittest.TestCase):
    def _generate(self, module, *, model=None):
        completions = FakeCompletions()
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )
        module.get_openai_client = lambda: fake_client

        result = module.generate_structured_json(
            system_prompt="system",
            user_prompt="user",
            json_schema={"name": "result", "schema": {"type": "object"}},
            model=model,
        )
        self.assertEqual(result, {"ok": True})
        return completions.kwargs

    def test_default_generation_model_is_gpt_5_6_sol(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_GENERATION_MODEL", None)
            module = load_openai_client_module("test_openai_client_default")

        kwargs = self._generate(module)

        self.assertEqual(kwargs["model"], "gpt-5.6-sol")
        self.assertNotIn("temperature", kwargs)

    def test_environment_can_override_default_generation_model(self):
        with patch.dict(
            os.environ,
            {"OPENAI_GENERATION_MODEL": "gpt-5.6-sol-custom"},
        ):
            module = load_openai_client_module("test_openai_client_env")

        kwargs = self._generate(module)

        self.assertEqual(kwargs["model"], "gpt-5.6-sol-custom")

    def test_explicit_model_still_overrides_default(self):
        module = load_openai_client_module("test_openai_client_explicit")

        kwargs = self._generate(module, model="gpt-5.6-terra")

        self.assertEqual(kwargs["model"], "gpt-5.6-terra")
        self.assertNotIn("temperature", kwargs)

    def test_older_model_keeps_requested_temperature(self):
        module = load_openai_client_module("test_openai_client_older_model")

        kwargs = self._generate(module, model="gpt-4.1-mini")

        self.assertEqual(kwargs["temperature"], 0.0)


if __name__ == "__main__":
    unittest.main()
