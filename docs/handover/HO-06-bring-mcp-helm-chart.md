> 🇩🇪 **Deutsch** (Leitfassung) · [🇬🇧 English](../readmes/en/handover/HO-06-bring-mcp-helm-chart.md)

# HO-06 — bring-hermes kommt als generisches Helm-Chart, die Werte für tethys liegen in tethys-cluster

Status: übergeben · Version: 1 · Stand: 2026-10-18
Absender: `scramb/tethys-cluster` (alles, was Flux auf dem Talos-Cluster auf tethys anwendet) · ADR 0020 Auslieferung als Helm-Chart · Empfänger: `scramb/bring--mcp` (die App bring-hermes)
Offene Entscheidungen entscheidet: der Eigentümer beider Repos (scramb).

> **Empfängerkopie.** Betreiberspezifische Angaben (Hostnamen, Gateway, Listener, Secret-Speicher, Policy-Namen) sind durch Platzhalter wie `bring.example.com` oder `<gateway>` ersetzt. Die verbindliche, vollständige Fassung liegt in `scramb/tethys-cluster` unter `docs/handover/HO-06-bring-mcp-helm-chart.md`; geändert wird nur dort.

## Anlass

bring-hermes, der MCP-Server für die Bring!-Einkaufsliste, läuft öffentlich unter `https://bring.example.com/mcp` und ist sein eigener OAuth-Server. Heute holt Flux auf tethys das Kustomize-Verzeichnis `deploy/` aus `scramb/bring--mcp` (Branch `master`, Stand `0b010da`) und wendet es im Namespace `bring-mcp` an:
- Deployment, Service und `HTTPRoute` `bring-hermes`,
- den CNPG-`Cluster` `bring-mcp-db` (Datenbank `bring`, Rolle `bring`),
- das `ExternalSecret` `bring-hermes-credentials` (`deploy/credentials.yaml`), das `TOKEN_ENCRYPTION_KEY` aus dem Secret-Speicher von tethys holt.

Hostname, Gateway, StorageClass und Secret-Pfade von tethys stehen damit im App-Repo, ebenso Abschnitte über tethys in `README.md` und `deploy/README.md`. Der Eigentümer hat am 2026-10-18 entschieden (ADR 0020 in tethys-cluster, Kern unter Randbedingungen): Jede App liefert ein Helm-Chart aus ihrem Repo, das tethys nicht kennt. Alles, was nur auf tethys gilt, liegt in `scramb/tethys-cluster` unter `workloads/bring-mcp/`, auch die Datenbank. Das App-Repo enthält bereits ein Chart (`helm/bring-hermes/`), das tethys bisher nicht nutzt.

tethys wird gerade neu aufgebaut. Die interne Domain des Betreibers wird dabei umbenannt, die Daten kommen aus Dumps zurück. `kubehut` ist der frühere Name des Clusters des Betreibers; der Name ist betreiberspezifisch und gehört nicht ins App-Repo. Der Umstieg auf das Chart geschieht auf dem neu aufgebauten Cluster. Der öffentliche Hostname bleibt.

## Nutzen

tethys betreibt bring-hermes aus einem generischen Helm-Chart des App-Repos, alle tethys-Werte liegen in tethys-cluster, und das App-Repo enthält nichts mehr, das nur für tethys gilt.

**Fertig, wenn:** Alle Punkte gelten:
- Das `HelmRelease` für bring-hermes im Namespace `bring-mcp` meldet `Ready True` mit dem Chart aus `scramb/bring--mcp`, `master`, gerendert mit `workloads/bring-mcp/values.yaml` aus tethys-cluster, und `https://bring.example.com` antwortet wie heute (Abnahme unter Integration).
- Das erste Release nach dem Umstieg ist ohne Commit in tethys-cluster in Betrieb gegangen (AF-05).
- Auf `master` von `scramb/bring--mcp` fehlt `deploy/`, und `git grep -n -i -E 'tethys|kubehut|meininger\.(cloud|internal)' -- ':!docs/handover' ':!docs/readmes/*/handover'` findet nichts (AF-07).

## Anforderungen

Reihenfolge = empfohlene Abarbeitungsreihenfolge. Muss: ohne das keine Integration · Soll: vor „Fertig, wenn“ gebraucht · Kann: blockiert nichts, darf später kommen. Alle Kriterien prüft der Absender offline gegen den gemeldeten Commit oder am Repo; die Abnahme im Cluster steht unter Integration.

