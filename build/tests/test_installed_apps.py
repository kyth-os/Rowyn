#!/usr/bin/env python3
"""Regression tests for the bounded bootc Flatpak-first app layer."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/installed-apps.sh"
REPO_ARTIFACT = ROOT / "shared/flathub-v1.flatpakrepo"


class InstalledAppsTests(unittest.TestCase):
    def run_helper(self, root: Path, path: str | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PATH"] = path or "/usr/bin:/bin"
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root)],
            text=True,
            capture_output=True,
            env=env,
        )

    def test_artifact_bundles_the_flathub_verification_key(self):
        self.assertTrue(REPO_ARTIFACT.name.startswith("flathub-v1."))
        text = REPO_ARTIFACT.read_text(encoding="utf-8")
        self.assertIn("[Flatpak Repo]", text)
        self.assertIn("Url=https://dl.flathub.org/repo/", text)
        self.assertIn("GPGKey=", text)
        self.assertGreater(len(text.split("GPGKey=", 1)[1].splitlines()[0]), 100)
        self.assertNotIn("NoGPGVerify", text)

    def test_helper_bootstraps_absent_persistent_state_without_flatpak_or_network(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-apps-") as temporary:
            root = Path(temporary)
            fake = root / "fake-bin"
            fake.mkdir()
            (fake / "flatpak").write_text("#!/bin/sh\nexit 99\n")
            (fake / "curl").write_text("#!/bin/sh\nexit 98\n")
            for command in ("flatpak", "curl"):
                (fake / command).chmod(0o755)

            result = self.run_helper(root, f"{fake}:/usr/bin:/bin")

            self.assertEqual(result.returncode, 0, result.stderr)
            # Flatpak initializes /var on the installed system, not in the image.
            self.assertFalse((root / "var").exists())
            self.assertTrue((root / "etc/flatpak/remotes.d/flathub.flatpakrepo").is_file())
            self.assertTrue((root / "usr/share/rowyn/installed-apps/policy.conf").is_file())

    def test_helper_preserves_existing_flatpak_data_after_stateless_var_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-apps-persist-") as temporary:
            root = Path(temporary)
            data = root / "var/lib/flatpak/app/org.example.Existing/current/files"
            data.mkdir(parents=True)
            marker = data / "user-data"
            marker.write_text("preserve me", encoding="utf-8")

            result = self.run_helper(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me")
            self.assertTrue((root / "var/lib/flatpak/app/org.example.Existing/current/files").is_dir())

    def test_helper_is_idempotent_and_does_not_duplicate_remote_or_policy(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-apps-idempotent-") as temporary:
            root = Path(temporary)
            first = self.run_helper(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            tracked = [
                root / "etc/flatpak/remotes.d/flathub.flatpakrepo",
                root / "usr/share/rowyn/installed-apps/policy.conf",
            ]
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}

            second = self.run_helper(root)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked})
            self.assertEqual(len(list((root / "etc/flatpak/remotes.d").glob("*.flatpakrepo"))), 1)
            self.assertEqual(len(list((root / "usr/share/rowyn/installed-apps").glob("policy.conf"))), 1)

    def test_policy_explicitly_separates_flatpak_gui_and_rootless_cli_paths(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-installed-apps-policy-") as temporary:
            root = Path(temporary)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            policy = (root / "usr/share/rowyn/installed-apps/policy.conf").read_text()
            self.assertIn("GuiApplications=flatpak", policy)
            self.assertIn("CliApplications=podman+distrobox", policy)
            self.assertIn("PersistentInstallation=/var/lib/flatpak", policy)
            self.assertIn("NativeGuiPackages=disabled", policy)
            self.assertIn("SnapGuiPackages=disabled", policy)
            self.assertIn("PackageKitGuiPackages=disabled", policy)

    def test_native_discover_backends_are_removed_without_touching_flatpak(self):
        # Debian Discover depends on PackageKit; removing that DEB removes Discover.
        # Disable the native plugins instead of exposing APT updates on immutable /usr.
        with tempfile.TemporaryDirectory(prefix="rowyn-discover-backends-") as temporary:
            root = Path(temporary)
            plugins = root / "usr/lib/x86_64-linux-gnu/qt6/plugins/discover"
            plugins.mkdir(parents=True)
            for name in ("packagekit-backend.so", "snap-backend.so", "flatpak-backend.so", "fwupd-backend.so"):
                (plugins / name).write_text(name)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("packagekit-backend.so", "snap-backend.so"):
                self.assertFalse((plugins / name).exists())
            for name in ("flatpak-backend.so", "fwupd-backend.so"):
                self.assertEqual((plugins / name).read_text(), name)
            mask = root / "etc/systemd/system/packagekit.service"
            self.assertTrue(mask.is_symlink())
            self.assertEqual(mask.readlink(), Path("/dev/null"))
            self.assertEqual(self.run_helper(root).returncode, 0)

    def test_distrobox_uses_rootless_podman_and_ubuntu_by_default(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-distrobox-") as temporary:
            root = Path(temporary)
            self.assertEqual(self.run_helper(root).returncode, 0)
            config = root / "etc/distrobox/distrobox.conf"
            self.assertTrue(config.is_file())
            text = config.read_text()
            self.assertIn('container_manager="podman"', text)
            self.assertIn('container_image_default="docker.io/library/ubuntu:26.10"', text)
            self.assertNotIn("fedora", text.lower())
            self.assertNotIn("container_root=1", text)

    def test_payload_integrates_app_layer_after_stateless_normalization(self):
        payload = (ROOT / "Containerfile").read_text()
        for package in ("flatpak", "plasma-discover-backend-flatpak", "distrobox", "slirp4netns", "uidmap", "fuse-overlayfs"):
            self.assertIn(package, payload)
        self.assertIn("apt-get install -y --no-install-recommends", payload)
        self.assertIn("/ctx/shared/installed-apps.sh --root /", payload)
        self.assertLess(payload.index("/ctx/shared/bootc-rootfs.sh"),
                        payload.index("/ctx/shared/installed-apps.sh"))
        self.assertIn("apt-get purge -y plasma-discover-backend-snap snapd", payload)

    @unittest.skipUnless(os.environ.get("ROWYN_TEST_UBUNTU_APP_POLICY") == "1",
                         "opt-in installed Ubuntu Flatpak/Discover runtime fixture")
    def test_real_ubuntu_apps_remain_flatpak_only_after_var_initialization(self):
        image = os.environ.get("ROWYN_TEST_UBUNTU_APP_IMAGE", "localhost/rowyn-app-runtime:verified")
        script = '''
set -eu
bash /rowyn/installed-apps.sh --root /
rm -rf /var/lib/flatpak
flatpak remotes --system --columns=name,url,options
python3 -c 'import configparser; c=configparser.ConfigParser(); c.read("/var/lib/flatpak/repo/config"); assert c.getboolean("remote \\\"flathub\\\"", "gpg-verify")'
printf preserved > /var/lib/flatpak/rowyn-marker
bash /rowyn/installed-apps.sh --root /
test "$(cat /var/lib/flatpak/rowyn-marker)" = preserved
test "$(readlink /etc/systemd/system/packagekit.service)" = /dev/null
QT_QPA_PLATFORM=offscreen LC_ALL=C.UTF-8 plasma-discover --listbackends
distrobox-create --help
'''
        result = subprocess.run([
            "podman", "run", "--rm", "--network", "none", "--entrypoint", "/bin/bash",
            "-v", f"{ROOT / 'shared'}:/rowyn:ro,z", image, "-c", script,
        ], text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("flathub\thttps://dl.flathub.org/repo/", result.stdout)
        self.assertIn(" * flatpak-backend", result.stdout)
        self.assertNotIn(" * packagekit-backend", result.stdout)
        self.assertNotIn(" * snap-backend", result.stdout)
        self.assertIn("default: docker.io/library/ubuntu:26.10", result.stdout)

    def test_image_smoke_checks_real_flatpak_and_discover_backends(self):
        smoke = (ROOT / ".github/workflows/build.yml").read_text()
        for check in ("flatpak remotes --system", "plasma-discover --listbackends",
                      "flatpak-backend", "(packagekit|snap)-backend", "distrobox-create --help"):
            self.assertIn(check, smoke)

    def test_helper_has_no_host_package_or_signature_bypass_path(self):
        source = "\n".join(line for line in HELPER.read_text(encoding="utf-8").splitlines()
                           if not line.lstrip().startswith("#"))
        for forbidden in ("apt-get", "apt ", "snap install", "packagekit install", "--no-gpg-verify", "remote-add"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
