# SmartGasSensor

Monitor local del dipòsit de gasoil: rep el JSON MQTT del NodeMCU ESP8266, el desa i ofereix web, API REST i històric. Funciona independentment de Home Assistant.

```text
ESP8266 → Mosquitto → SmartGasSensor (MQTT + FastAPI) → SQLite/PVC
                                  ├→ navegador
                                  └→ API / Home Assistant
                                  └→ Prometheus
```

El firmware Arduino és a [`firmware/`](firmware/README.md), amb credencials locals separades del codi publicable.

## Què inclou

- Dashboard en català, adaptable a mòbil, amb gràfic i últimes lectures.
- Distància, dispersió, mostres vàlides, Wi-Fi, firmware, temps despert i bateria si el dispositiu l'envia.
- Volum, percentatge i reserva quan es configura la geometria rectangular real.
- Diferenciació entre dades antigues, error de sensor, error de calibratge i simulació.
- Última lectura vàlida separada de l'últim intent. Els errors no es representen com a zero litres.
- Validació del contracte, deduplicació, reconnexió MQTT i sessió persistent QoS 1.
- SQLite WAL amb commit abans de l'ACK MQTT; volum persistent i una sola instància.
- Docker sense root, Compose, manifests Kubernetes/Kustomize i construcció d’imatges amb GitHub Actions.

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

`.env` està exclòs de Git i del context Docker. Si ja s'ha preparat un `.env` local, no el sobreescriguis amb la plantilla. Per al desenvolupament local fes servir `MQTT_CLIENT_ID=smartgas-local`, diferent de `smartgassensor-production`. Dues aplicacions amb el mateix client ID s'expulsen mútuament del broker.

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

`config/tanks.json`, `config/tanks.example.json` i `k8s/tanks.json` configuren el dipòsit instal·lat, identificat pel firmware com `NodeMCU_ESP32`:

- Amplada aproximada: **110 cm**; fons: **80 cm**; alçada: **160 cm**.
- Capacitat nominal: **1.000 L**; reserva: **150 L**.
- Distància del sensor al nivell màxim: **14 cm**.
- Distància estimada al fons: **174 cm**, assumint que els 160 cm corresponen a l’alçada útil.

Les dimensions donen **1.408 L geomètrics**, que no coincideixen amb els 1.000 L nominals. Per això s’utilitza `calibration_mode=geometry_nominal`: el volum s’escala linealment entre 0 i 1.000 L, en lloc de considerar que el dipòsit pot contenir 1.408 L. Les dimensions són aproximades i cal validar la distància al buit i el calibratge amb un nivell conegut. No és una taula volumètrica del fabricant.

| Camp | Significat |
|---|---|
| `length_mm`, `width_mm`, `height_mm` | Dimensions aproximades; 1100 × 800 × 1600 mm |
| `sensor_to_bottom_mm` | Distància estimada del sensor al fons: 1740 mm |
| `nominal_capacity_liters` | Capacitat utilitzada per als litres: 1000 L |
| `reserve_liters` | Llindar de reserva: 150 L |

`alçada = 1740 − distance_mm`. `percentatge = 100 × alçada / 1600`. `litres = 1000 × alçada / 1600`.

| Distància sensor-superfície | Nivell | Litres estimats |
|---|---:|---:|
| 14 cm | 100% | 1000 L |
| 54 cm | 75% | 750 L |
| 94 cm | 50% | 500 L |
| 134 cm | 25% | 250 L |
| 174 cm | 0% | 0 L |

Si s’omet `nominal_capacity_liters`, s’utilitza el volum geomètric rectangular. També es manté el calibratge antic de dos punts (`full_distance_mm`, `empty_distance_mm` i capacitat nominal, sense dimensions) per compatibilitat, però no és el mode del dipòsit instal·lat. La web mostra els extrems configurats, sense distàncies fixes del prototip.