| ID | Anforderung | Abnahmekriterium (von außen prüfbar) | Priorität | Hängt ab von |
|---|---|---|---|---|
| AF-01 | Das Chart auf `master` ergibt mit den Beispielwerten aus dem Kontrakt eine bring-hermes-Instanz, die der heutigen aus `deploy/` gleichwertig ist. | `helm template bring-hermes <chart-pfad> -n bring-mcp -f values.yaml` (Beispielwerte unten) läuft fehlerfrei. Das Ergebnis erfüllt jeden Punkt von „Gleichwertig“ im Kontrakt, und jede Abweichung des Pod-Templates von `kubectl kustomize deploy/` @`0b010da` ist dort erklärt. Das Standard-Image des Charts ist gleich dem Image in `deploy/kustomization.yaml` am gemeldeten Commit (heute `ghcr.io/scramb/bring--mcp:0.4.0`). | Muss | — |
| AF-02 | Das Chart erzeugt standardmäßig keine Secrets, sondern verweist auf Secrets mit einstellbarem Namen. | `helm template` mit den Beispielwerten und mit den Chart-Standardwerten (plus `--set config.publicUrl=https://example.test`) erzeugt kein Objekt `kind: Secret`. Ein Secret aus Werten entsteht nur, wenn ein ausdrücklicher Schalter eingeschaltet ist. Heute muss das Secret in `existingSecret` auch `DATABASE_URL` enthalten, künftig nicht mehr. Für andere Nutzer des Charts ist das ein Bruch: Die Chart-Version in `Chart.yaml` steigt mindestens in der Minor-Stelle, und die Chart-README beschreibt die Änderung. | Muss | AF-01 |
| AF-03 | Chart und Standardwerte enthalten nichts, das nur für tethys gilt. | `grep -r -n -i -E 'tethys\|kubehut\|meininger\.(cloud\|internal)' <chart-pfad>` findet nichts. `helm template x <chart-pfad> --set config.publicUrl=https://example.test` läuft fehlerfrei und erzeugt kein Objekt `Namespace`, kein Objekt einer `*.toolkit.fluxcd.io`-API, keinen Hostnamen, keine `storageClass` und keine `HTTPRoute` mit voreingestelltem Gateway (heute ist eines voreingestellt). Ist die Route standardmäßig an, scheitert das Rendern ohne `parentRefs` mit einer Meldung. | Muss | AF-01 |
| AF-04 | Unbekannte oder falsch geschriebene Werte lassen das Rendern scheitern, statt still ignoriert zu werden. | Mit den Beispielwerten plus einem Tippfehler-Schlüssel (z. B. `config.publicURL`) scheitert `helm template` mit einer Meldung, die den Schlüssel nennt. Grund für Muss: tethys rollt jeden Commit aus, ein still ignorierter Wert ginge sofort in Betrieb. | Muss | AF-01 |
| AF-05 | Ein Release von bring-hermes ist ein Commit im App-Repo, der erst nach dem Image kommt. | Beim ersten Release nach dem Umstieg ändert der Commit, der den Tag hebt, eine Datei unter dem Chart-Pfad. Er liegt zeitlich nach dem grünen Lauf des Release-Workflows, der das Image gebaut hat. `workloads/bring-mcp/` in tethys-cluster bleibt dabei ohne Commit. | Soll | AF-01, Integration |
| AF-06 | Die Werte-Schnittstelle des Charts ist dokumentiert, einschließlich der Objektarten, die das Chart erzeugt. | `values.yaml` des Charts oder die Chart-README beschreibt jeden Wert aus dem Beispiel unten. Sie nennt die Objektarten, die mit den Beispielwerten entstehen. `helm lint <chart-pfad> -f values.yaml` mit den Beispielwerten ist grün. | Soll | AF-01 |
| AF-07 | Nach dem Umstieg enthält das App-Repo keine Auslieferung und keinen Text mehr, der nur für tethys gilt. | Nach dem Issue „HO-06: switched“ (siehe Integration) fehlt `deploy/` auf `master`, und `git grep -n -i -E 'tethys\|kubehut\|meininger\.(cloud\|internal)' -- ':!docs/handover' ':!docs/readmes/*/handover'` findet nichts, auch nicht in `README.md`. Der Nachname Meininger in Copyright oder Autorenangaben trifft das Muster nicht. | Soll | Integration |

