"""Life Inbox MCP server: lets AI assistants (Claude, ChatGPT, ...) use your inbox.

MCP (Model Context Protocol) is a standard way to give an AI assistant
"tools". This server offers four:

    search_inbox     find notes by meaning or keywords
    ask_inbox        get an answer with sources from your notes
    add_note         save a new note
    what_needs_attention   overdue / today / upcoming tasks

Architecture: this is NOT part of the web app. It's a small separate program
that talks to the Life Inbox HTTP API with an API token -- exactly like any
other client. So the API stays the single source of truth, and the MCP server
can run on your laptop while Life Inbox runs on a server.

    AI assistant --(MCP, stdio)--> this server --(HTTPS + token)--> Life Inbox API

Setup:
    1. In Life Inbox, create a token:  POST /api/tokens {"name": "claude"}
    2. Set LIFE_INBOX_URL (e.g. https://your-server) and LIFE_INBOX_TOKEN
    3. Run:  python -m mcp_server.server
"""

import os

import httpx
from mcp.server.mcpserver import MCPServer

API_URL = os.getenv("LIFE_INBOX_URL", "http://127.0.0.1:8000")
API_TOKEN = os.getenv("LIFE_INBOX_TOKEN", "")

# One HTTP client for all calls. Tests swap this for a test client.
http = httpx.Client(
    base_url=API_URL,
    headers={"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {},
    timeout=60,
)

mcp = MCPServer(
    "Life Inbox",
    instructions="Search, question and add to the user's personal Life Inbox notes.",
)


def call_api(method, path, **kwargs):
    response = http.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def search_inbox(query: str, limit: int = 5) -> list[dict]:
    """Search the user's saved notes, documents and links. Returns matching excerpts."""
    hits = call_api("GET", "/api/search", params={"q": query, "limit": limit})
    return [
        {"item_id": h["item_id"], "title": h["title"], "excerpt": h["excerpt"], "score": h["score"]}
        for h in hits
    ]


@mcp.tool()
def ask_inbox(question: str) -> dict:
    """Answer a question using only the user's notes, with the sources it used."""
    answer = call_api("POST", "/api/ask", json={"question": question})
    return {
        "answer": answer["answer"],
        "confidence": answer["confidence"],
        "sources": [
            {"item_id": c["item_id"], "excerpt": c.get("excerpt", "")}
            for c in answer.get("citations", [])
        ],
    }


@mcp.tool()
def add_note(text: str) -> dict:
    """Save a new text note in the user's Life Inbox."""
    item = call_api("POST", "/api/items", json={"raw_text": text})
    return {"item_id": item["id"], "saved": True}


@mcp.tool()
def what_needs_attention() -> dict:
    """The user's overdue, due-today and upcoming tasks."""
    return call_api("GET", "/api/attention")


if __name__ == "__main__":
    mcp.run()  # stdio: the AI assistant starts this program and talks over stdin/stdout
