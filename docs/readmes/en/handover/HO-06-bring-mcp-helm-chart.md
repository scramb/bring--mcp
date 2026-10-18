> [🇩🇪 Deutsch](../../../handover/HO-06-bring-mcp-helm-chart.md) (Leitfassung) · 🇬🇧 **English**

# HO-06 — bring-hermes ships as a generic Helm chart, the values for tethys live in tethys-cluster

Status: handed over · Version: 1 · As of: 2026-10-18
Sender: `scramb/tethys-cluster` (everything Flux applies on the Talos cluster on tethys) · ADR 0020 delivery as a Helm chart · Receiver: `scramb/bring--mcp` (the app bring-hermes)
Open decisions are decided by: the owner of both repos (scramb).

> **Receiver copy.** Operator-specific details (hostnames, gateway, listener, secret store, policy names) are replaced by placeholders such as `bring.example.com` or `<gateway>`. The binding, complete version lives in `scramb/tethys-cluster` at `docs/handover/HO-06-bring-mcp-helm-chart.md`; changes are made only there.

## Occasion

bring-hermes, the MCP server for the Bring! shopping list, runs publicly at `https://bring.example.com/mcp` and is its own OAuth server. Today Flux on tethys fetches the Kustomize directory `deploy/` from `scramb/bring--mcp` (branch `master`, state `0b010da`) and applies it in the namespace `bring-mcp`:
- Deployment, Service and `HTTPRoute` `bring-hermes`,
- the CNPG `Cluster` `bring-mcp-db` (database `bring`, role `bring`),
- the `ExternalSecret` `bring-hermes-credentials` (`deploy/credentials.yaml`), which fetches `TOKEN_ENCRYPTION_KEY` from the secret store of tethys.

Hostname, gateway, StorageClass and secret paths of tethys therefore sit in the app repo, as do sections about tethys in `README.md` and `deploy/README.md`. The owner decided on 2026-10-18 (ADR 0020 in tethys-cluster, core under Constraints): every app ships a Helm chart from its own repo that does not know tethys. Everything that only applies to tethys lives in `scramb/tethys-cluster` under `workloads/bring-mcp/`, the database included. The app repo already contains a chart (`helm/bring-hermes/`) that tethys has not used so far.

tethys is being rebuilt. The operator's internal domain is renamed in the process, and the data comes back from dumps. `kubehut` is the former name of the operator's cluster; the name is operator-specific and does not belong in the app repo. The switch to the chart happens on the rebuilt cluster. The public hostname stays.

## Benefit

tethys runs bring-hermes from a generic Helm chart of the app repo, all tethys values live in tethys-cluster, and the app repo no longer contains anything that only applies to tethys.

**Done when:** all of the following hold:
- The `HelmRelease` for bring-hermes in the namespace `bring-mcp` reports `Ready True` with the chart from `scramb/bring--mcp`, `master`, rendered with `workloads/bring-mcp/values.yaml` from tethys-cluster, and `https://bring.example.com` answers as it does today (acceptance under Integration).
- The first release after the switch went live without a commit in tethys-cluster (AF-05).
- On `master` of `scramb/bring--mcp`, `deploy/` is gone and `git grep -n -i -E 'tethys|kubehut|meininger\.(cloud|internal)' -- ':!docs/handover' ':!docs/readmes/*/handover'` finds nothing (AF-07).

## Requirements

Order = recommended order of work. Must: no integration without it · Should: needed before "Done when" · Could: blocks nothing, may come later. The sender checks every criterion offline against the reported commit or on the repo; acceptance in the cluster is under Integration.

