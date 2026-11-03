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
    url = "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/qwen/qwen3-30b-a3b-fp8"
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
        {"role": "user", "content": "hello\n/think"},
    ]
    assert body["response_format"]["json_schema"] == {"type": "object"}
    assert body["max_tokens"] == 2048
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
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/qwen/qwen3-30b-a3b-fp8",
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


def test_new_qwen_uses_documented_reasoning_and_schema(monkeypatch, responses):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("CLOUDFLARE_MODEL", "@cf/qwen/qwen3.8-27b")
    responses.post(
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/qwen/qwen3.8-27b",
        json={
            "success": True,
            "result": {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        },
    )
    result = cloudflare.generate(
        [types.Content(role="user", parts=[types.Part(text="hello")])],
        types.GenerateContentConfig(response_json_schema={"type": "object"}),
    )
    body = json.loads(responses.calls[0].request.body)
    assert body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 1024
    assert body["response_format"]["json_schema"]["schema"] == {"type": "object"}
    assert body["messages"][-1]["content"] == "hello"
    assert result.candidates is None


def test_parser_does_not_spend_output_budget_on_reasoning(monkeypatch, responses):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    responses.post(
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/qwen/qwen3-30b-a3b-fp8",
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
    assert body["messages"][-1]["content"] == "Split this\n/no_think"


def test_quota_diagnostics_preserve_reset_without_exposing_token(monkeypatch, responses):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    responses.post(
        "https://api.cloudflare.com/client/v4/accounts/test-account/ai/run/@cf/qwen/qwen3-30b-a3b-fp8",
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
