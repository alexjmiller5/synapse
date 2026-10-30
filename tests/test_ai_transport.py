"""The real HTTP boundary, with synthetic responses and no provider calls."""

import json

import pytest
import requests
import responses

from core import ai_engine
from core.settings import get_settings

URL = "https://api.groq.com/openai/v1/chat/completions"
SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string"}, "project": {"type": "string"}},
    "required": ["category"],
}


@pytest.fixture(autouse=True)
def _reset_all_mocks(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "synthetic-provider-key")
    monkeypatch.setenv("AI_MODEL", "primary")
    monkeypatch.setenv("AI_FALLBACK_MODEL", "fallback")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def completion(value, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(value)}}]}


def generate(schema=SCHEMA):
    return ai_engine.generate_with_retry(
        system_instruction="Classify the input.",
        user_text="Buy milk",
        schema=schema,
    )


@responses.activate
def test_optional_fields_stay_absent_and_key_is_only_in_header():
    responses.post(URL, json=completion({"category": "tasks", "project": None}))
    assert generate() == {"category": "tasks"}
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer synthetic-provider-key"
    assert "synthetic-provider-key" not in request.url
    body = json.loads(request.body)
    assert body["model"] == "primary"
    assert body["temperature"] == 0
    assert body["max_completion_tokens"] <= 1000
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["category", "project"]
    assert schema["properties"]["project"]["anyOf"][-1] == {"type": "null"}


@responses.activate
def test_parser_array_is_wrapped_and_unwrapped_without_changing_text():
    schema = {"type": "array", "items": SCHEMA}
    responses.post(URL, json=completion({"items": [{"category": "tasks", "project": None}]}))
    assert generate(schema) == [{"category": "tasks"}]
    body = json.loads(responses.calls[0].request.body)
    assert body["response_format"]["json_schema"]["schema"]["type"] == "object"
    assert body["messages"][1]["content"] == "Buy milk"


@responses.activate
def test_missing_model_uses_only_the_configured_fallback():
    responses.post(URL, status=404)
    responses.post(URL, json=completion({"category": "tasks", "project": None}))
    assert generate() == {"category": "tasks"}
    assert [json.loads(c.request.body)["model"] for c in responses.calls] == ["primary", "fallback"]


@responses.activate
def test_bad_request_is_not_retried():
    responses.post(URL, status=400)
    with pytest.raises(requests.HTTPError):
        generate()
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_respects_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(ai_engine._generate.retry, "sleep", waits.append)
    responses.post(URL, status=429, headers={"Retry-After": "7"})
    responses.post(URL, json=completion({"category": "tasks"}))
    assert generate() == {"category": "tasks"}
    assert waits == [7]


@responses.activate
def test_daily_quota_does_not_hold_the_worker_for_hours():
    responses.post(URL, status=429, headers={"Retry-After": "3600"})
    with pytest.raises(requests.HTTPError):
        generate()
    assert len(responses.calls) == 1


@responses.activate
def test_truncated_json_is_retried_even_when_it_parses(monkeypatch):
    monkeypatch.setattr(ai_engine._generate.retry, "sleep", lambda _: None)
    responses.post(URL, json=completion({"category": "tasks"}, finish="length"))
    responses.post(URL, json=completion({"category": "tasks"}))
    assert generate() == {"category": "tasks"}
    assert len(responses.calls) == 2


@responses.activate
def test_long_service_unavailable_delay_does_not_block_the_worker(monkeypatch):
    waits = []
    monkeypatch.setattr(ai_engine._generate.retry, "sleep", waits.append)
    responses.post(URL, status=503, headers={"Retry-After": "3600"})
    with pytest.raises(requests.HTTPError):
        generate()
    assert len(responses.calls) == 1
    assert waits == []


@pytest.mark.parametrize("delay", [7, 3600])
@responses.activate
def test_retry_after_http_dates_are_respected(delay, monkeypatch):
    from email.utils import formatdate
    import time

    waits = []
    monkeypatch.setattr(ai_engine._generate.retry, "sleep", waits.append)
    responses.post(
        URL, status=429, headers={"Retry-After": formatdate(time.time() + delay, usegmt=True)}
    )
    responses.post(URL, json=completion({"category": "tasks"}))
    if delay > 60:
        with pytest.raises(requests.HTTPError):
            generate()
        assert len(responses.calls) == 1
        assert waits == []
    else:
        assert generate() == {"category": "tasks"}
        assert len(waits) == 1 and delay - 1 <= waits[0] <= delay


@pytest.mark.parametrize("fallback", ["", "primary"])
@responses.activate
def test_missing_model_without_an_alternative_is_not_replayed(fallback, monkeypatch):
    monkeypatch.setenv("AI_FALLBACK_MODEL", fallback)
    get_settings.cache_clear()
    responses.post(URL, status=404)
    with pytest.raises(requests.HTTPError):
        generate()
    assert len(responses.calls) == 1


@responses.activate
def test_bad_json_is_bounded_to_four_attempts(monkeypatch):
    monkeypatch.setattr(ai_engine._generate.retry, "sleep", lambda _: None)
    responses.post(URL, json={"choices": [{"finish_reason": "stop", "message": {"content": "{"}}]})
    with pytest.raises(ValueError, match="Malformed JSON"):
        generate()
    assert len(responses.calls) == 4


@responses.activate
def test_transient_server_error_retries_without_changing_the_model(monkeypatch):
    monkeypatch.setattr(ai_engine._generate.retry, "sleep", lambda _: None)
    responses.post(URL, status=503)
    responses.post(URL, json=completion({"category": "tasks"}))
    assert generate() == {"category": "tasks"}
    assert [json.loads(c.request.body)["model"] for c in responses.calls] == ["primary", "primary"]