| ID | Requirement | Acceptance criterion (checkable from outside) | Priority | Depends on |
|---|---|---|---|---|
| AF-01 | The chart on `master`, rendered with the example values from the contract, yields a bring-hermes instance equivalent to today's from `deploy/`. | `helm template bring-hermes <chart-path> -n bring-mcp -f values.yaml` (example values below) runs without error. The output meets every point of "Equivalent" in the contract, and every difference of the pod template from `kubectl kustomize deploy/` @`0b010da` is explained there. The chart's default image equals the image in `deploy/kustomization.yaml` at the reported commit (today `ghcr.io/scramb/bring--mcp:0.4.0`). | Must | — |
| AF-02 | By default the chart creates no Secrets; it refers to Secrets by a configurable name. | `helm template` with the example values and with the chart defaults (plus `--set config.publicUrl=https://example.test`) produces no object `kind: Secret`. A Secret from values is only created when an explicit switch is turned on. Today the Secret in `existingSecret` must also hold `DATABASE_URL`; in future it does not. For other users of the chart this is a breaking change: the chart version in `Chart.yaml` rises at least in the minor position, and the chart README describes the change. | Must | AF-01 |
| AF-03 | Chart and default values contain nothing that only applies to tethys. | `grep -r -n -i -E 'tethys\|kubehut\|meininger\.(cloud\|internal)' <chart-path>` finds nothing. `helm template x <chart-path> --set config.publicUrl=https://example.test` runs without error and produces no `Namespace` object, no object of a `*.toolkit.fluxcd.io` API, no hostname, no `storageClass` and no `HTTPRoute` with a preset gateway (today one is preset). If the route is on by default, rendering without `parentRefs` fails with a message. | Must | AF-01 |
| AF-04 | Unknown or misspelled values make rendering fail instead of being silently ignored. | With the example values plus a typo key (e.g. `config.publicURL`), `helm template` fails with a message naming the key. Why Must: tethys rolls out every commit, so a silently ignored value would go live at once. | Must | AF-01 |
| AF-05 | A release of bring-hermes is a commit in the app repo that only comes after the image. | At the first release after the switch, the commit that raises the tag changes a file under the chart path. It comes after the green run of the release workflow that built the image. `workloads/bring-mcp/` in tethys-cluster gets no commit for it. | Should | AF-01, Integration |
| AF-06 | The chart's values interface is documented, including the object kinds the chart creates. | The chart's `values.yaml` or the chart README describes every value from the example below. It names the object kinds produced with the example values. `helm lint <chart-path> -f values.yaml` with the example values is green. | Should | AF-01 |
| AF-07 | After the switch the app repo contains no delivery and no text that only applies to tethys. | After the issue "HO-06: switched" (see Integration), `deploy/` is gone from `master` and `git grep -n -i -E 'tethys\|kubehut\|meininger\.(cloud\|internal)' -- ':!docs/handover' ':!docs/readmes/*/handover'` finds nothing, not in `README.md` either. The surname Meininger in copyright or author lines does not match the pattern. | Should | Integration |

## Interface contract

- **Direction and trigger:** Flux on tethys fetches the chart through a `GitRepository` from `https://github.com/scramb/bring--mcp`, branch `master`. The repo is public, there is no key. The source is limited to the chart path; only a commit that changes something below it triggers a new render. The `HelmRelease` then renders the chart with `workloads/bring-mcp/values.yaml` from tethys-cluster, even without a new chart version. The receiver chooses the chart path and names it in the signal (today `helm/bring-hermes`). The app repo pushes nothing into the cluster.
- **Signals:**
  - Receiver → tethys: an issue in `scramb/tethys-cluster` titled "HO-06: chart ready". It names the commit SHA on `master`, the chart path and the fulfilled AFs.
  - tethys → receiver: an issue in `scramb/bring--mcp` titled "HO-06: switched", once tethys runs from the chart. It unlocks AF-07.
- **Data shape:** The interface is the chart's values. tethys does not set the image tag; it comes from the chart. Example for `workloads/bring-mcp/values.yaml`. **Only the values on the right are binding.** The key structure is a proposal that follows today's chart (`database` is new). The receiver fixes the final name of each key with AF-01.

```yaml
# workloads/bring-mcp/values.yaml in scramb/tethys-cluster
# Rendered with releaseName bring-hermes into the namespace bring-mcp.
fullnameOverride: bring-hermes            # Deployment, Service, HTTPRoute keep their names
podLabels:
  istio.io/dataplane-mode: ambient        # joins the Istio ambient mesh
serviceAccount:
  automount: false                        # no API token in the pod, as today
config:
  publicUrl: https://bring.example.com
  logLevel: INFO
existingSecret: bring-hermes-credentials  # key TOKEN_ENCRYPTION_KEY
database:
  existingSecret: bring-mcp-db-app        # generated by CloudNativePG
  uriKey: uri                             # postgresql://bring:<password>@<db-host>:5432/bring
resources:
  requests:
    cpu: 20m
    memory: 96Mi
  limits:
    memory: 256Mi
httpRoute:
  enabled: true
  parentRefs:
    - name: <gateway>
      namespace: <gateway-namespace>
      sectionName: <listener>
  hostnames:
    - bring.example.com
```

