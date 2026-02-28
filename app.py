"""Deterministic router for cluster ops questions."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


PROTOCOL_VERSION = "2025-06-18"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@dataclass
class ToolRequest:
    server: str
    tool: str
    arguments: dict[str, Any]


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


class AskResponse(BaseModel):
    question: str
    route: str
    server: str
    tool: str
    tool_args: dict[str, Any]
    raw_result: str
    summary: str


class MCPHTTPClient:
    """Minimal streamable HTTP client for MCP tool calls."""

    def __init__(self, name: str, base_url: str, timeout: float = 30.0):
        self.name = name
        self.endpoint = base_url.rstrip("/") + "/mcp"
        self.timeout = timeout

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            session_id = await self._initialize(client)
            await self._notify_initialized(client, session_id)
            payload = {
                "jsonrpc": "2.0",
                "id": "tool-call",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
            response = await client.post(
                self.endpoint,
                headers={**MCP_HEADERS, "mcp-session-id": session_id},
                json=payload,
            )
            response.raise_for_status()
            data = self._extract_event_json(response.text)
            result = data.get("result", {})
            if result.get("isError"):
                raise RuntimeError(result.get("structuredContent", {}).get("result") or "MCP tool call failed")
            structured = result.get("structuredContent")
            if isinstance(structured, dict) and "result" in structured:
                return str(structured["result"])
            content = result.get("content", [])
            text_chunks = [item.get("text", "") for item in content if item.get("type") == "text"]
            if text_chunks:
                return "\n".join(chunk for chunk in text_chunks if chunk)
            return json.dumps(result)

    async def _initialize(self, client: httpx.AsyncClient) -> str:
        payload = {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "cluster-query-router",
                    "version": "0.1.0",
                },
            },
        }
        response = await client.post(self.endpoint, headers=MCP_HEADERS, json=payload)
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError(f"{self.name} did not return an MCP session id")
        self._extract_event_json(response.text)
        return session_id

    async def _notify_initialized(self, client: httpx.AsyncClient, session_id: str) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        response = await client.post(
            self.endpoint,
            headers={**MCP_HEADERS, "mcp-session-id": session_id},
            json=payload,
        )
        if response.status_code not in (200, 202):
            response.raise_for_status()

    @staticmethod
    def _extract_event_json(event_body: str) -> dict[str, Any]:
        for line in event_body.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise RuntimeError("No MCP event payload found in response")


class OllamaSummarizer:
    def __init__(self, base_url: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def summarize(self, question: str, raw_result: str) -> str:
        prompt = (
            "You are summarizing real Kubernetes ops tool output.\n"
            "Use only the provided tool result.\n"
            "Do not invent facts.\n"
            "If the tool result is empty, say that clearly.\n"
            "Return exactly 3 short bullet points.\n\n"
            f"User question:\n{question}\n\n"
            f"Tool result:\n{raw_result}\n"
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
        summary = data.get("response", "").strip()
        return summary or raw_result


class QuestionRouter:
    def route(self, question: str) -> ToolRequest:
        normalized = self._normalize(question)
        namespace = self._extract_namespace(normalized)
        hours = self._extract_hours(normalized)

        if self._mentions(normalized, "prometheus", "metrics") and self._mentions(normalized, "health", "healthy", "up"):
            return ToolRequest(server="prometheus", tool="health_check", arguments={})

        if "namespaces" in normalized:
            return ToolRequest(server="loki", tool="list_namespaces", arguments={})

        if self._mentions(normalized, "restart", "restarts", "crash", "crashing", "crashloop", "oomkilled"):
            return ToolRequest(
                server="loki",
                tool="find_pod_restarts",
                arguments=self._with_common(namespace, hours),
            )

        pod_name = self._extract_pod_name(normalized)
        if pod_name:
            args = self._with_common(namespace, hours)
            args["pod_name"] = pod_name
            return ToolRequest(server="loki", tool="get_pod_logs", arguments=args)

        search_query = self._extract_search_query(question)
        if search_query:
            args = self._with_common(namespace, hours)
            args["query"] = search_query
            return ToolRequest(server="loki", tool="search_logs", arguments=args)

        if self._mentions(normalized, "error", "errors", "exception", "exceptions", "panic", "fatal"):
            return ToolRequest(
                server="loki",
                tool="get_error_summary",
                arguments=self._with_common(namespace, hours),
            )

        raise ValueError("No deterministic route matched this question")

    @staticmethod
    def _normalize(text: str) -> str:
        cleaned = re.sub(r'[^a-z0-9\-\s"]+', " ", text.lower())
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _mentions(text: str, *terms: str) -> bool:
        return any(term in text for term in terms)

    @staticmethod
    def _with_common(namespace: str, hours: int) -> dict[str, Any]:
        return {"namespace": namespace, "hours": hours}

    @staticmethod
    def _extract_namespace(text: str) -> str:
        patterns = [
            r"(?:in|from) (?:the )?([a-z0-9-]+) namespace",
            r"namespace ([a-z0-9-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_hours(text: str) -> int:
        match = re.search(r"(?:last|past) (\d+) hours?", text)
        if match:
            return max(1, int(match.group(1)))
        if "right now" in text or "currently" in text:
            return 1
        return 1

    @staticmethod
    def _extract_pod_name(text: str) -> str:
        patterns = [
            r"logs from (?:the )?([a-z0-9-*]+)",
            r"logs for (?:the )?([a-z0-9-*]+)",
            r"pod ([a-z0-9-*]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_search_query(text: str) -> str:
        quoted = re.search(r'"([^"]+)"', text)
        if quoted:
            return quoted.group(1)

        lowered = text.lower()
        patterns = [
            r"search for ([a-z0-9 _.-]+)",
            r"find logs containing ([a-z0-9 _.-]+)",
            r"containing ([a-z0-9 _.-]+)",
            r"mentions ([a-z0-9 _.-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return match.group(1).strip()
        if "timeout" in lowered:
            return "timeout"
        return ""


app = FastAPI(title="cluster-query-router")
router = QuestionRouter()
loki_client = MCPHTTPClient("loki", os.getenv("LOKI_MCP_URL", "http://loki-mcp.monitoring.svc.cluster.local:8000"))
prometheus_client = MCPHTTPClient(
    "prometheus",
    os.getenv("PROMETHEUS_MCP_URL", "http://prometheus-mcp.monitoring.svc.cluster.local:8080"),
)
summarizer = OllamaSummarizer(
    os.getenv("OLLAMA_URL", "http://ollama-external.ai.svc.cluster.local:11434"),
    os.getenv("OLLAMA_MODEL", "phi4-mini:latest"),
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    try:
        tool_request = router.route(request.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = loki_client if tool_request.server == "loki" else prometheus_client
    try:
        raw_result = await client.call_tool(tool_request.tool, tool_request.arguments)
    except Exception as exc:  # pragma: no cover - surfaced to caller
        raise HTTPException(status_code=502, detail=f"Tool call failed: {exc}") from exc

    try:
        summary = await summarizer.summarize(request.question, raw_result)
    except Exception:
        summary = raw_result

    return AskResponse(
        question=request.question,
        route=f"{tool_request.server}.{tool_request.tool}",
        server=tool_request.server,
        tool=tool_request.tool,
        tool_args=tool_request.arguments,
        raw_result=raw_result,
        summary=summary,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        log_level="info",
    )