## Schnittstellen-Kontrakt

- **Richtung und Auslöser:** Flux auf tethys holt das Chart über eine `GitRepository` aus `https://github.com/scramb/bring--mcp`, Branch `master`. Das Repo ist öffentlich, es gibt keinen Schlüssel. Die Quelle ist auf den Chart-Pfad beschränkt; nur ein Commit, der darunter etwas ändert, löst ein neues Rendern aus. Das `HelmRelease` rendert das Chart dann mit `workloads/bring-mcp/values.yaml` aus tethys-cluster, auch ohne neue Chart-Version. Den Chart-Pfad wählt der Empfänger und nennt ihn im Signal (heute `helm/bring-hermes`). Das App-Repo pusht nichts in den Cluster.
- **Signale:**
  - Empfänger → tethys: Issue in `scramb/tethys-cluster` mit dem Titel „HO-06: chart ready“. Es nennt den Commit-SHA auf `master`, den Chart-Pfad und die erfüllten AFs.
  - tethys → Empfänger: Issue in `scramb/bring--mcp` mit dem Titel „HO-06: switched“, sobald tethys aus dem Chart läuft. Es gibt AF-07 frei.
- **Datenform:** Die Schnittstelle sind die Werte des Charts. Das Image-Tag setzt tethys nicht, es kommt aus dem Chart. Beispiel für `workloads/bring-mcp/values.yaml`. **Verbindlich sind nur die Werte rechts.** Die Schlüsselstruktur ist ein Vorschlag, der dem heutigen Chart folgt (`database` ist neu). Den endgültigen Namen jedes Schlüssels legt der Empfänger mit AF-01 fest.

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

- **Gleichwertig** heißt, abschließend:
  - Deployment, Service und `HTTPRoute` heißen `bring-hermes`, eine Replik.
  - Der Pod trägt `app.kubernetes.io/name: bring-hermes` und die zusätzlichen Labels aus den Werten. Die Selektor-Labels darf das Chart behalten, wie es sie heute hat (`name` und `instance`).
  - Container-Port `8080` (Name `http`). Welchen Port der Service anbietet, entscheidet das Chart.
  - Umgebung: `PUBLIC_URL` und `LOG_LEVEL` aus den Werten, `PORT=8080`, `MCP_PATH=/mcp`, `MCP_JSON_RESPONSE=true`. `TOKEN_ENCRYPTION_KEY` kommt aus dem Secret in `existingSecret`. `DATABASE_URL` kommt aus dem Datenbank-Secret, dessen Name ein Wert ist. CloudNativePG legt dort die Schlüssel `username`, `password`, `host`, `port`, `dbname`, `uri` und `jdbc-uri` ab; das Chart darf `uri` verwenden, der Schlüssel ist ein Wert oder dokumentiert. Ob die Variablen inline oder über eine `ConfigMap` kommen, ist frei.
  - Probes: Liveness `GET /healthz`, Readiness `GET /readyz` (meldet `503`, solange die Datenbank nicht erreichbar ist), Zeiten wie in `deploy/deployment.yaml`.
  - `securityContext`: `runAsNonRoot`, Benutzer und Gruppe `10001`, `fsGroup` `10001`, `seccompProfile` `RuntimeDefault`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, alle Capabilities entfernt. Ein `emptyDir` unter `/tmp`, weil das Dateisystem nur lesbar ist.
  - Kein ServiceAccount-Token im Pod.
  - `resources` setzt tethys über die Werte.
- **Was tethys bereitstellt und das Chart nur beim Namen nennt:**
  - den Namespace `bring-mcp`,
  - das Secret `bring-hermes-credentials` mit dem Schlüssel `TOKEN_ENCRYPTION_KEY`. Heute liegt dafür `deploy/credentials.yaml` im App-Repo. Künftig entsteht es aus einem `ExternalSecret` in tethys-cluster, das den Wert aus dem Secret-Speicher von tethys holt. Inhalt ist ein Fernet-Schlüssel. Ändert er sich, muss sich jeder Nutzer einmal neu anmelden.
  - den CNPG-`Cluster` `bring-mcp-db` mit dem Secret `bring-mcp-db-app`. Das Chart bringt keine Datenbank mit.
  - die Isolation: eine Istio-`AuthorizationPolicy`, die auf das Pod-Label `app.kubernetes.io/name: bring-hermes` zielt und nur das öffentliche Gateway zulässt,
  - das Gateway `<gateway-namespace>/<gateway>` mit Listener und TLS-Zertifikat.
