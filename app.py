"""Deterministic router for cluster ops questions."""

from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from fastapi import FastAPI, HTTPException
from fastapi import Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, make_asgi_app


PROTOCOL_VERSION = "2025-06-18"
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
TRACER_NAME = "cluster-query-router"
_TRACING_CONFIGURED = False
HTTP_REQUESTS = Counter(
    "cluster_query_router_http_requests_total",
    "Total HTTP requests handled by cluster-query-router.",
    ["method", "handler", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "cluster_query_router_http_request_duration_seconds",
    "HTTP request latency for cluster-query-router.",
    ["method", "handler"],
)

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cluster Query Router</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%23020617'/%3E%3Crect x='6' y='6' width='52' height='52' rx='13' fill='%230f172a'/%3E%3Cpath d='M18 24h12' stroke='%2322d3ee' stroke-width='4' stroke-linecap='round'/%3E%3Cpath d='M18 40h12' stroke='%2360a5fa' stroke-width='4' stroke-linecap='round'/%3E%3Cpath d='M34 20l12 8-12 8' fill='none' stroke='%23a5f3fc' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/%3E%3Cpath d='M34 36l12 8-12 8' transform='translate(0 -8)' fill='none' stroke='%23bfdbfe' stroke-width='4' stroke-linecap='round' stroke-linejoin='round' opacity='0.85'/%3E%3C/svg%3E" type="image/svg+xml">
  <style>
    :root {
      color-scheme: dark;
      --bg: #09111f;
      --panel: rgba(15, 23, 42, 0.9);
      --panel-strong: rgba(30, 41, 59, 0.92);
      --ink: #e5e7eb;
      --muted: #94a3b8;
      --border: rgba(148, 163, 184, 0.18);
      --accent: #22d3ee;
      --accent-ink: #042f2e;
      --accent-soft: rgba(34, 211, 238, 0.14);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      background:
        radial-gradient(circle at top right, rgba(34, 211, 238, 0.16), transparent 26%),
        radial-gradient(circle at bottom left, rgba(59, 130, 246, 0.14), transparent 32%),
        linear-gradient(180deg, #020617 0%, var(--bg) 100%);
      color: var(--ink);
    }

    main {
      max-width: 980px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }

    h1 {
      margin: 0 0 8px;
      font-size: clamp(2.2rem, 5vw, 4rem);
      line-height: 0.95;
      letter-spacing: -0.03em;
    }

    .intro {
      margin: 0 0 24px;
      color: var(--muted);
      font-size: 1.05rem;
    }

    .layout {
      display: grid;
      gap: 20px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 18px 48px rgba(2, 8, 23, 0.34);
    }

    label {
      display: block;
      font-size: 0.9rem;
      font-weight: 600;
      margin-bottom: 8px;
    }

    textarea {
      width: 100%;
      min-height: 108px;
      resize: vertical;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      font: inherit;
      color: inherit;
      background: rgba(15, 23, 42, 0.88);
    }

    textarea:focus {
      outline: 2px solid rgba(34, 211, 238, 0.22);
      border-color: var(--accent);
    }

    .controls {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 14px;
      flex-wrap: wrap;
    }

    .tabs {
      display: flex;
      gap: 10px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }

    .tab-button {
      border: 1px solid var(--border);
      border-radius: 999px;
      background: rgba(15, 23, 42, 0.82);
      color: var(--ink);
      padding: 10px 16px;
    }

    .tab-button.active {
      background: var(--accent);
      color: var(--accent-ink);
      border-color: var(--accent);
    }

    .tab-panel {
      display: none;
    }

    .tab-panel.active {
      display: block;
    }

    .prompt-grid {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }

    .prompt-card {
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--panel-strong);
      padding: 12px 14px;
      text-align: left;
      color: var(--ink);
      font-weight: 600;
      line-height: 1.35;
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
    }

    .prompt-card:hover {
      transform: translateY(-1px);
      border-color: var(--accent);
      background: rgba(30, 41, 59, 0.98);
    }

    .prompt-card small {
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-weight: 500;
      font-size: 0.82rem;
    }

    .prompt-groups {
      display: grid;
      gap: 18px;
    }

    .prompt-group h3 {
      margin: 0 0 10px;
      font-size: 1rem;
    }

    button {
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: var(--accent-ink);
      font: inherit;
      font-weight: 700;
      padding: 12px 18px;
      cursor: pointer;
    }

    button:disabled {
      opacity: 0.6;
      cursor: wait;
    }

    .status {
      color: var(--muted);
      font-size: 0.95rem;
    }

    .chips {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }

    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.86);
      padding: 7px 12px;
      font-family: ui-monospace, "SFMono-Regular", "SF Mono", Menlo, monospace;
      font-size: 0.85rem;
    }

    .section-title {
      margin: 0 0 8px;
      font-size: 1rem;
      font-weight: 700;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, "SFMono-Regular", "SF Mono", Menlo, monospace;
      font-size: 0.92rem;
      line-height: 1.45;
      color: #dbeafe;
    }

    .empty {
      color: var(--muted);
      font-style: italic;
    }

    @media (min-width: 860px) {
      .layout {
        grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
      }

      .panel.wide {
        grid-column: 1 / -1;
      }
    }
  </style>
