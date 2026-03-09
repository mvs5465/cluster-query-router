from fastapi.testclient import TestClient

import pytest

from app import MCPHTTPClient, app


def test_health_endpoint():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_endpoint():
    client = TestClient(app)
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "cluster_query_router_http_requests_total" in response.text
    assert "cluster_query_router_http_request_duration_seconds" in response.text


def test_extract_event_json_skips_non_json_data_lines():
    body = "\n".join(
        [
            "event: message",
            "data: keepalive",
            'data: {"jsonrpc":"2.0","result":{"ok":true}}',
        ]
    )

    payload = MCPHTTPClient._extract_event_json(body)

    assert payload["jsonrpc"] == "2.0"
    assert payload["result"]["ok"] is True


def test_extract_event_json_raises_when_no_json_payload_present():
    body = "\n".join(["event: message", "data: keepalive", "data: [DONE]"])

    with pytest.raises(RuntimeError, match="No MCP event payload found"):
        MCPHTTPClient._extract_event_json(body)