- **Objektarten:** Das Chart nennt in AF-06 die Arten, die es mit den Beispielwerten erzeugt. Heute sind das Deployment, Service, `HTTPRoute`, `ConfigMap` und `ServiceAccount`. tethys gibt dem helm-controller genau diese Arten im Namespace `bring-mcp` frei.
- **Fehlerverhalten:**
  - Lässt sich das Chart mit den tethys-Werten nicht rendern, bleibt der letzte installierte Stand laufen, und das `HelmRelease` zeigt `Ready False` mit der Ursache.
  - Fehlt ein Secret oder ein Schlüssel darin, bleibt der neue Pod in `CreateContainerConfigError`. Ist die Datenbank nicht erreichbar, meldet `/readyz` `503`. tethys installiert mit Warten auf Bereitschaft. Ein solches Upgrade schlägt deshalb fehl, wird zurückgerollt und nicht wiederholt. Das Chart muss dafür die Readiness-Probe auf `/readyz` behalten.
  - Ist das App-Repo nicht erreichbar, läuft der letzte Stand weiter.
- **Kompatibilität:**
  - Ein Bruch der tethys-Werte ist ein umbenannter oder entfernter Schlüssel, ein neuer Pflicht-Schlüssel in einem Secret, ein neuer Port oder eine neue Verbindung nach außen. Er kommt erst auf `master`, wenn tethys in einem Issue in `scramb/tethys-cluster` bestätigt hat, dass die neuen Werte bereitliegen. Die Chart-Version in `Chart.yaml` steigt dabei.
  - Neue Werte haben Standardwerte, mit denen die Beispielwerte unverändert weiter rendern.
  - Eine neue Objektart ist ebenfalls ein Bruch, denn ohne Freigabe in tethys scheitert sie mit `forbidden`.
- **Verbindliche Fassung:** Die Werte-Schnittstelle des Charts (`values.yaml`, `values.schema.json`) im App-Repo. tethys-cluster hält nur die eigenen Werte in `workloads/bring-mcp/values.yaml`. Bis AF-01 geliefert ist, gilt dieser Abschnitt.

## Nicht-Scope

- Namespace, `ExternalSecret`, Datenbank, Isolation, `GitRepository`, `HelmRelease` und die Rechte des helm-controllers. Das alles baut tethys-cluster.
- Die Übernahme der heutigen Objekte. Deployment, Service und `HTTPRoute` legt tethys beim Umstieg neu an (siehe Integration).
- Veröffentlichung des Charts als OCI-Artefakt in GHCR. Darf bleiben, tethys nutzt sie nicht.
- Automatisches Nachziehen neuer Image-Tags, mehrere Replikas, Backup der Datenbank.
- Änderungen am Verhalten der App (OAuth, Login, MCP-Werkzeuge).

## Randbedingungen

Alle aus ADR 0020 (Eigentümer, 2026-10-18), nicht verhandelbar:
- Das Chart liegt im App-Repo. Flux holt es über eine `GitRepository` auf das App-Repo, Branch `master`, und installiert es mit einem `HelmRelease`.
- Das Chart enthält keinen Hostnamen, kein Gateway und keine `parentRefs` darauf, keine StorageClass, kein Namespace-Objekt, keine Flux-Objekte und keine Namen oder Domains des Betreibers (Suchmuster in AF-03).
- Das Standard-Image-Tag steht im Chart. Ein Release ist ein Commit im App-Repo, die tethys-Werte setzen kein Tag. Der Tag steigt erst, wenn das Image in der Registry liegt.
- Secrets stellt tethys bereit, das Chart verweist auf sie beim Namen. Secrets aus Werten zu erzeugen ist nur als ausdrücklich eingeschaltete Option erlaubt (Standard aus); tethys nutzt sie nie.
- Datenbanken gehören tethys. Das Chart bringt keine Datenbank mit und liest die Verbindung aus einem Secret, dessen Name ein Wert ist.
- Pods nehmen zusätzliche Labels aus den Werten an, und `app.kubernetes.io/name: bring-hermes` bleibt stabil, denn darauf zielt die Isolation.
- Das Chart erzeugt keine `AuthorizationPolicy`, `Role`, `RoleBinding` oder `Namespace`. Istio verknüpft ALLOW-Policies mit ODER, eine zusätzliche Policy könnte die Isolation aushebeln.
- Brüche der tethys-Werte warten auf die Bestätigung von tethys (siehe Kompatibilität).
- Der Umstieg darf Deployment, Service und `HTTPRoute` einmal neu anlegen (rund eine Minute Ausfall). Datenbank und Volumes bleiben unberührt.