</head>
<body>
  <main>
    <h1>Cluster Query Router</h1>
    <p class="intro">Ask a plain-English cluster question. The app routes it to a real MCP tool, then summarizes the result.</p>

    <section class="panel wide">
      <div class="tabs">
        <button id="askTabButton" class="tab-button active" type="button" data-tab="askTab">Ask</button>
        <button id="libraryTabButton" class="tab-button" type="button" data-tab="libraryTab">Prompt Library</button>
      </div>

      <div id="askTab" class="tab-panel active">
        <label for="question">Question</label>
        <textarea id="question" spellcheck="false">What errors are happening in my cluster right now?</textarea>
        <div class="controls">
          <button id="submit" type="button">Ask</button>
          <span class="status" id="status">Ready.</span>
        </div>
      </div>

      <div id="libraryTab" class="tab-panel">
        <div class="prompt-groups">
          <section class="prompt-group">
            <h3>Cluster Health</h3>
            <div class="prompt-grid">
              <button class="prompt-card" type="button" data-prompt="What errors are happening in my cluster right now?">
                What errors are happening right now?
                <small>Broad cluster-wide Loki error scan.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="What is the health status of Prometheus right now?">
                Is Prometheus healthy?
                <small>Quick Prometheus health check.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="What namespaces have logs in Loki?">
                What namespaces have logs?
                <small>List namespaces seen by Loki.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="What errors are happening in the ai namespace right now?">
                What errors are happening in AI?
                <small>Scope the error scan to the AI namespace.</small>
              </button>
            </div>
          </section>

          <section class="prompt-group">
            <h3>Restarts And Crashes</h3>
            <div class="prompt-grid">
              <button class="prompt-card" type="button" data-prompt="Which pods are restarting in the ai namespace in the last 2 hours?">
                Which pods are restarting in AI?
                <small>Find restart and crash patterns.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="Which pods are restarting in the monitoring namespace in the last 4 hours?">
                Which pods are restarting in monitoring?
                <small>Focus on monitoring stack churn.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="Which pods are crashing in my cluster in the last 6 hours?">
                Which pods are crashing cluster-wide?
                <small>Wider time window for instability.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="Which pods are restarting in the services namespace in the last 2 hours?">
                Which pods are restarting in services?
                <small>Check your user-facing services.</small>
              </button>
            </div>
          </section>

          <section class="prompt-group">
            <h3>Logs And Search</h3>
            <div class="prompt-grid">
              <button class="prompt-card" type="button" data-prompt="Show me logs from ollama in the ai namespace">
                Show logs from Ollama
                <small>Pull recent pod logs for a specific workload.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="Show me logs from cluster-query-router in the ai namespace">
                Show logs from cluster-query-router
                <small>Check the router service directly.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="Show me logs from loki-mcp in the monitoring namespace">
                Show logs from loki-mcp
                <small>Inspect the Loki MCP server.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="Show me logs from ollama-mcp-bridge in the ai namespace">
                Show logs from ollama-mcp-bridge
                <small>Inspect the bridge connection layer.</small>
              </button>
            </div>
          </section>

          <section class="prompt-group">
            <h3>Search Patterns</h3>
            <div class="prompt-grid">
              <button class="prompt-card" type="button" data-prompt='Search for "timeout" in the ai namespace'>
                Search for timeout errors
                <small>Regex log search scoped to AI.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt='Search for "connection refused" in the monitoring namespace'>
                Search for connection refused
                <small>Look for service connectivity failures.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt='Search for "error" in the services namespace'>
                Search for error in services
                <small>Broad error keyword search for user services.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt='Search for "authentication" in the ai namespace'>
                Search for authentication issues
                <small>Look for auth failures in AI components.</small>
              </button>
            </div>
          </section>

          <section class="prompt-group">
            <h3>Common Follow-Ups</h3>
            <div class="prompt-grid">
              <button class="prompt-card" type="button" data-prompt="What errors are happening in the monitoring namespace in the last 4 hours?">
                What errors are happening in monitoring?
                <small>Drill into the observability stack.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="What errors are happening in the services namespace in the last 4 hours?">
                What errors are happening in services?
                <small>Check user-facing app failures.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="What errors are happening in the outline namespace in the last 4 hours?">
                What errors are happening in outline?
                <small>Inspect the outline app specifically.</small>
              </button>
              <button class="prompt-card" type="button" data-prompt="What errors are happening in the external-secrets namespace in the last 4 hours?">
                What errors are happening in external-secrets?
                <small>Check secret sync and controller issues.</small>
              </button>
            </div>
          </section>
        </div>
      </div>
    </section>

    <div class="layout">
      <section class="panel">
        <div class="chips">
          <span class="chip">Route: <strong id="route">-</strong></span>
          <span class="chip">Tool: <strong id="tool">-</strong></span>
        </div>
        <h2 class="section-title">Summary</h2>
        <pre id="summary" class="empty">No result yet.</pre>
      </section>

      <section class="panel">
        <h2 class="section-title">Tool Args</h2>
        <pre id="toolArgs" class="empty">No result yet.</pre>
      </section>

      <section class="panel wide">
        <h2 class="section-title">Raw Result</h2>
        <pre id="rawResult" class="empty">No result yet.</pre>
      </section>
    </div>
  </main>

  <script>
    const questionEl = document.getElementById("question");
    const submitEl = document.getElementById("submit");
    const statusEl = document.getElementById("status");
    const routeEl = document.getElementById("route");
    const toolEl = document.getElementById("tool");
    const toolArgsEl = document.getElementById("toolArgs");
    const summaryEl = document.getElementById("summary");
    const rawResultEl = document.getElementById("rawResult");
    const promptEls = document.querySelectorAll(".prompt-card");
    const tabButtonEls = document.querySelectorAll(".tab-button");
    const tabPanelEls = document.querySelectorAll(".tab-panel");

    function showTab(tabName) {
      tabButtonEls.forEach((element) => {
        element.classList.toggle("active", element.dataset.tab === tabName);
      });
      tabPanelEls.forEach((element) => {
        element.classList.toggle("active", element.id === tabName);
      });
    }

    async function askQuestion() {
      const question = questionEl.value.trim();
      if (!question) {
        statusEl.textContent = "Enter a question first.";
        questionEl.focus();
        return;
      }

      submitEl.disabled = true;
      statusEl.textContent = "Running query...";

      try {
        const response = await fetch("/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Request failed");
        }

        routeEl.textContent = data.route;
        toolEl.textContent = data.tool;
        toolArgsEl.textContent = JSON.stringify(data.tool_args, null, 2);
        summaryEl.textContent = data.summary;
        rawResultEl.textContent = data.raw_result;

        toolArgsEl.classList.remove("empty");
        summaryEl.classList.remove("empty");
        rawResultEl.classList.remove("empty");
        statusEl.textContent = "Done.";
      } catch (error) {
        statusEl.textContent = error.message;
      } finally {
        submitEl.disabled = false;
      }
    }

    submitEl.addEventListener("click", askQuestion);
    tabButtonEls.forEach((element) => {
      element.addEventListener("click", () => showTab(element.dataset.tab));
    });
    promptEls.forEach((element) => {
      element.addEventListener("click", () => {
        questionEl.value = element.dataset.prompt || "";
        showTab("askTab");
        askQuestion();
      });
    });
    questionEl.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        askQuestion();
      }
    });
  </script>
