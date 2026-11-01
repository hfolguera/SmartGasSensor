#!/usr/bin/env python3
"""Build/arrencada local amb Podman; normalitza .env sense posar secrets en arguments."""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def read_env(path):
    values = []
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            raise ValueError("Línia .env invàlida")
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError("Les variables han de ser cadenes d'una línia")
        values.append(f"{key}={value}")
    return "\n".join(values) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--recreate", action="store_true", help="Recrea només el contenidor smartgas; conserva el volum")
    parser.add_argument("--relay", action="store_true", help="Utilitza mqtt_relay.py al Mac, port 28883")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    podman = shutil.which("podman") or "/opt/podman/bin/podman"
    image = "localhost/smartgassensor:local"
    env = read_env(root / ".env")
    if args.build:
        subprocess.run([podman, "build", "--format", "docker", "-t", image, "."], check=True)
    exists = subprocess.run([podman, "container", "exists", "smartgas"], check=False).returncode
    if exists not in (0, 1):
        raise SystemExit("No es pot consultar Podman; comprova que la màquina estigui activa")
    if exists == 0:
        if not args.recreate:
            raise SystemExit("smartgas ja existeix. Utilitza podman start smartgas, o --recreate per aplicar canvis.")
        subprocess.run([podman, "stop", "smartgas"], check=True)
        subprocess.run([podman, "rm", "smartgas"], check=True)
    volume_exists = subprocess.run([podman, "volume", "exists", "smartgas-data"], check=False).returncode
    if volume_exists == 1:
        subprocess.run([podman, "volume", "create", "smartgas-data"], check=True)
    elif volume_exists != 0:
        raise SystemExit("No es pot consultar el volum de dades de Podman")
    with tempfile.NamedTemporaryFile(mode="w", prefix="smartgas-env-", delete=False) as file:
        os.chmod(file.name, 0o600)
        file.write(env)
        filename = file.name
    try:
        command = [podman, "run", "-d", "--name", "smartgas", "--restart", "unless-stopped", "--env-file", filename]
        if args.relay:
            command.extend(["-e", "MQTT_HOST=host.containers.internal", "-e", "MQTT_PORT=28883"])
        command.extend(["--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                        "--tmpfs", "/tmp:rw,size=16m,mode=1777", "-p", "127.0.0.1:8080:8080",
                        "-v", "smartgas-data:/data", "-v", str(root / "config/tanks.json") + ":/config/tanks.json:ro", image])
        subprocess.run(command, check=True)
    finally:
        os.unlink(filename)
    print("Aplicació: http://localhost:8080")
    if args.relay:
        print("Cal mantenir mqtt_relay.py actiu al Mac. La configuració de producció no s'ha modificat.")


if __name__ == "__main__":
    main()