- **Equivalent** means, exhaustively:
  - Deployment, Service and `HTTPRoute` are named `bring-hermes`, one replica.
  - The pod carries `app.kubernetes.io/name: bring-hermes` and the additional labels from the values. The chart may keep its selector labels as they are today (`name` and `instance`).
  - Container port `8080` (name `http`). The chart decides which port the Service offers.
  - Environment: `PUBLIC_URL` and `LOG_LEVEL` from the values, `PORT=8080`, `MCP_PATH=/mcp`, `MCP_JSON_RESPONSE=true`. `TOKEN_ENCRYPTION_KEY` comes from the Secret in `existingSecret`. `DATABASE_URL` comes from the database Secret, whose name is a value. CloudNativePG puts the keys `username`, `password`, `host`, `port`, `dbname`, `uri` and `jdbc-uri` there; the chart may use `uri`, and the key is a value or documented. Whether the variables come inline or through a `ConfigMap` is free.
  - Probes: liveness `GET /healthz`, readiness `GET /readyz` (returns `503` while the database is unreachable), timings as in `deploy/deployment.yaml`.
  - `securityContext`: `runAsNonRoot`, user and group `10001`, `fsGroup` `10001`, `seccompProfile` `RuntimeDefault`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, all capabilities dropped. An `emptyDir` at `/tmp`, because the file system is read-only.
  - No service account token in the pod.
  - tethys sets `resources` through the values.
- **What tethys provides and the chart only refers to by name:**
  - the namespace `bring-mcp`,
  - the Secret `bring-hermes-credentials` with the key `TOKEN_ENCRYPTION_KEY`. Today `deploy/credentials.yaml` in the app repo provides it. In future it comes from an `ExternalSecret` in tethys-cluster that fetches the value from the secret store of tethys. The content is a Fernet key. If it changes, every user has to sign in once more.
  - the CNPG `Cluster` `bring-mcp-db` with the Secret `bring-mcp-db-app`. The chart brings no database.
  - the isolation: an Istio `AuthorizationPolicy` that targets the pod label `app.kubernetes.io/name: bring-hermes` and admits only the public gateway,
  - the gateway `<gateway-namespace>/<gateway>` with listener and TLS certificate.
- **Object kinds:** In AF-06 the chart names the kinds it produces with the example values. Today these are Deployment, Service, `HTTPRoute`, `ConfigMap` and `ServiceAccount`. tethys grants the helm-controller exactly these kinds in the namespace `bring-mcp`.
- **Error behaviour:**
  - If the chart cannot be rendered with the tethys values, the last installed state keeps running, and the `HelmRelease` shows `Ready False` with the cause.
  - If a Secret or a key in it is missing, the new pod stays in `CreateContainerConfigError`. If the database is unreachable, `/readyz` returns `503`. tethys installs with a wait for readiness, so such an upgrade fails, is rolled back and is not retried. The chart must therefore keep the readiness probe on `/readyz`.
  - If the app repo is unreachable, the last state keeps running.
- **Compatibility:**
  - A breaking change to the tethys values is a renamed or removed key, a new mandatory key in a Secret, a new port or a new outbound connection. It reaches `master` only after tethys has confirmed in an issue in `scramb/tethys-cluster` that the new values are in place. The chart version in `Chart.yaml` rises with it.
  - New values have defaults with which the example values keep rendering unchanged.
  - A new object kind is a breaking change too, because without a grant in tethys it fails with `forbidden`.
- **Binding version:** The chart's values interface (`values.yaml`, `values.schema.json`) in the app repo. tethys-cluster holds only its own values in `workloads/bring-mcp/values.yaml`. Until AF-01 is delivered, this section applies.

## Out of scope

- Namespace, `ExternalSecret`, database, isolation, `GitRepository`, `HelmRelease` and the helm-controller's rights. tethys-cluster builds all of that.
- Adopting today's objects. tethys recreates Deployment, Service and `HTTPRoute` at the switch (see Integration).
- Publishing the chart as an OCI artifact in GHCR. It may stay; tethys does not use it.
- Automatic image tag updates, multiple replicas, database backup.
- Changes to the app's behaviour (OAuth, login, MCP tools).

