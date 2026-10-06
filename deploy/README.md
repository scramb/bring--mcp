# Deployment on tethys (`deploy/`)

## What this is

`deploy/` is a Kustomize base with everything bring-hermes needs on the
tethys cluster: the `Deployment`, the `Service`, the public `HTTPRoute`
for `bring.meininger.cloud` and the `ExternalSecret` that pulls the
credentials from OpenBao.

Flux in [`scramb/tethys`](https://github.com/scramb/tethys) watches the
`master` branch of this repo (only `deploy/`) and applies it into the
namespace `bring-mcp`, impersonating `bring-mcp-deployer`. Nobody pushes
into the cluster from here. tethys keeps what the app must not decide for
itself (`base-setup/workloads/bring-mcp/` there):

- the namespace `bring-mcp`,
- the L4 isolation: only the public gateway may connect to the pod,
- the guard on the gateway that rejects requests without an
  `Authorization` header,
- the binding: `GitRepository`, `Kustomization` and the deployer's rights.

`bring-mcp-deployer` may manage exactly the kinds that are here --
`Deployment`, `Service`, `HTTPRoute`, `ExternalSecret` -- in this
namespace only. A new kind (a `ConfigMap`, a `ServiceAccount`, ...) needs
a change of that `Role` in tethys first; otherwise the Kustomization
there turns `Ready False` with `forbidden` and the last applied state
keeps running.

The Helm chart in `helm/` is not used on tethys.

## Rollout: bump the tag

1. Cut a release: push a tag `vX.Y.Z` (or `X.Y.Z`); the `Release` workflow
   builds `ghcr.io/scramb/bring--mcp:X.Y.Z`. Wait until it is green.
2. Change `newTag` in `deploy/kustomization.yaml` to `X.Y.Z` -- never
   `latest`, or Git no longer says what runs. New mandatory configuration
   goes into `deployment.yaml` in the same commit.
3. Check the render offline, and with a cluster context the diff:

   ```bash
   kubectl kustomize deploy/ >/dev/null
   kubectl diff -k deploy/
   ```

4. Commit and push to `master`. Flux polls every 5 minutes; to apply now:

   ```bash
   flux reconcile kustomization bring-mcp -n bring-mcp --with-source
   ```

## Check

```bash
flux get sources git -n bring-mcp
flux get kustomizations -n bring-mcp
kubectl -n bring-mcp get pods,externalsecret
curl -s -o /dev/null -w '%{http_code}\n' https://bring.meininger.cloud/mcp   # 403, gateway
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer wrong' \
  https://bring.meininger.cloud/mcp                                           # 401, app
```

## Credentials

Live in OpenBao, never in Git (`credentials.yaml`):

| Path | Properties |
|---|---|
| `kv/bring-mcp/bring` | `email`, `password` (Bring! account) |
| `kv/bring-mcp/api-key` | `api_key` (comma-separated for rotation) |

How to write them is described in tethys, `base-setup/secrets/README.md`,
section 14. The current API key for an MCP client:

```bash
kubectl -n bring-mcp get secret bring-hermes-credentials \
  -o jsonpath='{.data.MCP_API_KEY}' | base64 -d; echo
```
