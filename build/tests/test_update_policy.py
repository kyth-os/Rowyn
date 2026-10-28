#!/usr/bin/env python3
"""TDD coverage for Rowyn's image-owned bootc update signature policy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/update-policy.sh"
REPOSITORY = "ghcr.io/kyth-os/rowyn"
KEY_PATH = "/usr/share/rowyn/update.pub"
UNRELATED_GHCR_IMAGE = "docker://ghcr.io/fluxcd/flux-cli:v2.7.1"
UBUNTU_IMAGE = "docker://docker.io/library/ubuntu:latest"
UNSCOPED_IMAGE = "docker://quay.io/podman/hello:latest"


class UpdatePolicyTests(unittest.TestCase):
    def run_helper(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root)],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def run_skopeo_copy(
        self,
        policy: Path,
        registries: Path,
        source: str,
        destination: Path,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("CONTAINERS_POLICY_JSON", None)
        return subprocess.run(
            [
                "skopeo",
                "--policy",
                str(policy),
                "--registries.d",
                str(registries),
                "copy",
                "--src-no-creds",
                source,
                f"dir:{destination}",
            ],
            text=True,
            capture_output=True,
            env=env,
            timeout=120,
        )

    def assert_runtime_fixture_available(
        self, result: subprocess.CompletedProcess[str], image: str
    ) -> None:
        if result.returncode == 0:
            return
        stderr = result.stderr.lower()
        if "rejected by policy" in stderr or "source image rejected" in stderr:
            self.fail(f"{image} was rejected by policy: {result.stderr}")
        unavailable = (
            "connection refused",
            "connection reset",
            "dial tcp",
            "forbidden",
            "i/o timeout",
            "manifest unknown",
            "no such host",
            "proxyconnect",
            "temporary failure",
            "tls handshake timeout",
            "too many requests",
            "unauthorized",
        )
        if any(marker in stderr for marker in unavailable):
            self.skipTest(f"registry fixture unavailable for {image}: {result.stderr.strip()}")
        self.fail(f"unexpected skopeo failure for {image}: {result.stderr}")

    def install_fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="rowyn-update-policy-")
        root = Path(temporary.name)
        result = self.run_helper(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = root / "etc/containers/policy.json"
        registries = root / "etc/containers/registries.d"
        return temporary, root, policy, registries

    def test_installs_image_owned_fail_closed_policy_without_root_or_var_writes(self):
        temporary, root, policy_path, _ = self.install_fixture()
        with temporary:
            self.assertTrue(policy_path.is_file())
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "var").exists())
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertEqual(policy["default"], [{"type": "reject"}])

            docker = policy["transports"]["docker"]
            self.assertEqual(docker["docker.io"], [{"type": "insecureAcceptAnything"}])
            self.assertEqual(docker["ghcr.io"], [{"type": "insecureAcceptAnything"}])
            self.assertEqual(docker[REPOSITORY], [{
                "type": "sigstoreSigned",
                "keyPath": KEY_PATH,
                "signedIdentity": {
                    "type": "exactRepository",
                    "dockerRepository": REPOSITORY,
                },
            }])

    def test_generates_registry_attachment_configuration_for_exact_repository(self):
        temporary, root, _, registries = self.install_fixture()
        with temporary:
            config_path = registries / "rowyn-update.yaml"
            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                "docker:\n"
                "  ghcr.io/kyth-os/rowyn:\n"
                "    use-sigstore-attachments: true\n",
            )
            self.assertFalse((root / "root").exists())
            self.assertFalse((root / "var").exists())

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-update-policy-idempotent-") as temporary:
            root = Path(temporary)
            first = self.run_helper(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            tracked = [
                root / "etc/containers/policy.json",
                root / "etc/containers/registries.d/rowyn-update.yaml",
            ]
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}

            second = self.run_helper(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, {
                path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked
            })

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_actual_rootless_docker_hub_ubuntu_remains_usable(self):
        if os.geteuid() == 0:
            self.skipTest("rootless fixture must run as a non-root user")
        temporary, root, policy, registries = self.install_fixture()
        with temporary:
            result = self.run_skopeo_copy(policy, registries, UBUNTU_IMAGE, root / "ubuntu")
            self.assert_runtime_fixture_available(result, UBUNTU_IMAGE)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_actual_unrelated_ghcr_is_allowed_but_scoped_rowyn_is_not(self):
        temporary, root, policy, registries = self.install_fixture()
        with temporary:
            unrelated = self.run_skopeo_copy(
                policy, registries, UNRELATED_GHCR_IMAGE, root / "unrelated-ghcr"
            )
            self.assert_runtime_fixture_available(unrelated, UNRELATED_GHCR_IMAGE)
            self.assertEqual(unrelated.returncode, 0, unrelated.stderr)

            scoped = self.run_skopeo_copy(
                policy, registries, "docker://ghcr.io/kyth-os/rowyn:spike", root / "rowyn"
            )
            if scoped.returncode == 0:
                self.fail("scoped Rowyn repository bypassed sigstoreSigned policy")
            stderr = scoped.stderr.lower()
            if any(marker in stderr for marker in (
                "connection refused",
                "connection reset",
                "dial tcp",
                "forbidden",
                "i/o timeout",
                "manifest unknown",
                "no such host",
                "proxyconnect",
                "temporary failure",
                "tls handshake timeout",
                "too many requests",
                "unauthorized",
            )):
                self.skipTest(f"Rowyn registry fixture unavailable: {scoped.stderr.strip()}")
            self.assertRegex(stderr, "(rejected|signature|update.pub|policy)")

    @unittest.skipUnless(shutil.which("skopeo"), "skopeo is unavailable")
    def test_actual_unscoped_registry_is_rejected_by_default_policy(self):
        temporary, root, policy, registries = self.install_fixture()
        with temporary:
            result = self.run_skopeo_copy(
                policy,
                registries,
                UNSCOPED_IMAGE,
                root / "untrusted",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr.lower(), "(rejected|policy)")


if __name__ == "__main__":
    unittest.main()