## Constraints

All from ADR 0020 (owner, 2026-10-18), not negotiable:
- The chart lives in the app repo. Flux fetches it through a `GitRepository` on the app repo, branch `master`, and installs it with a `HelmRelease`.
- The chart contains no hostname, no gateway and no `parentRefs` to one, no StorageClass, no namespace object, no Flux objects and no names or domains of the operator (search pattern in AF-03).
- The default image tag lives in the chart. A release is a commit in the app repo; the tethys values set no tag. The tag rises only once the image is in the registry.
- tethys provides the secrets; the chart refers to them by name. Creating Secrets from values is allowed only as an explicitly enabled option (default off); tethys never uses it.
- Databases belong to tethys. The chart brings no database and reads the connection from a Secret whose name is a value.
- Pods accept additional labels from the values, and `app.kubernetes.io/name: bring-hermes` stays stable, because the isolation targets it.
- The chart creates no `AuthorizationPolicy`, `Role`, `RoleBinding` or `Namespace`. Istio ORs ALLOW policies, so an additional policy could undo the isolation.
- Breaking changes to the tethys values wait for tethys' confirmation (see Compatibility).
- The switch may recreate Deployment, Service and `HTTPRoute` once (about one minute of downtime). Database and volumes stay untouched.

## Assumptions

| ID | Assumption | Basis | If wrong |
|---|---|---|---|
| A-01 | The existing chart `helm/bring-hermes` on `master` is a usable starting point. It supports `existingSecret`, `podLabels`, `fullnameOverride`, `httpRoute.*` and `extraEnv`. | Content of `master` @`0b010da` | More work for AF-01, nothing else |
| A-02 | The app needs only `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `PUBLIC_URL` and the optional values from `.env.example`. | `deploy/deployment.yaml`, `.env.example` | Further values enter the contract as an announced breaking change |
| A-03 | The app can use the CloudNativePG `uri` unchanged as `DATABASE_URL` (scheme `postgresql://`, without `sslmode`). Today `deploy/` sets `postgres://…?sslmode=require`. | Layout of the CloudNativePG `-app` Secret | The chart assembles the URL from `username`, `password`, `host`, `port` and `dbname`; AF-01 stays, only the way changes |

## Open decisions

None. Where the database lives and whether the switch may recreate objects was decided by the owner on 2026-10-18 (see Constraints). How the chart meets the requirements is the receiver's decision.

## Integration

Integration work package: [`scramb/tethys-cluster#2`](https://github.com/scramb/tethys-cluster/issues/2).

The switch in tethys-cluster starts with the issue "HO-06: chart ready" and runs on the rebuilt cluster. Until then bring-hermes keeps running unchanged from `deploy/`, and the app repo only changes the tag there.

The switch step in tethys-cluster creates under `workloads/bring-mcp/`:
- namespace, isolation and the `ExternalSecret` `bring-hermes-credentials`,
- the `Cluster` `bring-mcp-db`, restored from the dump,
- a `GitRepository` limited to the chart path,
- a `HelmRelease` with `releaseName: bring-hermes`, its own restricted service account, rendering on every revision, waiting for readiness and rollback without retry,
- `values.yaml` per the contract.

tethys removes the previous Flux `Kustomization` for `deploy/` before the `HelmRelease` installs. The old objects disappear with it, and the chart creates them anew. The receiver has nothing to do for this.

tethys accepts from outside:
- AF-01 to AF-04 offline against the reported SHA,
- live: `HelmRelease` `Ready True`; `curl -s https://bring.example.com/.well-known/oauth-authorization-server` returns `issuer` `https://bring.example.com`; `curl -s -o /dev/null -w '%{http_code}' https://bring.example.com/mcp` returns `401`; the number of rows in `accounts` matches the dump.

Afterwards tethys reports with "HO-06: switched", and the receiver completes AF-07. tethys commits to a commit under the chart path being live within 15 minutes. It checks this together with AF-05 at the first release after the switch.

## Changes

| Version | Date | What |
|---|---|---|
| 1 | 2026-10-18 | Draft; handed over |
