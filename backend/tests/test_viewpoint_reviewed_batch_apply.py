from types import SimpleNamespace

import pytest

from backend.pipeline.viewpoint_reviewed_batch_apply import prohibit_model_calls, require_same_semantic_input


def test_recovery_never_calls_a_model():
    adapter = prohibit_model_calls(SimpleNamespace(generate=lambda _: {"unexpected": True}))
    with pytest.raises(ValueError, match="model calls prohibited"):
        adapter.generate({})


def test_same_proposition_context_may_be_reused():
    context = [{"viewpoint_id": "CV1", "core_proposition": "old words"}]
    require_same_semantic_input({"registry_context": context}, list(context))


def test_changed_proposition_context_cannot_be_reused():
    with pytest.raises(ValueError, match="context changed"):
        require_same_semantic_input({"registry_context": [{"viewpoint_id": "CV1", "core_proposition": "old words"}]},
                                   [{"viewpoint_id": "CV1", "core_proposition": "new words"}])
