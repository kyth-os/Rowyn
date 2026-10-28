#!/usr/bin/env python3
"""Regression tests for the payload and Ubuntu live update-policy wiring."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
LIVE_CONTEXT = ROOT / "build/custom-installer-iso.sh"
LIVE_CONFIG = ROOT / "build/configure-live.sh"
ISO_WORKFLOW = ROOT / ".github/workflows/installer-iso.yml"
HELPER = ROOT / "shared/update-policy.sh"
KEY_PATH = "/usr/share/rowyn/update.pub"


class UpdatePolicyWiringTests(unittest.TestCase):
    def test_payload_copies_key_and_runs_policy_after_stateless_normalization(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")

        self.assertIn("COPY keys/update.pub /usr/share/rowyn/update.pub", text)
        self.assertIn("/ctx/shared/update-policy.sh", text)
        self.assertIn("/usr/libexec/rowyn-update-policy --root /", text)
        self.assertIn("/usr/share/rowyn/update.pub", text)
        self.assertLess(text.index("/ctx/shared/bootc-rootfs.sh"), text.index("/ctx/shared/update-policy.sh"))
        self.assertNotIn("/var/etc/containers", text)
        self.assertNotIn("/var/usr/share/rowyn", text)

    def test_live_context_stages_helper_and_key_without_bypassing_pinned_source(self):
        text = LIVE_CONTEXT.read_text(encoding="utf-8")

        source = text.index("python3 build/ubuntu-installer-source.py")
        staging = text.index("shared/update-policy.sh")
        self.assertLess(source, staging)
        self.assertIn("shared/update-policy.sh", text)
        self.assertIn("keys/update.pub", text)
        self.assertIn("$work/source/rowyn/", text)
        self.assertIn("KYTH_INSTALLER_COMMIT", text)
        self.assertIn("Unreviewed installer source", text)

        staging_command = next(
            line.strip()
            for line in text.splitlines()
            if line.startswith("cp shared/kde-dark-defaults.sh")
        )
        with tempfile.TemporaryDirectory(prefix="rowyn-live-context-") as temporary:
            root = Path(temporary)
            for relative in (
                "shared/kde-dark-defaults.sh",
                "shared/kde-wallpaper-defaults.sh",
                "shared/update-policy.sh",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            fixture_key = root / "keys/update.pub"
            fixture_key.parent.mkdir(parents=True, exist_ok=True)
            fixture_key.write_text("fixture public key\n", encoding="utf-8")
            target = root / "source/rowyn"
            target.mkdir(parents=True)
            result = subprocess.run(
                ["/bin/bash", "-c", f"work={root!s}; {staging_command}"],
                cwd=root,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (target / "update-policy.sh").read_text(encoding="utf-8"),
                "shared/update-policy.sh\n",
            )
            self.assertEqual(
                (target / "update.pub").read_text(encoding="utf-8"),
                "fixture public key\n",
            )

    def test_live_configuration_installs_key_and_policy_before_runtime_setup(self):
        text = LIVE_CONFIG.read_text(encoding="utf-8")

        key_install = 'install -Dm644 /rowyn/update.pub ' + KEY_PATH
        helper_install = "install -Dm755 /rowyn/update-policy.sh /usr/libexec/rowyn-update-policy"
        policy_run = "/usr/libexec/rowyn-update-policy --root /"
        self.assertIn(key_install, text)
        self.assertIn(helper_install, text)
        self.assertIn(policy_run, text)
        self.assertLess(text.index(policy_run), text.index("apt-get update"))
        self.assertLess(text.index(policy_run), text.index("update-initramfs"))

    def test_iso_trigger_rebuilds_for_policy_helper_and_key(self):
        workflow = yaml.safe_load(ISO_WORKFLOW.read_text(encoding="utf-8"))
        paths = workflow[True]["push"]["paths"]

        self.assertIn("shared/update-policy.sh", paths)
        self.assertIn("keys/update.pub", paths)


    def test_policy_helper_stays_stateless_when_run_against_a_fixture_root(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-policy-root-") as temporary:
            root = Path(temporary)
            result = subprocess.run(
                ["/bin/bash", str(HELPER), "--root", str(root)],
                text=True,
                capture_output=True,
                env=dict(os.environ, PATH="/usr/bin:/bin"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "etc/containers/policy.json").is_file())
            self.assertFalse((root / "var").exists())


if __name__ == "__main__":
    unittest.main()