Si l'alçada queda fora de `[0, height_mm]`, no es força a zero/ple: es marca error de calibratge. Les lectures originals es conserven i els càlculs es refan en consultar: canviar dimensions permet revisar l'històric. Reinicia l'aplicació després de canviar la configuració local. A Kubernetes edita `k8s/tanks.json` a `main`: el canvi del ConfigMap genera una nova versió i recrea el Pod.

`received_at` és l'hora UTC de recepció al servidor, no una hora de mesura enviada pel sensor. La web la mostra en hora local. Els missatges acumulats al broker durant una caiguda poden correspondre a mesures anteriors; sense un `measured_at` fiable del firmware no se'n pot saber l'antiguitat física exacta. El timeout de dades s'aplica a la recepció. Amb `STALE_AFTER_SECONDS=10800`, es marquen antigues després de tres hores sense cap nova mostra; això dona marge a les lectures horàries i als petits retards de connexió. Els duplicats no refresquen aquesta hora.

Retenció per defecte: 730 dies. Neteja com a màxim una vegada per hora quan entren dades; conserva sempre l'última mostra de cada dispositiu. Els registres antics fora de retenció no són un arxiu indefinit.

## API

| Endpoint | Resposta |
|---|---|
| `GET /api/v1/status` | Connexió/subscripció MQTT, comptadors i configuració temporal |
| `GET /api/v1/devices` | Últim estat de cada dispositiu i `last_valid` |
| `GET /api/v1/devices/{device_id}/latest` | Estat d'un dispositiu |
| `GET /api/v1/devices/{device_id}/history?hours=24&limit=500` | Històric, ordre cronològic, màxim 5.000 mostres per consulta |
| `GET /api/v1/notifications` | Avisos enviats/pendents, llindars, intents i estat ntfy |
| `GET /api/v1/metrics` | Mètriques en format Prometheus |
| `GET /openapi.json` | Esquema OpenAPI |
| `GET /health/live` | Liveness; falla si hi ha hagut un error de persistència que requereix reinici |
| `GET /health/ready` | Base de dades disponible; una caiguda MQTT no amaga la web/històric |

La consulta històrica retorna les mostres més recents del període, no agregats. `next_before` permet demanar un tram anterior amb el paràmetre `before` (timestamp Unix). La web mostra les últimes 500 i indica si arriba al límit.

Si `API_TOKEN` no és buit, totes les rutes `/api/v1` requereixen `Authorization: Bearer TOKEN`. La web demana aquest token i el conserva a `sessionStorage`. Les rutes de salut i l'estructura de la web no exposen lectures. No hi ha cap ruta HTTP d'escriptura de telemetria. Per accés fora d'una LAN de confiança, configura TLS i autenticació al punt d’accés que configuris o un token API; no publiquis credencials sobre HTTP.

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

## Prometheus

