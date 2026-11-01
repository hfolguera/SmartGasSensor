#!/usr/bin/env python3
"""Crea secrets al clúster via stdin; no posa contrasenyes en arguments ni fitxers Git."""
import base64
import getpass
import json
import subprocess


def apply(resource):
    subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(resource), text=True, check=True)


if __name__ == "__main__":
    context = subprocess.check_output(["kubectl", "config", "current-context"], text=True).strip()
    print("Context Kubernetes:", context)
    print("Es crearan/actualitzaran els secrets smartgas-secrets i ghcr-pull al namespace smartgas.")
    if input("Escriu el nom del context per continuar: ").strip() != context:
        raise SystemExit("Cancel·lat")
    mqtt_user = input("Usuari MQTT [mosquitto]: ").strip() or "mosquitto"
    mqtt_password = getpass.getpass("Contrasenya MQTT: ")
    api_token = getpass.getpass("Token API (buit = sense autenticació pròpia): ")
    ghcr_user = input("Usuari GHCR [hfolguera]: ").strip() or "hfolguera"
    ghcr_token = getpass.getpass("Token GHCR amb read:packages: ")
    if not ghcr_token:
        raise SystemExit("Cal un token de lectura per la imatge privada")
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "smartgas"}})
    apply({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
           "metadata": {"name": "smartgas-secrets", "namespace": "smartgas"},
           "stringData": {"MQTT_USERNAME": mqtt_user, "MQTT_PASSWORD": mqtt_password, "API_TOKEN": api_token}})
    auth = base64.b64encode(f"{ghcr_user}:{ghcr_token}".encode()).decode()
    docker_config = json.dumps({"auths": {"ghcr.io": {"auth": auth}}})
    apply({"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/dockerconfigjson",
           "metadata": {"name": "ghcr-pull", "namespace": "smartgas"},
           "data": {".dockerconfigjson": base64.b64encode(docker_config.encode()).decode()}})
