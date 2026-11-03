import json

import pytest
from google.genai import types

from core import cloudflare


@pytest.fixture(autouse=True)
def settings_cache():
    from core.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def responses():
    import responses as library

    with library.RequestsMock() as mock:
        yield mock


def test_schema_request_and_usage(monkeypatch, responses):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    url = "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/google/gemma-4-26b-a4b-it"
    responses.post(
        url,
        json={
            "success": True,
            "result": {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"neurons": 1},
            },
        },
    )
    result = cloudflare.generate(
        [types.Content(role="user", parts=[types.Part(text="hello")])],
        types.GenerateContentConfig(
            system_instruction="JSON only", response_json_schema={"type": "object"}
        ),
    )
    assert json.loads(result.text) == {"ok": True}
    body = json.loads(responses.calls[0].request.body)
    assert body["messages"] == [
        {"role": "system", "content": "JSON only"},
        {"role": "user", "content": "hello"},
    ]
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert body["max_completion_tokens"] == 2048
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0
    assert result.usage_metadata["neurons"] == 1


@pytest.mark.parametrize(
    "status,payload",
    [
        (429, {"errors": [{"message": "quota exhausted"}]}),
        (200, {"success": False, "errors": [{"message": "busy"}]}),
        (
            200,
            {
                "success": True,
                "result": {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
            },
        ),
    ],
)
def test_failure_never_returns_success(monkeypatch, responses, status, payload):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    responses.post(
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/google/gemma-4-26b-a4b-it",
        json=payload,
        status=status,
    )
    with pytest.raises((ValueError, RuntimeError)):
        cloudflare.generate([], types.GenerateContentConfig())


def test_provider_selection_does_not_call_gemini(monkeypatch, mock_gemini):
    from core import ai_engine
    from types import SimpleNamespace

    monkeypatch.setenv("AI_PROVIDER", "cloudflare")
    expected = SimpleNamespace(text="{}")
    monkeypatch.setattr(cloudflare, "generate", lambda contents, config: expected)
    assert ai_engine.generate_with_retry("unused", [], types.GenerateContentConfig()) is expected
    mock_gemini.models.generate_content.assert_not_called()


def test_unknown_provider_is_rejected(monkeypatch, mock_gemini):
    from core import ai_engine

    monkeypatch.setenv("AI_PROVIDER", "cloudflrae")
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        ai_engine.generate_with_retry("unused", [], types.GenerateContentConfig())
    mock_gemini.models.generate_content.assert_not_called()


def test_parser_does_not_spend_output_budget_on_reasoning(monkeypatch, responses):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    responses.post(
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/google/gemma-4-26b-a4b-it",
        json={
            "success": True,
            "result": {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
        },
    )
    cloudflare.generate(
        [types.Content(role="user", parts=[types.Part(text="Split this")])],
        types.GenerateContentConfig(
            response_json_schema={"type": "array", "items": {"type": "object"}}
        ),
    )
    body = json.loads(responses.calls[0].request.body)
    assert body["messages"][-1]["content"] == "Split this"


def test_quota_diagnostics_preserve_reset_without_exposing_token(monkeypatch, responses):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    responses.post(
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/google/gemma-4-26b-a4b-it",
        status=429,
        headers={"Retry-After": "3600"},
        json={"errors": [{"code": 3041, "message": "daily neuron limit for test-token"}]},
    )
    with pytest.raises(RuntimeError) as error:
        cloudflare.generate([], types.GenerateContentConfig())
    assert "daily neuron limit" in str(error.value)
    assert "3600" in str(error.value)
    assert "test-token" not in str(error.value)
    assert len(responses.calls) == 1


def test_live_suite_uses_selected_production_provider_and_context(monkeypatch, responses):
    import runpy
    from types import SimpleNamespace
    from core import ai_engine

    monkeypatch.setenv("AI_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("GEMINI_API_KEY", "unused-test-sentinel")
    calls = []

    def generate(model, contents, config):
        calls.append((contents, config))
        return SimpleNamespace(text="{}")

    monkeypatch.setattr(ai_engine, "generate_with_retry", generate)
    suite = runpy.run_path("tests/test_integration.py")
    suite["extract"]("tasks", "Learn a skill", "medium priority")
    assert calls[-1][0][0].parts[0].text == "Learn a skill\n[Context: medium priority]"
    suite["parse"]("One @ Two")
    assert calls[-1][1].response_json_schema["type"] == "array"
    assert len(responses.calls) == 0


def test_classifier_eval_accepts_cloudflare_without_gemini(monkeypatch):
    import runpy
    from types import SimpleNamespace
    from core import ai_engine

    monkeypatch.setenv("AI_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        ai_engine,
        "generate_with_retry",
        lambda *args, **kwargs: SimpleNamespace(text='{"category":"tasks"}'),
    )
    script = runpy.run_path("scripts/eval_classifier.py")
    assert (
        script["run_set"]("prompt", "fixture", [{"text": "Do something", "expected": "tasks"}], 1)
        == 1
    )


def test_gemini_comparison_keeps_credentials_out_of_urls(monkeypatch, responses):
    import runpy

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    suite = runpy.run_path("tests/test_integration.py")
    responses.post(
        suite["GEMINI_URL"], json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    )
    assert suite["_gemini_call"]("JSON only", "hello") == {}
    request = responses.calls[0].request
    assert "test-secret-key" not in request.url
    assert request.headers["x-goog-api-key"] == "test-secret-key"
