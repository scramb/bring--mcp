# bring-hermes

An **MCP server** that exposes the [Bring!](https://www.getbring.com/) shopping
list API as tools — so an assistant ("hermes") can read your lists and **add
items or whole recipes (with the correct quantity) to Bring!**.

It is served over the **MCP Streamable HTTP** transport and is its own **OAuth
2.1 authorization server**: users connect it from claude.ai (or any MCP client
with OAuth support) and sign in **with their own Bring! account** on its login
page. Every user only ever sees and edits their own lists. Built to run on
**Docker/Podman** and **Kubernetes**, with Postgres for state.

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
| `PUBLIC_URL` | ✅ | — | Public base URL, e.g. `https://bring.example.com`. OAuth issuer; the MCP endpoint is `PUBLIC_URL` + `MCP_PATH`. |
| `DATABASE_URL` | ✅ | — | Postgres URL for OAuth clients, tokens and linked accounts. |
| `TOKEN_ENCRYPTION_KEY` | ✅ | — | Fernet key the stored Bring! refresh tokens are encrypted with. |
| `HOST` | — | `0.0.0.0` | Bind address. |
| `PORT` | — | `8080` | Bind port. |
| `MCP_PATH` | — | `/mcp` | Path the MCP endpoint is served at. |
| `MCP_JSON_RESPONSE` | — | `true` | Plain-JSON Streamable HTTP responses; `false` = SSE-framed. |
| `ACCESS_TOKEN_TTL` | — | `3600` | Lifetime of issued access tokens (seconds). |
| `REFRESH_TOKEN_TTL` | — | `7776000` | Lifetime of issued refresh tokens (seconds, 90 days; rotated on use). |
| `LOG_LEVEL` | — | `INFO` | Python log level. |

Generate a key:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

---

## Run locally (Compose)

```bash
cp .env.example .env
# edit .env: set TOKEN_ENCRYPTION_KEY
podman compose up --build     # or: docker compose up --build
```

The server listens on `http://localhost:8080` with the MCP endpoint at
`http://localhost:8080/mcp`. `/healthz` and `/readyz` (database reachable) are
unauthenticated; `/mcp` answers `401` with a pointer to the OAuth metadata
until a client presents a token.

## Run without containers (uv)

```bash
uv sync
uv run bring-hermes        # reads the same env vars (e.g. via `set -a; . ./.env`)
```

---

## Connecting an MCP client

### claude.ai

*Settings → Connectors → Add custom connector*, URL
`https://<your host>/mcp`. Claude registers itself (dynamic client
registration), opens the **"Mit Bring! anmelden"** page, and after you sign in
with your Bring! e-mail and password the tools are available.

### Other clients

Any client implementing the MCP authorization spec works the same way, e.g.
Claude Code:

```bash
claude mcp add --transport http bring https://<your host>/mcp
# then /mcp in Claude Code to sign in
```

---

## Deploy to Kubernetes (Helm)

A Helm chart lives in [`helm/bring-hermes/`](helm/bring-hermes/) — see its
[README](helm/bring-hermes/README.md) for the full values reference. TLS is
terminated at the Ingress (cert-manager + Let's Encrypt in the example), or you
can expose it via the Gateway API instead — set `httpRoute.enabled=true` with a
`parentRefs` Gateway (see the chart README).

```bash
kubectl create namespace bring-hermes
kubectl -n bring-hermes create secret generic bring-hermes-credentials \
  --from-literal=DATABASE_URL='postgres://user:pass@postgres:5432/bring' \
  --from-literal=TOKEN_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

helm install bring-hermes ./helm/bring-hermes \
  --namespace bring-hermes \
  --set existingSecret=bring-hermes-credentials \
  --set config.publicUrl=https://bring.example.com \
  --set httpRoute.hostnames[0]=bring.example.com
```

The chart brings no database; point `DATABASE_URL` at any Postgres.

Notes:

- **One replica**: Bring! sessions are cached in memory and the login rate
  limit is per process; all durable state is in Postgres.
- **Probes**: `/healthz` is liveness; `/readyz` returns `503` while the
  database is unreachable.
- **Responses**: plain-JSON Streamable HTTP by default (no SSE), so no special
  proxy buffering/timeout tuning is required at the Ingress.
- **Hardening**: runs as non-root with a read-only root filesystem and all
  capabilities dropped.

### On tethys (Flux)

The running instance at `https://bring.meininger.cloud/mcp` is not
installed with Helm: Flux on the tethys cluster applies
[`deploy/`](deploy/) from `master`. A rollout there is a tag bump in
`deploy/kustomization.yaml` — see [`deploy/README.md`](deploy/README.md).

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

- **OAuth**: the MCP SDK provides the metadata endpoints
  (`/.well-known/oauth-protected-resource/mcp`,
  `/.well-known/oauth-authorization-server`), dynamic client registration,
  `/authorize`, `/token` (PKCE) and `/revoke`. `/authorize` parks the request
  and sends the browser to `/login`.
- **Login**: the e-mail and password go to Bring! exactly once. On success the
  Bring! user is linked, an authorization code is issued and the browser
  returns to the client. The access token's subject is the Bring! user uuid.
- **Bring! sessions**: only the Bring! refresh token is kept, Fernet-encrypted
  in Postgres. A session is restored from it on demand and cached per user;
  a rotated Bring! token is written back. If Bring! rejects it, the user's
  OAuth tokens are revoked and the client asks them to sign in again.
- **Tokens**: opaque random strings, stored only as SHA-256 hashes. Access
  tokens live an hour; refresh tokens rotate on every use.

## Security

- The Bring! password is never stored or logged. Users still type it into
  *this* server's page, not Bring!'s -- only offer the server to people who
  trust its operator.
- Any Bring! account can sign in. Each user only reaches their own lists.
- Failed logins are limited to 5 per e-mail address per 10 minutes.
- A database dump alone grants nothing: tokens are hashed, Bring! tokens
  encrypted with `TOKEN_ENCRYPTION_KEY` (keep that one in a secret store).
  Changing the key only forces every user to sign in again.
- Always serve behind TLS.

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