</body>
</html>
"""


def _otlp_endpoint() -> str:
    return (
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or ""
    ).strip()


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _otlp_insecure(endpoint: str) -> bool:
    if not endpoint:
        return False
    if "OTEL_EXPORTER_OTLP_TRACES_INSECURE" in os.environ:
        return _env_flag("OTEL_EXPORTER_OTLP_TRACES_INSECURE", False)
    if "OTEL_EXPORTER_OTLP_INSECURE" in os.environ:
        return _env_flag("OTEL_EXPORTER_OTLP_INSECURE", False)
    return endpoint.startswith("http://")


def configure_tracing() -> bool:
    global _TRACING_CONFIGURED

    endpoint = _otlp_endpoint()
    if not endpoint:
        return False
    if _TRACING_CONFIGURED:
        return True

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.environ.get("OTEL_SERVICE_NAME", TRACER_NAME)}
        )
    )
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        insecure=_otlp_insecure(endpoint),
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _TRACING_CONFIGURED = True
    return True


async def traced_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    name: str,
    **kwargs,
) -> httpx.Response:
    tracer = trace.get_tracer(TRACER_NAME)
    parsed = urlparse(url)
    with tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
        span.set_attribute("http.request.method", "POST")
        span.set_attribute("url.path", parsed.path or "/")
        if parsed.netloc:
            span.set_attribute("server.address", parsed.netloc)

        response = await client.post(url, **kwargs)
        span.set_attribute("http.response.status_code", response.status_code)
        if response.is_error:
            span.set_status(Status(StatusCode.ERROR))
        return response


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
            response = await traced_post(
                client,
                self.endpoint,
                name=f"{self.name}.tools_call",
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
        response = await traced_post(
            client,
            self.endpoint,
            name=f"{self.name}.initialize",
            headers=MCP_HEADERS,
            json=payload,
        )
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
        response = await traced_post(
            client,
            self.endpoint,
            name=f"{self.name}.initialized_notification",
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
            response = await traced_post(
                client,
                f"{self.base_url}/api/generate",
                name="ollama.generate",
                json=payload,
            )
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
configure_tracing()
metrics_app = make_asgi_app()
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


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started_at = time.perf_counter()
    tracer = trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span(
        f"{request.method} {request.url.path}",
        kind=SpanKind.SERVER,
    ) as span:
        span.set_attribute("http.request.method", request.method)
        span.set_attribute("url.path", request.url.path)
        if request.headers.get("host"):
            span.set_attribute("server.address", request.headers["host"])
        try:
            response = await call_next(request)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise

        handler = request.url.path
        duration = max(time.perf_counter() - started_at, 0.0)
        HTTP_REQUEST_DURATION.labels(
            method=request.method,
            handler=handler,
        ).observe(duration)
        HTTP_REQUESTS.labels(
            method=request.method,
            handler=handler,
            status=str(response.status_code),
        ).inc()
        span.set_attribute("http.response.status_code", response.status_code)
        if response.status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))
        return response


app.mount("/metrics", metrics_app)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


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