`GET /metrics` i `GET /api/v1/metrics` exposen el mateix contingut en [format Prometheus 0.0.4](https://prometheus.io/docs/instrumenting/exposition_formats/). Si configures `API_TOKEN`, també requereixen `Authorization: Bearer …`.

- Connexió i subscripció MQTT; comptadors de recepcions, duplicats, rebutjos i errors d’emmagatzematge des de l’inici del procés.
- Temps des de l’última lectura real, instant de recepció persistent i estat de l’avís de silenci.
- Avisos retinguts a SQLite per estat (`pending`, `sent`, `cancelled`), com a gauges perquè la retenció pot reduir-los.
- Per `device_id`: antiguitat, dada obsoleta, lectura vàlida, mostres, distància en metres, percentatge, litres, Wi-Fi, bateria en volts i període de mostreig.

No s’exporten simulacions. Els valors desconeguts o les mesures fallides s’ometen; no s’inventen zeros. Un nivell antic pot continuar exposat: comprova sempre `smartgas_device_stale` i `smartgas_device_measurement_valid` abans de generar alertes. No s’utilitzen identificadors de mostra com a etiquetes.

Exemple de configuració de scrape dins del clúster (Service HTTP al port 80):

```yaml
scrape_configs:
  - job_name: smartgassensor
    metrics_path: /metrics
    scrape_interval: 15m
    static_configs:
      - targets: [smartgassensor.smartgassensor.svc.cluster.local:80]
    # Si API_TOKEN està configurat:
    # authorization:
    #   type: Bearer
    #   credentials_file: /etc/prometheus/secrets/smartgassensor-api-token
```

Amb una lectura del sensor cada hora, el scrape de 15 minuts permet recollir el nou valor amb un retard addicional de fins a 15 minuts i comprovar periòdicament que l’aplicació respon. L’interval de scrape no modifica el firmware ni la freqüència de mesura MQTT.

Prometheus utilitza per defecte un lookback de 5 minuts. Amb aquest scrape de 15 minuts, una consulta instantània directa pot quedar sense valor entre scrapes. Per mostrar l’últim valor recollit a Grafana pots utilitzar:

```promql
last_over_time(smartgas_device_volume_liters{device_id="NodeMCU_ESP32"}[30m])
```

Aquesta consulta pot conservar un volum anterior si després hi ha errors del sensor: comprova també `last_over_time(smartgas_device_measurement_valid[30m])` i `last_over_time(smartgas_device_stale[30m])`. El primer ha de ser 1 i el segon 0 per presentar-lo com un nivell vàlid i recent. La finestra de 30 minuts es refereix a la recollida de Prometheus; l’antiguitat de la mesura del sensor s’obté de `smartgas_device_age_seconds`.

L’exemple de target anterior és per a un Prometheus dins del clúster. Si és extern, conserva l’URL accessible que tinguis configurada i modifica només `scrape_interval` al job `smartgassensor`, sense canviar l’interval global ni els altres jobs. Després valida i recarrega la configuració de Prometheus. [Comportament del lookback](https://prometheus.io/docs/prometheus/latest/querying/basics/#staleness).

## Notificacions ntfy i autonomia de combustible

L’aplicació avisa quan una **nova lectura real i vàlida** travessa cap avall els llindars **90%, 80%, …, 20%, 10% i 0%**. Si una lectura salta diversos llindars, genera un avís per cadascun. No es generen avisos amb simulacions, errors del sensor ni lectures sense calibratge vàlid.

- Del 90% al 30%: prioritat ntfy normal (`3`).
- Al 20%: text de reserva baixa i prioritat alta (`4`).
- Al 10% i 0%: text crític i prioritat urgent (`5`). El so final depèn de la configuració del client ntfy.
- La primera lectura després d’habilitar els avisos estableix la referència: no s’envien tots els llindars històrics.
- Un llindar notificat es reactiva després d’un ascens d’almenys **5 punts** sobre seu. Per exemple, després d’avisar al 90%, no es repeteix fins que el nivell arriba al 95% i torna a baixar al 90%.
- En canviar el calibratge s’estableix una nova referència i es cancel·len els avisos pendents del calibratge anterior. Una recuperació de nivell també cancel·la els avisos pendents dels llindars que ja s’han superat amb el marge de reactivació.

També s’envia un avís de prioritat alta (`4`) quan passen **més de 24 hores sense una lectura MQTT real nova**, amb enllaç a la web. El control s’executa en segon pla encara que no arribin missatges. Només s’avisa una vegada per interrupció; una nova lectura reactiva el control i cancel·la l’avís pendent si encara no s’ha entregat. Un error del sensor (`no_echo`, etc.) compta com a recepció; els duplicats, JSON rebutjats i simulacions no reinicien el termini. El rellotge i l’estat persisteixen als reinicis. Sense historial, es compta des del primer inici de la supervisió. Amb `MQTT_ENABLED=false` o `NTFY_ENABLED=false` no es generen aquests avisos. La supervisió cobreix el flux MQTT global; no és una alarma individual per cada sensor.

Configuració local a `.env` i de Kubernetes al ConfigMap:

```dotenv
NTFY_ENABLED=true
NTFY_SERVER_URL=https://ntfy.calfolguera.freemyip.com
NTFY_TOPIC=home-notifications
NTFY_HYSTERESIS_PERCENT=5
PUBLIC_WEB_URL=https://smartgassensor.calfolguera.freemyip.com
```

Si el servidor requereix autenticació, afegeix `NTFY_TOKEN` al `.env` local o al Secret `smartgassensor-secrets`; alternativament `NTFY_USERNAME` i `NTFY_PASSWORD`. No es guarden tokens al repositori ni es retornen per l’API. El script `create_secrets.py` demana un token ntfy opcional; si el deixes buit, permet indicar usuari i contrasenya. Per autenticació bàsica, `NTFY_TOKEN` ha de quedar buit perquè el token té prioritat. Després de modificar el Secret, reinicia el Deployment per carregar les credencials. El client comprova TLS i no segueix redireccions amb credencials.

Els missatges contenen nivell, litres estimats, un text segons urgència, un **enllaç directe** i el botó **Veure evolució** cap a `PUBLIC_WEB_URL`. El calibratge provisional s’indica al missatge. Subscriu el teu client ntfy al servidor indicat i al topic `home-notifications`.

La lectura, l’estat dels llindars i la cua d’avisos es desen en una mateixa transacció SQLite abans de confirmar MQTT. Un fil independent fa els enviaments HTTP: si ntfy falla, la ingestió continua i els avisos es reintenten amb espera creixent, entre 30 segons i una hora. Els pendents sobreviuen als reinicis. Es conserva l’històric d’avisos segons la retenció configurada. Com en qualsevol entrega HTTP sense clau d’idempotència del receptor, una caiguda entre l’acceptació d’ntfy i el registre de la confirmació pot causar una repetició puntual.

La barra **Autonomia de combustible** mostra el combustible restant: vermell (reserva crítica) a l’esquerra i verd (reserva alta) a la dreta, amb marcador i text accessible. És una classificació basada en el nivell, no una predicció de dies d’autonomia. Si la lectura és antiga o correspon a l’últim valor vàlid després d’un error, la web ho indica.

`GET /api/v1/notifications` mostra configuració no sensible, comptadors, últims avisos, intents i errors. `GET /api/v1/status` inclou un resum. Una resposta HTTP 401/403 d’ntfy es mostra com a reintent pendent: cal configurar les credencials abans que els avisos puguin entregar-se.

## Kubernetes (desplegament manual)

Els manifests i el calibratge són directament a `k8s/`, sense subdirectoris. Es manté un únic `kustomization.yaml` per generar els ConfigMaps i seleccionar la imatge.

Revisa la configuració abans de desplegar:

- `k8s/pvc.yaml`: 2 GiB; omet `storageClassName` per utilitzar la classe per defecte. Afegeix-la si cal.
- `k8s/kustomization.yaml`: IP, port, topic, client ID, retenció i llindar d'antiguitat.
- `k8s/tanks.json`: dimensions reals, quan les tinguis.

Renderitza localment, sense desplegar:

```bash
kubectl kustomize k8s
```

Crea dos secrets al namespace `smartgassensor`, **fora de Git**:

- `smartgassensor-secrets`: `MQTT_USERNAME`, `MQTT_PASSWORD`, `API_TOKEN` (pot ser buit) i les credencials ntfy: `NTFY_USERNAME`/`NTFY_PASSWORD` amb `NTFY_TOKEN` buit, o bé `NTFY_TOKEN`.
- `ghcr-pull`: tipus `kubernetes.io/dockerconfigjson`, credencial de lectura GHCR.

El script següent demana credencials sense mostrar-les i les envia a Kubernetes per stdin. Revisa el context seleccionat abans de confirmar:

```bash
python3 scripts/create_secrets.py
```

Si la imatge és privada, `ghcr-pull` necessita una credencial amb permís de lectura del paquet. Si és pública pots eliminar `imagePullSecrets` del Deployment.

Els manifests utilitzen `ghcr.io/hfolguera/smartgassensor:latest` amb `imagePullPolicy: Always`. El workflow publica `latest` des de `main`, a més del tag per commit. Aplica els manifests manualment:

```bash
kubectl apply -k k8s
kubectl -n smartgassensor get pods,pvc,svc
kubectl -n smartgassensor logs deployment/smartgassensor -f
```

La NetworkPolicy, si el clúster en té, ha de permetre DNS i sortida TCP al broker, i entrada cap al Service de l’aplicació. Per MQTT TLS, activa `MQTT_TLS`, ajusta el port i, amb una CA privada, munta el certificat i defineix `MQTT_CA_FILE`; no es desactiva la verificació TLS.

## Backups i actualitzacions

Les lectures i els avisos es desen al PVC `smartgassensor-data`. Conserva aquest volum durant les actualitzacions i configura snapshots/backups al clúster.

Per fer una còpia coherent d'una SQLite local mentre l'aplicació està activa:

```bash
python3 scripts/backup_db.py --source data/smartgas.db --destination backups/smartgas-2026-11-01.db
```

En Docker/Kubernetes, executa l'equivalent dins del contenidor amb Python/SQLite o fes snapshots coherents del volum. No copiïs només el `.db` mentre hi ha escriptures al WAL. Per restaurar, atura l'aplicació, conserva una còpia de l'estat actual i restaura el backup al camí configurat amb permisos UID/GID 10001.

Secrets modificats no reinicien automàticament un Pod: després d'actualitzar-los, fes `kubectl -n smartgassensor rollout restart deployment/smartgassensor`. El canvi del ConfigMap sí que canvia el nom generat i recrea el Pod.

## Desenvolupament i proves

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest -q
node --check app/static/app.js
kubectl kustomize k8s > /tmp/smartgas.yaml
```

Les versions directes són a `requirements.in`/`requirements-dev.in`; els `.txt` fixen també dependències transitives. Per actualitzar-les, utilitza `pip-tools` amb Python 3.12 i torna a executar les proves.

Per enviar una mostra simulada, el script `scripts/publish_sample.py` llegeix les credencials de les variables d'entorn i publica per defecte a `home/gas/demo/telemetry`, amb `device_id=demo-sensor` i `simulated=true`. Per veure-la cal configurar la subscripció de l'aplicació a aquest topic o utilitzar explícitament el topic real amb `--topic home/gas/telemetry`. El dispositiu simulat sempre es distingeix del real.

## Fonts principals

- [Paho Python: sessions, callbacks i ACK manual](https://eclipse.dev/paho/clients/python/docs/)
- [GitHub Actions: publicar imatges](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GHCR: permisos i visibilitat](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
- [Kubernetes: registres privats](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/)

També es controla **cada sensor** per avisar amb prioritat alta (`4`) després de **més de 24 hores sense mesures correctes**, encara que continuïn arribant errors per MQTT. Només una lectura real amb estat `ok`, distància present i nivell calculable amb el calibratge actual reinicia aquest termini. Els duplicats i les simulacions no el reinicien. L’estat persisteix a SQLite; s’envia un avís per incidència i una mesura correcta rearma el control i cancel·la els avisos pendents. En actualitzar, s’utilitza l’última mesura correcta de l’historial; si no n’hi ha, la primera recepció real o l’inici de la supervisió. Sense configuració del dipòsit no es considera que una mesura sigui utilitzable. Aquest avís és independent del de silenci MQTT i pot coincidir-hi.

L’API `/api/v1/status` inclou `valid_measurement_monitors`. Prometheus exposa `smartgas_device_last_valid_timestamp_seconds` (absent fins a la primera mesura correcta), `smartgas_device_without_valid_data_seconds` i `smartgas_device_no_valid_data_alert_active`, amb l’etiqueta `device_id`.
