"""Tests for mcp_server.py — search_notes tool."""
import httpx
import pytest
from unittest.mock import MagicMock, patch

import mcp_server


# ── search_notes ──────────────────────────────────────────────────────────────

def test_search_notes_returns_results():
    results = [{"rank": 1, "source": "Claude/note.md", "title": "Note", "entry_date": "2025-01-01", "entities": [], "snippet": "some text"}]
    payload = {"filter": {"start": "2025-01-01", "end": "2025-01-31"}, "results": results}
    fake_resp = MagicMock()
    fake_resp.json.return_value = payload
    with patch("mcp_server.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.get.return_value = fake_resp
        result = mcp_server.search_notes("what did I work on?", top_k=3)
    assert result == results
    call_kwargs = mock_cls.return_value.__enter__.return_value.get.call_args
    assert call_kwargs.kwargs["params"] == {"q": "what did I work on?", "k": 3}


def test_search_notes_default_top_k():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"filter": {}, "results": []}
    with patch("mcp_server.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.get.return_value = fake_resp
        mcp_server.search_notes("test query")
    call_kwargs = mock_cls.return_value.__enter__.return_value.get.call_args
    assert call_kwargs.kwargs["params"]["k"] == 5


def test_search_notes_strips_trailing_slash_from_rag_url():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"filter": {}, "results": []}
    with (
        patch("mcp_server.RAG_URL", "http://localhost:8000/"),
        patch("mcp_server.httpx.Client") as mock_cls,
    ):
        mock_cls.return_value.__enter__.return_value.get.return_value = fake_resp
        mcp_server.search_notes("test")
    url = mock_cls.return_value.__enter__.return_value.get.call_args.args[0]
    assert url == "http://localhost:8000/retrieve/dated"


def test_search_notes_raises_on_http_error():
    with patch("mcp_server.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.get.return_value.raise_for_status.side_effect = (
            httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        )
        with pytest.raises(httpx.HTTPStatusError):
            mcp_server.search_notes("test")


# ── server composition ────────────────────────────────────────────────────────

def test_vault_tools_are_mounted():
    """obsidian-mcp-guard tools are present on the composed MCP server."""
    import asyncio
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert {"read_note", "list_notes", "create_note", "update_note", "delete_note", "lint_note"}.issubset(tool_names)


def test_search_notes_tool_is_registered():
    """search_notes is registered on the MCP server."""
    import asyncio
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert any(t.name == "search_notes" for t in tools)
