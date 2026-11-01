# Validació local

Validat l'1 de novembre de 2026.

- Python 3.12, dependències directes i transitives fixades als requirements.
- 26 proves automatitzades correctes; ruff sense errors.
- Proves de validació de telemetria, deduplicació sense refrescar antiguitat, ACK després de commit, fallades de disc, persistència, calibratge, API, autenticació, simulació i manifests.
- Promoció GitOps provada contra un repositori bare temporal: crea `gitops`, fixa digest i actualitza la branca sense modificar `main`.
- Workflow verificat amb actionlint 1.7.12.
- Manifests renderitzats amb kubectl/kustomize v5.8.1, sense accedir al clúster.
- Dockerfile construït amb Podman, arquitectura ARM64. Contenidor arrencat com UID 10001, sense capabilities i amb filesystem de només lectura; endpoint `/health/ready` correcte.
- Subscripció real al broker de la LAN: recepció de mostres del `NodeMCU_ESP32` a `home/gas/telemetry`; dades desades i accessibles per l'API.
- Dashboard revisat al navegador amb dades reals: connexió MQTT, distància, qualitat, històric i taula; cap error de consola observat.

La captura `docs/dashboard.png` correspon a una prova amb el sensor de sobretaula i encara sense calibratge del dipòsit.

La compilació multi-arquitectura AMD64/ARM64, la publicació a GHCR i l'execució real de GitHub Actions queden pendents de pujar el repositori i activar el workflow. ArgoCD/Kubernetes no s'han modificat. No s'han publicat credencials al repositori.

La suite local pot mostrar un avís de deprecació de Starlette sobre httpx al client de proves. No afecta les proves ni el client HTTP de producció.

## Notificacions i barra de risc

- 50 proves correctes, incloent llindars, diversos salts en una lectura, histèresi, prioritats 20%/10%/0%, persistència, migració SQLite, simulacions excloses i reintents HTTP.
- Enviament HTTP verificat amb un servidor ntfy simulat local; no s'han enviat missatges de prova al topic de casa.
- Salut de `https://ntfy.calfolguera.freemyip.com/v1/health` correcta des del Mac i del contenidor. La confirmació d'un enviament real es registrarà quan es travessi el primer llindar després de la lectura inicial de referència.
- Entorn local activat amb topic `home-notifications`, web final `https://smartgassensor.calfolguera.freemyip.com` i histèresi de 5 punts.
- Barra de risc i estat d'avisos revisats al navegador amb lectures reals.
- Backup previ a la migració: `/data/smartgas-before-ntfy-20261101.db`, al volum persistent local.
