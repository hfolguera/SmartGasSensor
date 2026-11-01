from pathlib import Path

import yaml


def test_all_yaml_parses():
    for path in [*Path("k8s").rglob("*.yaml"), *Path("argocd").glob("*.yaml"), Path(".github/workflows/build.yaml")]:
        assert yaml.safe_load(path.read_text()) is not None, str(path)


def test_single_writer_and_private_image_pull():
    deployment = yaml.safe_load(Path("k8s/base/deployment.yaml").read_text())
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    pod = deployment["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "ghcr-pull"}]
    assert pod["automountServiceAccountToken"] is False


def test_no_placeholder_secret_deployed():
    resources = yaml.safe_load(Path("k8s/base/kustomization.yaml").read_text())["resources"]
    assert not any("secret" in name for name in resources)
    pvc = yaml.safe_load(Path("k8s/base/pvc.yaml").read_text())
    assert "Prune=false" in pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"]