## Annahmen

| ID | Annahme | Grundlage | Wenn falsch |
|---|---|---|---|
| A-01 | Das vorhandene Chart `helm/bring-hermes` auf `master` ist ein brauchbarer Ausgangspunkt. Es kann `existingSecret`, `podLabels`, `fullnameOverride`, `httpRoute.*` und `extraEnv`. | Inhalt von `master` @`0b010da` | Mehr Arbeit für AF-01, sonst nichts |
| A-02 | Die App braucht nur `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `PUBLIC_URL` und die optionalen Werte aus `.env.example`. | `deploy/deployment.yaml`, `.env.example` | Weitere Werte kommen in den Kontrakt, als angekündigter Bruch |
| A-03 | Die App kann den `uri` von CloudNativePG unverändert als `DATABASE_URL` verwenden (Schema `postgresql://`, ohne `sslmode`). Heute setzt `deploy/` `postgres://…?sslmode=require`. | Aufbau des `-app`-Secrets von CloudNativePG | Das Chart setzt die URL aus `username`, `password`, `host`, `port` und `dbname` zusammen; AF-01 bleibt, nur der Weg ändert sich |

## Offene Entscheidungen

Keine. Wo die Datenbank liegt und ob der Umstieg Objekte neu anlegen darf, hat der Eigentümer am 2026-10-18 entschieden (siehe Randbedingungen). Wie das Chart die Anforderungen erfüllt, entscheidet der Empfänger.

## Integration

Umstellungsschritt in tethys-cluster: [`scramb/tethys-cluster#2`](https://github.com/scramb/tethys-cluster/issues/2).

Der Umstieg in tethys-cluster beginnt mit dem Issue „HO-06: chart ready“ und läuft auf dem neu aufgebauten Cluster. Bis dahin läuft bring-hermes unverändert aus `deploy/`, und das App-Repo ändert dort nur noch das Tag.

Der Umstiegsschritt in tethys-cluster legt unter `workloads/bring-mcp/` an:
- Namespace, Isolation und das `ExternalSecret` `bring-hermes-credentials`,
- den `Cluster` `bring-mcp-db`, aus dem Dump wiederhergestellt,
- eine `GitRepository`, beschränkt auf den Chart-Pfad,
- ein `HelmRelease` mit `releaseName: bring-hermes`, eigener beschränkter ServiceAccount, Rendern bei jeder Revision, Warten auf Bereitschaft und Rollback ohne Wiederholung,
- `values.yaml` nach dem Kontrakt.

Die bisherige Flux-`Kustomization` für `deploy/` entfernt tethys, bevor das `HelmRelease` installiert. Damit verschwinden die alten Objekte, und das Chart legt sie neu an. Der Empfänger muss dafür nichts tun.

tethys nimmt dabei von außen ab:
- AF-01 bis AF-04 offline gegen den gemeldeten SHA,
- live: `HelmRelease` `Ready True`; `curl -s https://bring.example.com/.well-known/oauth-authorization-server` liefert `issuer` `https://bring.example.com`; `curl -s -o /dev/null -w '%{http_code}' https://bring.example.com/mcp` liefert `401`; die Zahl der Zeilen in `accounts` entspricht dem Dump.

Danach meldet tethys mit „HO-06: switched“, und der Empfänger erledigt AF-07. tethys sagt zu, dass ein Commit unter dem Chart-Pfad spätestens 15 Minuten später läuft. Beim ersten Release danach prüft tethys das zusammen mit AF-05.

## Änderungen

| Version | Datum | Was |
|---|---|---|
| 1 | 2026-10-18 | Entwurf; übergeben |
