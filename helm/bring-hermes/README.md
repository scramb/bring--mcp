# bring-hermes Helm chart

Deploy [**bring-hermes**](../../README.md) — an MCP server for the Bring!
shopping list API — to Kubernetes. TLS is terminated at the Ingress; the app
runs stateless behind an API key, so it scales horizontally without session
affinity.

## TL;DR

```bash
helm install bring-hermes ./helm/bring-hermes \
  --namespace bring-hermes --create-namespace \
  --set image.repository=ghcr.io/your-org/bring-hermes \
  --set image.tag=0.1.0 \
  --set bring.email='you@example.com' \
  --set bring.password='your-bring-password' \
  --set bring.apiKey="$(openssl rand -hex 32)"
```

> For production, **don't** pass credentials on the CLI — use an existing Secret
> (see below) or a values file kept out of version control.

### Install from GHCR (OCI)

Released charts are published to GHCR as OCI artifacts by the
[release workflow](../../.github/workflows/release.yml). The chart version equals
the git tag (and the image tag):

```bash
helm install bring-hermes \
  oci://ghcr.io/<owner>/charts/bring-hermes --version <version> \
  --namespace bring-hermes --create-namespace \
  --set bring.existingSecret=bring-hermes-credentials
```

The chart's default `image.repository` and `appVersion` already point at the
matching released image, so you don't need to set the image tag yourself.

## Install with an existing Secret (recommended)

Create a Secret with the three required keys, then reference it:

```bash
kubectl -n bring-hermes create secret generic bring-hermes-credentials \
  --from-literal=BRING_EMAIL='you@example.com' \
  --from-literal=BRING_PASSWORD='your-bring-password' \
  --from-literal=MCP_API_KEY="$(openssl rand -hex 32)"

helm install bring-hermes ./helm/bring-hermes \
  --namespace bring-hermes --create-namespace \
  --set bring.existingSecret=bring-hermes-credentials \
  --set image.tag=0.1.0
```

## Enable the Ingress (with TLS)

```yaml
# my-values.yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: bring-hermes.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: bring-hermes-tls
      hosts:
        - bring-hermes.example.com
```

```bash
helm upgrade --install bring-hermes ./helm/bring-hermes \
  -n bring-hermes -f my-values.yaml
```

The MCP endpoint is then `https://bring-hermes.example.com/mcp` (send
`Authorization: Bearer <MCP_API_KEY>`).

## Values

| Key | Default | Description |
| --- | --- | --- |
| `replicaCount` | `2` | Replicas (ignored when `autoscaling.enabled`). |
| `image.repository` | `ghcr.io/your-org/bring-hermes` | Image repository. |
| `image.tag` | `""` (chart `appVersion`) | Image tag. |
| `image.pullPolicy` | `IfNotPresent` | Image pull policy. |
| `imagePullSecrets` | `[]` | Pull secrets for private registries. |
| `bring.existingSecret` | `""` | Name of an existing Secret with `BRING_EMAIL`, `BRING_PASSWORD`, `MCP_API_KEY`. If set, the next three are ignored. |
| `bring.email` | `""` | Bring! account email (used only if no `existingSecret`). |
| `bring.password` | `""` | Bring! account password. |
| `bring.apiKey` | `""` | API key(s) clients must present (comma-separated for rotation). |
| `config.port` | `8080` | Container port. |
| `config.mcpPath` | `/mcp` | Path the MCP endpoint is served at. |
| `config.logLevel` | `INFO` | Log level. |
| `config.jsonResponse` | `true` | Plain-JSON Streamable HTTP responses; `false` = SSE-framed. |
| `config.defaultList` | `""` | Default list (name or UUID); empty = first list. |
| `extraEnv` | `[]` | Additional env vars. |
| `service.type` | `ClusterIP` | Service type. |
| `service.port` | `80` | Service port. |
| `ingress.enabled` | `false` | Create an Ingress. |
| `ingress.className` | `nginx` | Ingress class. |
| `ingress.annotations` | `{}` | Ingress annotations (e.g. cert-manager issuer). |
| `ingress.hosts` | `bring-hermes.example.com` | Host/path rules. |
| `ingress.tls` | `[]` | TLS config (secretName + hosts). |
| `resources` | `50m/128Mi` … `500m/256Mi` | Requests/limits. |
| `autoscaling.enabled` | `false` | Enable HPA. |
| `autoscaling.minReplicas` / `maxReplicas` | `2` / `5` | HPA bounds. |
| `autoscaling.targetCPUUtilizationPercentage` | `80` | HPA CPU target. |
| `autoscaling.targetMemoryUtilizationPercentage` | `0` (off) | HPA memory target. |
| `livenessProbe` | `GET /healthz` | Liveness probe spec. |
| `readinessProbe` | `GET /readyz` | Readiness probe spec. |
| `podSecurityContext` | non-root, RuntimeDefault | Pod security context. |
| `securityContext` | read-only FS, drop ALL caps | Container security context. |
| `serviceAccount.create` | `true` | Create a ServiceAccount. |
| `nodeSelector` / `tolerations` / `affinity` | `{}` / `[]` / `{}` | Scheduling. |

## Test

```bash
helm test bring-hermes -n bring-hermes
```

Runs a pod that curls `/healthz` against the Service.

## Uninstall

```bash
helm uninstall bring-hermes -n bring-hermes
```

---

## Credits

This server — and therefore this chart — stands on the shoulders of
[**`bring-api`**](https://github.com/miaucl/bring-api) by **Cyrill Raccaud
([@miaucl](https://github.com/miaucl))** and **Manfred Dennerlein Rodelo**, the
excellent Python Bring! client that also powers Home Assistant's Bring!
integration. All the hard Bring! API work is theirs — thank you! 💙

`bring-api` is MIT-licensed. This project is unofficial and not affiliated with
Bring! Labs AG.
