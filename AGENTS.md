# Cluster Query Router

## Scope
- Deterministic FastAPI service that maps common cluster questions to direct MCP calls, then summarizes the real tool output.
- Keep the routing logic explicit and predictable; this repo should avoid opaque agent behavior.

## Local Development
- Install deps with `pip install -e .`
- Run locally with:
  - `python app.py`
- The local UI is served at `http://127.0.0.1:8080`

## Routing Rules
- Preserve the route matching behavior unless the user explicitly asks to change the question contract.
- Keep MCP request payloads, tool names, and response shapes stable unless you are intentionally updating the client contract too.
- The router should prefer deterministic tool selection over “smart” model behavior.
- If you change the model prompt or summary logic, keep the raw MCP output visible for debugging.

## Environment
- Expected env vars:
  - `LOKI_MCP_URL`
  - `PROMETHEUS_MCP_URL`
  - `OLLAMA_URL`
  - `OLLAMA_MODEL`
- Do not hardcode local LAN endpoints into the app code.

## Helm And Releases
- If a PR changes anything under `chart/`, bump `chart/Chart.yaml` `version` in the same PR.
- Bump `appVersion` when the deployed application behavior materially changes.
- Treat chart and app versions as release metadata, not deployment selectors; ArgoCD deploys from `main`.
- Use loose semver tracking:
  - bump `version` for chart changes, usually patch unless the chart interface changes materially
  - bump `appVersion` for meaningful app changes, including routing, UI, or behavior changes
  - keeping `version` and `appVersion` aligned is acceptable when that is the simplest honest representation
- If ports, env vars, or image expectations change, update the matching ArgoCD app in `local-k8s-apps`.

## Verification
- Use `python -m py_compile app.py` for quick syntax validation.
- For behavioral changes, run the app locally and verify both the web UI and `POST /ask`.
- Run `helm template cluster-query-router ./chart` for chart changes.
