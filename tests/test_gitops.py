import os
import shutil
import subprocess
from pathlib import Path

import yaml


def test_promote_digest_to_gitops_branch_without_touching_main(tmp_path):
    script = Path("scripts/update_gitops.sh").resolve()
    origin = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    work.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(work), *args], text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("remote", "add", "origin", str(origin))
    shutil.copytree("k8s", work / "k8s")
    (work / "app.txt").write_text("application source")
    git("add", ".")
    git("commit", "-m", "initial")
    git("push", "origin", "main")
    for iteration in range(2):
        git("checkout", "main")
        if iteration:
            (work / "app.txt").write_text("second source revision")
            git("add", ".")
            git("commit", "-m", "second")
            git("push", "origin", "main")
        sha = git("rev-parse", "HEAD")
        digest = "sha256:" + str(iteration + 1) * 64
        environment = {**os.environ, "GITHUB_ACTIONS": "true", "GITHUB_SHA": sha,
                       "IMAGE_NAME": "ghcr.io/hfolguera/smartgassensor", "IMAGE_DIGEST": digest}
        subprocess.run(["bash", str(script)], cwd=work, env=environment, check=True, capture_output=True)
        content = git("show", "gitops:k8s/overlays/production/kustomization.yaml")
        assert yaml.safe_load(content)["images"][0]["digest"] == digest
        assert git("rev-parse", "main") == sha
        assert git("ls-tree", "--name-only", "gitops") == "k8s"
