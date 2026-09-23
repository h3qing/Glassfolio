import json

import pytest

import glassfolio.llm as llm
from glassfolio.llm import ModelError, OpenAICompatModel, extract_json, require_loopback


def test_loopback_only():
    assert require_loopback("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    assert require_loopback("http://localhost:1234/v1")
    with pytest.raises(ModelError, match="this machine"):
        require_loopback("http://8.8.8.8/v1")
    with pytest.raises(ModelError, match="http"):
        require_loopback("file:///etc/passwd")


def test_remote_model_is_refused_before_any_request():
    with pytest.raises(ModelError, match="this machine"):
        OpenAICompatModel("m", base_url="http://1.1.1.1/v1").complete_json("s", "u", {"type": "object"})
    assert llm.list_models("http://1.1.1.1/v1") == ()


def test_unreachable_local_model_is_a_clear_error():
    with pytest.raises(ModelError, match="not reachable"):
        OpenAICompatModel("m", base_url="http://127.0.0.1:9/v1").complete_json("s", "u", {"type": "object"})


@pytest.mark.parametrize("text", [
    '{"a": 1}', 'Sure! Here it is:\n```json\n{"a": 1}\n```', '<think>hmm {x}</think> {"a": 1} done',
])
def test_extract_json_from_varied_replies(text):
    assert extract_json(text) == {"a": 1}


def test_negotiates_down_when_a_server_rejects_features(monkeypatch):
    seen = []

    def fake_http(url, body=None, timeout=0):
        seen.append((body.get("response_format", {}).get("type"), "reasoning_effort" in body))
        if body.get("response_format", {}).get("type") == "json_schema":
            raise llm._Rejected(400)
        if "reasoning_effort" in body:
            raise llm._Rejected(400)
        return {"choices": [{"message": {"content": 'OK: {"a": 2}'}}]}

    monkeypatch.setattr(llm, "_http", fake_http)
    assert OpenAICompatModel("m").complete_json("s", "u", {"type": "object"}) == {"a": 2}
    assert seen == [("json_schema", True), ("json_schema", False), ("json_object", True), ("json_object", False)]


def test_falls_back_to_text_when_json_mode_output_is_unusable(monkeypatch):
    def fake_http(url, body=None, timeout=0):
        kind = body.get("response_format", {}).get("type")
        content = "not json" if kind in ("json_schema", "json_object") else json.dumps({"a": 3})
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(llm, "_http", fake_http)
    assert OpenAICompatModel("m").complete_json("s", "u", {"type": "object"}) == {"a": 3}


def test_only_literal_loopback_hosts():
    with pytest.raises(ModelError):
        require_loopback("http://localtest.me:11434/v1")  # a DNS name that happens to resolve to 127.0.0.1
    assert require_loopback("http://[::1]:11434/v1")
