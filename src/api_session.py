"""
MCP client session lifecycle manager for FastAPI lifespan.
Manually drives PubMedMCPClient.from_env() so the session persists across requests.
"""

from __future__ import annotations

import logging

from src.mcp_client import PubMedMCPClient

logger = logging.getLogger(__name__)


class MCPSessionManager:
    _ctx_mgr: object | None = None
    _client: PubMedMCPClient | None = None

    async def start(self) -> None:
        ctx = PubMedMCPClient.from_env()
        self._client = await ctx.__aenter__()
        self._ctx_mgr = ctx
        logger.info("MCP client session started")

    async def stop(self) -> None:
        if self._ctx_mgr is not None:
            try:
                await self._ctx_mgr.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"MCP session cleanup error: {e}")
            self._ctx_mgr = None
            self._client = None
            logger.info("MCP client session stopped")

    def get_client(self) -> PubMedMCPClient:
        if self._client is None:
            raise RuntimeError("MCP client session not started")
        return self._client

    async def health_check(self) -> bool:
        """Verify MCP session is alive; attempt reconnect if dead."""
        if self._client is None:
            return False
        try:
            res = await self._client.search_articles(query="test", max_results=1)
            return bool(res)
        except Exception:
            logger.warning("MCP health check failed, reconnecting")
            await self.stop()
            try:
                await self.start()
                return True
            except Exception as e:
                logger.error(f"MCP reconnect failed: {e}")
                return False