#!/usr/bin/env python3
"""
MCP server (stdio) for Cursor and Claude Desktop.
Provides semantic search over the Obsidian notes vector store and direct
vault file operations via obsidian-mcp-guard.
Requires the RAG stack to be running (make up) for search_notes.
File tools (read/list/create/update/delete/lint) are provided by
obsidian-mcp-guard and operate directly on the vault filesystem via the
HOST_VAULT_PATH and WRITE_VAULT environment variables.
"""

import os
from fastmcp import FastMCP
import httpx
from obsidian_mcp_guard import create_vault_server

mcp = FastMCP("markdown-rag")
RAG_URL = os.getenv("RAG_URL", "http://localhost:8000")

# Mount vault file tools — configuration via HOST_VAULT_PATH and WRITE_VAULT
# environment variables (set in the MCP client config, not in .env).
mcp.mount(create_vault_server())


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_notes(question: str, top_k: int = 5) -> list[dict]:
    """
    Semantically search the Obsidian notes vector store.
    Returns ranked chunks with source, title, entry_date, people, and snippet.
    Use date phrases like 'last week' or '2025-01-15' to filter by time.
    Use quoted names or multi-word names to filter by people.
    The source field in each result can be passed directly to read_note.
    """
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{RAG_URL.rstrip('/')}/debug/retrieve-dated",
            params={"q": question, "k": top_k},
        )
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    mcp.run(transport="stdio")
