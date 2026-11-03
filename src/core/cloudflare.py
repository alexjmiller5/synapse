"""Workers AI JSON generation. Credentials stay server-side."""

import json
import os
from types import SimpleNamespace

import requests

from core.settings import get_settings


def generate(contents, config):
    settings = get_settings()
    token = settings.cloudflare_api_token
    account = settings.cloudflare_account_id
    if not token or not account:
        raise ValueError("Cloudflare credentials are missing")
    messages = [{"role": "system", "content": config.system_instruction or ""}]
    for content in contents:
        messages.append(
            {
                "role": "user",
                "content": "".join(part.text or "" for part in content.parts) + "\n/think",
            }
        )
    body = {
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
    }
    schema = config.response_json_schema
    if schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": schema,
        }
    else:
        body["response_format"] = {"type": "json_object"}
    if schema and schema.get("type") == "array":
        for message in messages[1:]:
            message["content"] = message["content"].removesuffix("\n/think") + "\n/no_think"
    model = os.environ.get("CLOUDFLARE_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8")
    if model == "@cf/qwen/qwen3.8-27b":
        body.pop("max_tokens")
        body["max_completion_tokens"] = 1024
        body["reasoning_effort"] = "low"
        for message in messages[1:]:
            message["content"] = message["content"].removesuffix("\n/think")
        if schema:
            body["response_format"]["json_schema"] = {"name": "synapse", "schema": schema}
    response = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=60,
    )
    if response.status_code != 200:
        try:
            details = json.dumps(response.json().get("errors", [])).replace(token, "[redacted]")[
                :500
            ]
        except ValueError:
            details = "Unparseable error response"
        raise RuntimeError(
            f"Workers AI HTTP {response.status_code}; Retry-After={response.headers.get('Retry-After')}; {details}"
        )
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError("Workers AI rejected generation")
    result = payload["result"]
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("Workers AI output is incomplete")
    text = choice["message"]["content"]
    json.loads(text)
    return SimpleNamespace(text=text, usage_metadata=result.get("usage", {}), candidates=None)
