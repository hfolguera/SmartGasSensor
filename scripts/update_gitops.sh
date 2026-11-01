#!/usr/bin/env bash
set -euo pipefail
# Només s'executa en GitHub Actions, des d'un checkout net i efímer.
: "${GITHUB_ACTIONS:?Només per al runner de GitHub Actions}"
: "${GITHUB_SHA:?}" "${IMAGE_NAME:?}" "${IMAGE_DIGEST:?}"
if [[ ! "$IMAGE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  echo "Digest d'imatge invàlid" >&2; exit 1
fi
# Una compilació antiga que acaba més tard no ha de substituir la versió nova.
main_head=$(git ls-remote origin refs/heads/main | cut -f1)
if [[ "$main_head" != "$GITHUB_SHA" ]]; then
  echo "main ja té commits posteriors; aquesta imatge es publica però no es promociona."
  exit 0
fi
snapshot=$(mktemp -d)
trap 'rm -rf "$snapshot"' EXIT
cp -R k8s "$snapshot/k8s"
export SNAPSHOT="$snapshot"
python - <<'PY'
import os
from pathlib import Path
import yaml
path = Path(os.environ['SNAPSHOT']) / 'k8s/overlays/production/kustomization.yaml'
data = yaml.safe_load(path.read_text())
data['images'] = [{'name': 'ghcr.io/hfolguera/smartgassensor',
                   'newName': os.environ['IMAGE_NAME'], 'digest': os.environ['IMAGE_DIGEST']}]
path.write_text(yaml.safe_dump(data, sort_keys=False))
PY
if git ls-remote --exit-code --heads origin gitops >/dev/null 2>&1; then
  git fetch origin gitops
  git checkout -B gitops FETCH_HEAD
else
  git checkout --orphan gitops
fi
# La branca gitops només conté manifests; tota modificació es fa a main.
git rm -rf --ignore-unmatch . >/dev/null
cp -R "$snapshot/k8s" ./k8s
git add k8s
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
if git diff --cached --quiet; then
  echo "Sense canvis GitOps"
  exit 0
fi
git commit -m "deploy: ${GITHUB_SHA} (${IMAGE_DIGEST})"
git push origin HEAD:gitops
