# cluster-query-router

Deterministic query router for cluster ops questions.

It maps a small set of plain-English questions to direct MCP tool calls, then uses a small local model to summarize the real tool output.

## What it does

- routes common cluster questions to Loki or Prometheus MCP tools
- avoids asking the model to perform tool calling
- uses `phi4-mini` only to summarize the real tool output

## API

`GET /`

A small web UI for asking questions, viewing the summary, and inspecting the raw tool output.

`POST /ask`

```json
{
  "question": "What errors are happening in my cluster right now?"
}
```

The response includes the matched route, raw MCP result, and a short summary.

Open the UI locally with:

```bash
python app.py
# then visit http://127.0.0.1:8080
```

## Local run

```bash
pip install -e .
python app.py
```

Environment variables:

- `LOKI_MCP_URL`
- `PROMETHEUS_MCP_URL`
- `OLLAMA_URL`
- `OLLAMA_MODEL`
