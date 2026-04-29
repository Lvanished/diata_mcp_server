"""
MCP client for cyanheads/pubmed-mcp-server (stdio or Streamable HTTP).

Tool names: pubmed_search_articles, pubmed_fetch_articles, pubmed_fetch_fulltext
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


def parse_pmcid_numeric(pmcid: str) -> str:
    return re.sub(r"\D", "", pmcid or "")


def _parse_mcp_server_args(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    if "," in raw and " " not in raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _resolve_executable(cmd: str) -> str | None:
    """Return absolute path to executable, or None if not on PATH (Windows-aware)."""
    c = (cmd or "").strip()
    if not c:
        return None
    found = shutil.which(c)
    if found:
        return found
    if os.name == "nt" and not c.lower().endswith((".exe", ".cmd", ".bat")):
        return shutil.which(f"{c}.exe") or shutil.which(f"{c}.cmd")
    return None


def _stdio_missing_message(command: str, cwd: Path) -> str:
    return (
        f"MCP stdio cannot start: executable {command!r} not found on PATH (Windows: WinError 2).\n"
        f"Working directory for server: {cwd}\n\n"
        "Fix one of:\n"
        "  1) Install Bun and ensure it is on PATH: https://bun.sh/\n"
        "  2) Use HTTP instead of spawning a local server — in .env set:\n"
        "       MCP_TRANSPORT=http\n"
        "       MCP_SERVER_URL=https://pubmed.caseyjhand.com/mcp\n"
        "     (or your own host, e.g. http://localhost:3010/mcp)\n"
        "  3) If Node.js is installed, use the published package:\n"
        "       MCP_SERVER_COMMAND=npx\n"
        "       MCP_SERVER_ARGS=-y,@cyanheads/pubmed-mcp-server@latest\n"
        "       MCP_SERVER_CWD=.\n"
        "  4) Clone and build pubmed-mcp-server, then point MCP_SERVER_CWD at that folder.\n"
    )


def _merge_child_env() -> dict[str, str]:
    env = os.environ.copy()
    # Map convenience names to what pubmed-mcp-server expects.
    email = env.get("NCBI_EMAIL") or env.get("NCBI_ADMIN_EMAIL")
    if email:
        env["NCBI_ADMIN_EMAIL"] = email
    if env.get("MCP_TRANSPORT", "").lower() == "stdio":
        env.setdefault("MCP_TRANSPORT_TYPE", "stdio")
    if env.get("MCP_TRANSPORT", "").lower() == "http":
        env.setdefault("MCP_TRANSPORT_TYPE", "http")
    return env


def _tool_result_to_data(result: types.CallToolResult) -> Any:
    if result.isError:
        text = ""
        for c in result.content:
            if isinstance(c, types.TextContent):
                text += c.text
        raise RuntimeError(text or "MCP tool returned isError without message")

    if result.structuredContent is not None:
        return result.structuredContent

    chunks: list[str] = []
    for c in result.content:
        if isinstance(c, types.TextContent):
            chunks.append(c.text)
    blob = "\n".join(chunks).strip()
    if not blob:
        return {}

    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Some tools return markdown; log and return raw payload for debugging.
        logger.warning("Tool result was not JSON; returning text wrapper. First 500 chars: %s", blob[:500])
        return {"_raw_text": blob}


class PubMedMCPClient:
    """
    Thin async wrapper around MCP ClientSession with robust tool result parsing.
    Use as async context manager: `async with PubMedMCPClient.from_env() as c: ...`
    """

    def __init__(self, session: ClientSession):
        self._session = session

    @classmethod
    @asynccontextmanager
    async def from_env(cls, project_root: Path | None = None) -> AsyncIterator[PubMedMCPClient]:
        transport = (os.getenv("MCP_TRANSPORT") or "stdio").strip().lower()
        if transport == "http":
            url = os.getenv("MCP_SERVER_URL") or "http://localhost:3010/mcp"
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield cls(session)
            return

        if transport != "stdio":
            raise ValueError(f"Unsupported MCP_TRANSPORT: {transport}")

        cmd = os.getenv("MCP_SERVER_COMMAND") or "bun"
        args = _parse_mcp_server_args(os.getenv("MCP_SERVER_ARGS") or "run,start:stdio")
        root = project_root or Path.cwd()
        cwd_raw = os.getenv("MCP_SERVER_CWD") or "./pubmed-mcp-server"
        cwd = (root / cwd_raw).resolve() if not Path(cwd_raw).is_absolute() else Path(cwd_raw)

        if not cwd.is_dir():
            raise FileNotFoundError(
                f"MCP_SERVER_CWD is not a directory: {cwd}\n"
                "Clone https://github.com/cyanheads/pubmed-mcp-server or set MCP_SERVER_CWD to the project root, "
                "or use MCP_TRANSPORT=http with MCP_SERVER_URL."
            )

        resolved = _resolve_executable(cmd)
        if not resolved:
            raise RuntimeError(_stdio_missing_message(cmd, cwd))

        extra = _merge_child_env()
        if "MCP_TRANSPORT_TYPE" not in extra:
            extra["MCP_TRANSPORT_TYPE"] = "stdio"

        # Use resolved path so Windows does not rely on PATH inside the child (more reliable).
        params = StdioServerParameters(command=resolved, args=args, env=extra, cwd=str(cwd))
        logger.info("Starting MCP stdio server: %s %s (cwd=%s)", resolved, " ".join(args), cwd)

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield cls(session)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("Client session not initialized")
        res = await self._session.call_tool(name, arguments or {})
        return _tool_result_to_data(res)

    async def search_articles(self, query: str, max_results: int) -> dict[str, Any]:
        data = await self.call_tool(
            "pubmed_search_articles",
            {
                "query": query,
                "maxResults": int(max_results),
                "summaryCount": 0,
                "offset": 0,
            },
        )
        if not isinstance(data, dict):
            return {"pmids": [], "totalFound": 0, "effectiveQuery": query}
        return data

    async def fetch_articles(self, pmids: list[str]) -> dict[str, Any]:
        if not pmids:
            return {"articles": [], "totalReturned": 0}
        data = await self.call_tool(
            "pubmed_fetch_articles",
            {"pmids": pmids, "includeMesh": True, "includeGrants": False},
        )
        return data if isinstance(data, dict) else {"articles": [], "totalReturned": 0}

    async def fetch_fulltext_pmc(self, pmcids: list[str]) -> dict[str, Any]:
        """
        `pmcids` e.g. ["PMC123", "456"] — server normalizes.
        """
        if not pmcids:
            return {"articles": [], "totalReturned": 0}
        data = await self.call_tool(
            "pubmed_fetch_fulltext",
            {"pmcids": pmcids, "includeReferences": False},
        )
        return data if isinstance(data, dict) else {"articles": [], "totalReturned": 0}
