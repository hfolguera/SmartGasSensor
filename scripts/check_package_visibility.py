#!/usr/bin/env python3
"""Fail closed si una imatge GHCR existent no és privada. No modifica visibilitat."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

owner = os.environ["GITHUB_REPOSITORY_OWNER"]
image = os.environ["IMAGE_NAME"].split("/")[-1]
owner_type = os.environ.get("OWNER_TYPE", "User")
base = "orgs" if owner_type == "Organization" else "users"
url = f"https://api.github.com/{base}/{owner}/packages/container/{image}"
request = urllib.request.Request(url, headers={
    "Authorization": "Bearer " + os.environ["GH_TOKEN"],
    "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
})
require_exists = "--require-exists" in sys.argv
for attempt in range(6 if require_exists else 1):
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            package = json.load(response)
        if package.get("visibility") != "private":
            raise SystemExit("El paquet GHCR existent no és privat. No es publicarà cap imatge nova.")
        print("Visibilitat GHCR verificada: private")
        break
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise SystemExit(f"No es pot verificar la visibilitat GHCR (HTTP {error.code}).") from None
        if not require_exists:
            print("Paquet nou: GHCR el crearà privat per defecte; es verificarà després del push.")
            break
        if attempt == 5:
            raise SystemExit("No s'ha pogut verificar el paquet acabat de publicar.") from None
        time.sleep(5)
