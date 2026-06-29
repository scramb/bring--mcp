# bring-hermes

An **MCP server** that exposes the [Bring!](https://www.getbring.com/) shopping
list API as tools — so an assistant ("hermes") can read your lists and **add
items or whole recipes (with the correct quantity) to Bring!**.

It is served over the **MCP Streamable HTTP** transport, protected by an **API
key**, and built to run on **Docker** and **Kubernetes** (TLS terminated at the
Ingress).

Built on the actively maintained [`bring-api`](https://github.com/miaucl/bring-api)
Python library (the one behind Home Assistant's Bring! integration), which
provides batch updates, automatic token refresh, and a recipe-URL parser.

---

## What it can do

| Tool | Description |
| --- | --- |
| `list_shopping_lists` | List all lists on the account with their UUIDs. |
| `get_list_items` | Show the items currently on a list (name + quantity). |
| `add_item` | Add a single item; `quantity` → the item's specification. |
| `add_recipe` | Add **all ingredients of a recipe in one call**, with optional servings scaling. |
| `import_recipe_from_url` | Parse a recipe URL via Bring! and add its ingredients. |
| `remove_item` | Remove an item from a list. |
| `complete_item` | Mark an item as bought (moves it to "recently used"). |

> **Quantities:** Bring! stores an item's amount in a free-text
> `specification` field. Pass amounts like `"500 g"`, `"2"`, or `"1 EL"` in the
> `quantity` field — that is exactly what shows up under the item in the app.

### Servings scaling

`add_recipe` (and `import_recipe_from_url`) accept `base_servings` and
`target_servings`. When both are given, the leading number of each quantity is
scaled by `target / base` (units and text are preserved):

```text
"500 g" + base=4, target=6  ->  "750 g"
"2"     + base=4, target=6  ->  "3"
"1 EL"  + base=4, target=2  ->  "0.5 EL"
"Salz"  (no number)         ->  "Salz" (unchanged)
```

---

## Configuration

All configuration is via environment variables (see [`.env.example`](.env.example)):

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BRING_EMAIL` | ✅ | — | Bring! account email. |
| `BRING_PASSWORD` | ✅ | — | Bring! account password. |
| `MCP_API_KEY` | ✅ | — | One or more API keys (comma-separated) clients must present. |
| `BRING_DEFAULT_LIST` | — | first list | Default list (name or UUID) when a tool call omits one. |
| `HOST` | — | `0.0.0.0` | Bind address. |
| `PORT` | — | `8080` | Bind port. |
| `MCP_PATH` | — | `/mcp` | Path the MCP endpoint is served at. |
| `MCP_JSON_RESPONSE` | — | `true` | Plain-JSON Streamable HTTP responses; `false` = SSE-framed. |
| `LOG_LEVEL` | — | `INFO` | Python log level. |

---

## Run locally (Docker Compose)

```bash
cp .env.example .env
# edit .env: set BRING_EMAIL, BRING_PASSWORD, and a strong MCP_API_KEY
docker compose up --build
```

The server listens on `http://localhost:8080` with the MCP endpoint at
`http://localhost:8080/mcp`. Health endpoints (`/healthz`, `/readyz`) are
unauthenticated; everything else requires the API key.

Quick check:

```bash
curl -s http://localhost:8080/healthz            # -> ok
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/mcp   # -> 401 (no key)
```

## Run without Docker (uv)

```bash
uv sync
uv run bring-hermes        # reads the same env vars (e.g. via `set -a; . ./.env`)
```

---

## Connecting an MCP client

Point a Streamable-HTTP-capable MCP client at the `/mcp` URL and send the API
key in the `Authorization` header:

```json
{
  "mcpServers": {
    "bring": {
      "type": "http",
      "url": "https://bring-hermes.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_API_KEY>"
      }
    }
  }
}
```

(`X-API-Key: <MCP_API_KEY>` is accepted as an alternative to the bearer header.)

---

## Deploy to Kubernetes (Helm)

A Helm chart lives in [`helm/bring-hermes/`](helm/bring-hermes/) — see its
[README](helm/bring-hermes/README.md) for the full values reference. TLS is
terminated at the Ingress (cert-manager + Let's Encrypt in the example), or you
can expose it via the Gateway API instead — set `httpRoute.enabled=true` with a
`parentRefs` Gateway (see the chart README).

```bash
# Credentials as a managed Secret (recommended)
kubectl create namespace bring-hermes
kubectl -n bring-hermes create secret generic bring-hermes-credentials \
  --from-literal=BRING_EMAIL='you@example.com' \
  --from-literal=BRING_PASSWORD='your-bring-password' \
  --from-literal=MCP_API_KEY="$(openssl rand -hex 32)"

helm install bring-hermes ./helm/bring-hermes \
  --namespace bring-hermes \
  --set bring.existingSecret=bring-hermes-credentials \
  --set image.repository=ghcr.io/scramb/bring--mcp \
  --set image.tag=0.2.0 \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=bring-hermes.example.com
```

(Or pass `bring.email` / `bring.password` / `bring.apiKey` directly and let the
chart create the Secret — fine for testing, not for production.)

Notes:

- **Stateless** MCP transport → safe to run multiple replicas behind a normal
  Service (no session affinity required).
- **Probes**: `/healthz` is liveness; `/readyz` returns `503` until the first
  successful Bring! login, then `200`.
- **Responses**: plain-JSON Streamable HTTP by default (no SSE), so no special
  proxy buffering/timeout tuning is required at the Ingress.
- **Hardening**: runs as non-root with a read-only root filesystem and all
  capabilities dropped.

---

## Releases (GitHub Actions → GHCR)

Pushing a version tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml),
which publishes both artifacts to GHCR with the **same version as the git tag**
(a leading `v` is stripped so the value is valid SemVer for Helm):

```bash
git tag v0.2.0
git push origin v0.2.0
# -> image:  ghcr.io/<owner>/bring-hermes:0.2.0  (+ :latest)
# -> chart:  oci://ghcr.io/<owner>/charts/bring-hermes  version 0.2.0
```

The chart is packaged with `--version`/`--app-version` set to that version, and
its `image.tag` defaults to the chart `appVersion` — so image tag and chart
version always match.

### Build the image manually

```bash
docker build -t ghcr.io/your-org/bring-hermes:0.1.0 .
docker push ghcr.io/your-org/bring-hermes:0.1.0
```

---

## How it works

- One shared `Bring` client (single login) is created in the app's lifespan and
  reused across requests; the underlying library refreshes the access token
  automatically, and an expired-token error triggers a one-shot re-login.
- The FastMCP Streamable HTTP session manager is nested inside that lifespan, so
  the login is **not** re-run per request (which would otherwise happen in
  stateless mode).
- API-key auth is a small ASGI middleware in front of the MCP mount; health
  endpoints are exempt so Kubernetes probes work without credentials.
- The Streamable HTTP transport replies with plain JSON by default
  (`MCP_JSON_RESPONSE=true`); set it to `false` to get SSE-framed responses for
  streaming clients.

## Security

- Treat `MCP_API_KEY` as a secret; use a long random value and rotate it by
  setting a comma-separated list and removing the old key once clients migrate.
- Always serve behind TLS (the Ingress). The API key is a bearer credential —
  never expose `/mcp` over plain HTTP in production.
- The Bring! credentials grant full access to your shopping lists; scope and
  store them like any other secret.

## Credits

Huge thanks to the authors of [**`bring-api`**](https://github.com/miaucl/bring-api),
**Cyrill Raccaud ([@miaucl](https://github.com/miaucl))** and **Manfred
Dennerlein Rodelo** — the actively maintained Python Bring! client that also
powers Home Assistant's Bring! integration. This server is a thin MCP wrapper
around their work; all the real Bring! API heavy lifting (batch updates,
automatic token refresh, recipe parsing) is theirs. 💙

If you find this useful, please go star [`miaucl/bring-api`](https://github.com/miaucl/bring-api).

## License

MIT. `bring-api` is MIT-licensed by its respective authors. Bring! is a
trademark of its respective owners; this project is unofficial and not
affiliated with Bring! Labs AG.
