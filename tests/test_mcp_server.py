import json

from conftest import D
from glassfolio.assistant.mcp_server import call, read_tools, tool_schema
from glassfolio.assistant.tools import Context


def test_only_read_tools_are_exposed():
    names = set(read_tools())
    assert "answer_question" not in names and {"get_exposure", "query_readonly"} <= names
    assert tool_schema(read_tools()["query_readonly"])["required"] == ["sql"]


def test_calls_return_json_and_errors_as_data(golden):
    lake, _ = golden
    ctx = Context(lake, D)
    out = json.loads(call(ctx, "get_exposure", {"ticker": "NVDA", "as_of": "2026-09-18"}))
    assert out["rows"][0]["total"] == 4500
    assert "error" in json.loads(call(ctx, "answer_question", {"item_id": "x", "classification": "deposit"}))
    assert "error" in json.loads(call(ctx, "query_readonly", {"sql": "SELECT getenv('HOME')"}))


def test_cli_refuses_without_local_confirmation(tmp_path, monkeypatch):
    import pytest
    from glassfolio.cli import main
    with pytest.raises(SystemExit, match="--client-is-local"):
        main(["mcp"])
