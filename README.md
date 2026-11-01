# SmartGasSensor

Monitor local del dipòsit de gasoil: rep el JSON MQTT del NodeMCU ESP8266, el desa i ofereix web, API REST i històric. Funciona independentment de Home Assistant.

```text
ESP8266 → Mosquitto → SmartGasSensor (MQTT + FastAPI) → SQLite/PVC
                                  ├→ navegador
                                  └→ API / Home Assistant
main → GitHub Actions → GHCR → branca gitops → ArgoCD → Kubernetes
```

## Què inclou

- Dashboard en català, adaptable a mòbil, amb gràfic i últimes lectures.
- Distància, dispersió, mostres vàlides, Wi-Fi, firmware, temps despert i bateria si el dispositiu l'envia.
- Volum, percentatge i reserva quan es configura la geometria rectangular real.
- Diferenciació entre dades antigues, error de sensor, error de calibratge i simulació.
- Última lectura vàlida separada de l'últim intent. Els errors no es representen com a zero litres.
- Validació del contracte, deduplicació, reconnexió MQTT i sessió persistent QoS 1.
- SQLite WAL amb commit abans de l'ACK MQTT; volum persistent i una sola instància.
- Docker sense root, Compose, Kubernetes/Kustomize, GitHub Actions i Application ArgoCD.

El nom inicial previst és `hfolguera/SmartGasSensor` i la imatge `ghcr.io/hfolguera/smartgassensor`. El repositori remot i la publicació no es creen només per tenir aquests fitxers: cal pujar el projecte a GitHub i executar el workflow.

## Provar localment amb Docker

Requisits: Docker amb Compose, o motor compatible. El broker existent és `192.168.1.216:1883`, topic `home/gas/telemetry`.

```bash
cp .env.example .env
# Edita .env: MQTT_USERNAME, MQTT_PASSWORD i la resta segons el teu entorn.
docker compose up --build -d
docker compose logs -f smartgas
```

