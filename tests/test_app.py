from fastapi.testclient import TestClient

from app import app


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