Obre [http://localhost:8080](http://localhost:8080). La web consulta dades cada 15 s; el sensor pot continuar publicant cada 60 o 900 s. La primera lectura apareixerà quan el sensor torni a publicar: el firmware fa servir `retain=false`.

`.env` està exclòs de Git i del context Docker. Si ja s'ha preparat un `.env` local, no el sobreescriguis amb la plantilla. Per al desenvolupament local fes servir `MQTT_CLIENT_ID=smartgas-local`, diferent de `smartgas-production`. Dues aplicacions amb el mateix client ID s'expulsen mútuament del broker.

La web es vincula només a `127.0.0.1` en Compose. La base de dades queda al volum `smartgas-data`. No utilitzis `docker compose down -v` si vols conservar les lectures.

### Amb Podman al teu Mac

El projecte inclou un launcher que normalitza les cometes de `.env` abans de passar-lo a Podman (el lector `--env-file` de Podman/Docker no té la mateixa semàntica que Compose). Les credencials s'envien amb un fitxer temporal amb permisos 0600 que s'elimina després d'arrencar.

```bash
python3 scripts/run_local.py --build
/opt/podman/bin/podman logs -f smartgas
```

Si el contenidor ja existeix, `--recreate` aplica la nova imatge/configuració conservant el volum. Per aturar i tornar a arrencar sense canvis:

```bash
/opt/podman/bin/podman stop smartgas
/opt/podman/bin/podman start smartgas
```

#### Si la VM no pot accedir al broker de la LAN

En la prova d'aquest Mac, `192.168.1.216:1883` era accessible des del host però retornava connexió refusada des de la VM de Podman. Per provar localment es pot utilitzar un relay TCP al Mac, escoltant només a loopback. No interpreta MQTT ni registra contrasenyes.

En una terminal, mantén aquest procés actiu:

```bash
python3 scripts/mqtt_relay.py --target-host 192.168.1.216
```

En una altra terminal:

```bash
python3 scripts/run_local.py --relay --recreate
```

Això només substitueix `MQTT_HOST` i `MQTT_PORT` dins del contenidor local per `host.containers.internal:28883`; `.env` i els manifests de producció continuen apuntant al broker original. El relay i la VM de Podman han d'estar actius perquè entrin lectures. Si el relay s'atura, torna'l a iniciar: l'aplicació es reconnecta automàticament.

### Sense Docker

Python 3.12 o posterior; un únic worker d'Uvicorn:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# Exporta les variables MQTT a l'entorn; .env no s'importa automàticament en aquest mode.
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Per iniciar només la web, sense connectar al broker:

```bash
MQTT_ENABLED=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
```

## Calibratge i dades

`config/tanks.json` i el ConfigMap de Kubernetes porten ara el calibratge provisional del prototip: **50 mm = 100%, 250 mm = 0%**, amb escala nominal de **1.000 L**. El percentatge s’interpola linealment i es limita entre 0 i 100%; `calibration_clamped` indica si la distància era fora de l’interval. Aquests litres són orientatius per al prototip, no un calibratge físic del dipòsit real. Per passar a geometria real, substitueix aquesta configuració pel format següent, sense barrejar els dos modes. `config/tanks.example.json` és **només un exemple**, no les dimensions reals. La clau ha de coincidir amb el `device_id` que envia el teu firmware, actualment `NodeMCU_ESP32`.

| Camp | Significat |
|---|---|
| `name` | Nom visible a la web |
| `length_mm`, `width_mm`, `height_mm` | Dimensions interiors del volum rectangular |
| `sensor_to_bottom_mm` | Distància del pla de mesura del sensor al fons |
| `reserve_liters` | Llindar de reserva |

`alçada = sensor_to_bottom_mm − distance_mm`. `litres = llargada × amplada × alçada / 1.000.000`. El percentatge és respecte a la capacitat geomètrica calculada. La distància mínima del sensor i la capacitat útil poden limitar l'ompliment real.

Si l'alçada queda fora de `[0, height_mm]`, no es força a zero/ple: es marca error de calibratge. Les lectures originals es conserven i els càlculs es refan en consultar: canviar dimensions permet revisar l'històric. Reinicia l'aplicació després de canviar la configuració local. A Kubernetes edita `k8s/base/tanks.json` a `main`: el canvi del ConfigMap genera una nova versió i recrea el Pod.

`received_at` és l'hora UTC de recepció al servidor, no una hora de mesura enviada pel sensor. La web la mostra en hora local. Els missatges acumulats al broker durant una caiguda poden correspondre a mesures anteriors; sense un `measured_at` fiable del firmware no se'n pot saber l'antiguitat física exacta. El timeout de dades s'aplica a la recepció. Amb `STALE_AFTER_SECONDS=3600`, es marquen antigues després d'una hora sense cap nova mostra. Els duplicats no refresquen aquesta hora.

Retenció per defecte: 730 dies. Neteja com a màxim una vegada per hora quan entren dades; conserva sempre l'última mostra de cada dispositiu. Els registres antics fora de retenció no són un arxiu indefinit.

## API

| Endpoint | Resposta |
|---|---|
| `GET /api/v1/status` | Connexió/subscripció MQTT, comptadors i configuració temporal |
| `GET /api/v1/devices` | Últim estat de cada dispositiu i `last_valid` |
| `GET /api/v1/devices/{device_id}/latest` | Estat d'un dispositiu |
| `GET /api/v1/devices/{device_id}/history?hours=24&limit=500` | Històric, ordre cronològic, màxim 5.000 mostres per consulta |
| `GET /api/v1/metrics` | Mètriques en format Prometheus |
| `GET /openapi.json` | Esquema OpenAPI |
| `GET /health/live` | Liveness; falla si hi ha hagut un error de persistència que requereix reinici |
| `GET /health/ready` | Base de dades disponible; una caiguda MQTT no amaga la web/històric |

La consulta històrica retorna les mostres més recents del període, no agregats. `next_before` permet demanar un tram anterior amb el paràmetre `before` (timestamp Unix). La web mostra les últimes 500 i indica si arriba al límit.

Si `API_TOKEN` no és buit, totes les rutes `/api/v1` requereixen `Authorization: Bearer TOKEN`. La web demana aquest token i el conserva a `sessionStorage`. Les rutes de salut i l'estructura de la web no exposen lectures. No hi ha cap ruta HTTP d'escriptura de telemetria. Per accés fora d'una LAN de confiança, configura TLS i autenticació a l'Ingress o un token API; no publiquis credencials sobre HTTP.

Exemple sense token:

```bash
curl http://localhost:8080/api/v1/devices/NodeMCU_ESP32/latest
```

La integració REST de Home Assistant és a [docs/home-assistant.yaml](docs/home-assistant.yaml). Inclou disponibilitat basada en antiguitat, errors i simulació. No depèn de tenir HA per fer funcionar aquesta aplicació.

## MQTT i persistència

Client ID estable, `clean_session=false`, MQTT 3.1.1, subscripció QoS 1 i ACK manual després del commit SQLite. Cal que el broker permeti llegir `home/gas/telemetry` amb les credencials configurades i conservi sessions i missatges segons els límits del broker. La subscripció ha d'haver-se establert una primera vegada perquè pugui acumular missatges mentre l'app està aturada.

Un missatge duplicat es descarta per `(device_id, sample_id)` i es confirma. Un JSON invàlid, sobredimensionat o incompatible amb l'esquema es descarta, es compta i es confirma perquè no bloquegi la cua. El missatge de prova `{test: test1}` no és JSON vàlid i tampoc és telemetria: no generarà cap sensor.

Si falla una escriptura SQLite, el missatge no es confirma, la connexió es tanca i la liveness falla per forçar el reinici al clúster. En execució local cal revisar el disc i reiniciar el procés; Docker no reinicia automàticament un contenidor només perquè estigui `unhealthy`. La reconnexió de xarxa ordinària sí que és automàtica.

La base de dades conserva les dades en reiniciar el procés o recrear el contenidor si es manté el volum. SQLite requereix un volum que suporti correctament locking i WAL, preferiblement bloc/local; no assumeixis que qualsevol NFS és adequat. No escalis `replicas` ni workers per damunt d'1. Per HA/múltiples rèpliques cal separar ingestió i migrar a PostgreSQL.

## GitHub i GHCR

1. Crea un repositori GitHub, públic o privat, anomenat `SmartGasSensor` a `hfolguera`.
2. Puja aquest projecte a la branca `main`. No afegeixis secrets amb `git add -f`.
3. A GitHub comprova que Actions està habilitat i que les polítiques permeten `packages: write` i `contents: write` als jobs que ho declaren. La branca `gitops` ha de permetre el push del bot; la protecció de `main` pot mantenir-se.
4. El workflow es dispara a cada push a qualsevol branca excepte `gitops`; les PR només fan proves/build sense publicar. `main` promociona la imatge per ArgoCD. Publica `sha-<commit complet>` per a `linux/amd64` i `linux/arm64`, amb SBOM i provenance.
5. Fa login a GHCR amb `GITHUB_TOKEN`; **no cal guardar cap PAT d'escriptura** com a secret d'Actions.
6. La publicació no està condicionada a la visibilitat del repositori ni del paquet: publica tant si és públic com privat. El workflow no canvia la visibilitat de GHCR; es gestiona a GitHub. Les PR continuen fent build sense publicar. `.env`, credencials i dades locals continuen exclosos de Git i del context de la imatge.

```bash
git remote add origin https://github.com/hfolguera/SmartGasSensor.git
git push -u origin main
```

Si ja hi ha `origin`, revisa'l abans de canviar-lo. Si el repositori té un altre nom, actualitza `argocd/application.yaml` i els noms d'imatge a `k8s` i `scripts/update_gitops.sh`. El workflow deriva el nom GHCR del repositori real i el passa a la promoció.

## Flux GitOps / ArgoCD

El job de publicació de `main`, després de les proves i del push a GHCR:

1. Copia els manifests Kustomize de `main`.
2. Substitueix `bootstrap` pel **digest immutable** `sha256:…` de la imatge acabada de publicar.
3. Crea o actualitza `gitops`, una branca que conté només `k8s/`.
4. ArgoCD observa aquesta branca i sincronitza amb `prune` i `selfHeal`.

No es fa `kubectl apply` des de GitHub i no es comparteixen credencials de clúster amb Actions. No cal ArgoCD Image Updater. El tag `bootstrap` de `main` és només una plantilla i no existeix a GHCR: **espera que la primera Action generi `gitops` abans d'instal·lar l'Application**. No despleguis directament l'overlay de `main` sense posar-hi abans un digest real.

Les promocions estan serialitzades i una compilació d'un commit que ja no és el cap de `main` no es promociona. Tots els pushes igualment poden construir la seva imatge. Els canvis de manifests s'editen a `main`, mai manualment a `gitops`.

### Preparar Kubernetes

Edita abans del primer push:

- `k8s/overlays/production/ingress.yaml`: host `smartgas.example.com`, `ingressClassName: nginx`, TLS segons el clúster.
- `k8s/base/pvc.yaml`: 2 GiB; omet `storageClassName` per utilitzar la classe per defecte. Afegeix-la si cal.
- `k8s/base/kustomization.yaml`: IP, port, topic, client ID, retenció i llindar d'antiguitat.
- `k8s/base/tanks.json`: dimensions reals, quan les tinguis.
- `argocd/application.yaml`: nom del repositori i projecte ArgoCD si no utilitzes `default`.

Renderitza localment, sense desplegar:

```bash
kubectl kustomize k8s/overlays/production
```

Crea dos secrets al namespace `smartgas`, **fora de Git**:

- `smartgas-secrets`: `MQTT_USERNAME`, `MQTT_PASSWORD`, `API_TOKEN` (pot ser buit).
- `ghcr-pull`: tipus `kubernetes.io/dockerconfigjson`, credencial de lectura GHCR.

El script següent demana credencials sense mostrar-les i les envia a Kubernetes per stdin. Revisa el context seleccionat abans de confirmar:

```bash
python3 scripts/create_secrets.py
```

Per `ghcr-pull`, utilitza un PAT clàssic amb `read:packages` d'un compte amb accés al paquet privat; autoritza SSO si l'organització ho requereix. Aquest secret el fa servir Kubernetes, no ArgoCD per llegir Git. Per al repositori Git privat, configura també una credencial de lectura del repositori a ArgoCD (deploy key o token), mitjançant la seva UI o CLI.

Quan existeixi `gitops`, els secrets i la credencial Git d'ArgoCD:

```bash
kubectl apply -f argocd/application.yaml
kubectl -n smartgas get pods,pvc,ingress
kubectl -n smartgas logs deployment/smartgas -f
```

La NetworkPolicy, si el clúster en té, ha de permetre DNS i sortida TCP al broker, i entrada des del controlador Ingress. Per MQTT TLS, activa `MQTT_TLS`, ajusta el port i, amb una CA privada, munta el certificat i defineix `MQTT_CA_FILE`; no es desactiva la verificació TLS.

## Backups i actualitzacions

El PVC porta `Prune=false,Delete=false` perquè ArgoCD no el destrueixi automàticament. Això no és un backup. Configura snapshots/backups del volum al teu clúster.

Per fer una còpia coherent d'una SQLite local mentre l'aplicació està activa:

```bash
python3 scripts/backup_db.py --source data/smartgas.db --destination backups/smartgas-2026-11-01.db
```

En Docker/Kubernetes, executa l'equivalent dins del contenidor amb Python/SQLite o fes snapshots coherents del volum. No copiïs només el `.db` mentre hi ha escriptures al WAL. Per restaurar, atura l'aplicació, conserva una còpia de l'estat actual i restaura el backup al camí configurat amb permisos UID/GID 10001.

Secrets modificats no reinicien automàticament un Pod: després d'actualitzar-los, fes `kubectl -n smartgas rollout restart deployment/smartgas`. El canvi del ConfigMap sí que canvia el nom generat i recrea el Pod.

## Desenvolupament i proves

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest -q
node --check app/static/app.js
kubectl kustomize k8s/overlays/production > /tmp/smartgas.yaml
```

Les versions directes són a `requirements.in`/`requirements-dev.in`; els `.txt` fixen també dependències transitives. Per actualitzar-les, utilitza `pip-tools` amb Python 3.12 i torna a executar les proves.

Per enviar una mostra simulada, el script `scripts/publish_sample.py` llegeix les credencials de les variables d'entorn i publica per defecte a `home/gas/demo/telemetry`, amb `device_id=demo-sensor` i `simulated=true`. Per veure-la cal configurar la subscripció de l'aplicació a aquest topic o utilitzar explícitament el topic real amb `--topic home/gas/telemetry`. El dispositiu simulat sempre es distingeix del real.

## Fonts principals

- [Paho Python: sessions, callbacks i ACK manual](https://eclipse.dev/paho/clients/python/docs/)
- [GitHub Actions: publicar imatges](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GHCR: permisos i visibilitat](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
- [Kubernetes: registres privats](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/)
- [ArgoCD: sincronització automàtica](https://argo-cd.readthedocs.io/en/latest/user-guide/auto_sync/)
