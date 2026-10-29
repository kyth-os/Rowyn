#!/usr/bin/env python3
"""Contracts for the payload's login-user surface.

An installed Rowyn system must offer exactly one interactive account at
the login screen: the one the installer creates. The image therefore
bakes in no login users at all — the spike-era `rowyn` account is gone
(useradd, throwaway password, sudo grant, SSH key, tmpfiles home), and
the Ubuntu base image's `ubuntu` account is deleted during the build.
"""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"


class PayloadUserTests(unittest.TestCase):
    def test_no_spike_user_is_created(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        # ("/etc/default/useradd" the config file is unrelated and stays.)
        self.assertNotIn("useradd --home-dir", text)
        self.assertNotIn("| chpasswd", text)
        self.assertNotIn("90-rowyn-spike", text)
        self.assertNotIn("rowyn-spike.conf", text)
        self.assertNotIn("spike.pub", text)
        self.assertNotIn("--user rowyn", text)

    def test_base_image_ubuntu_user_is_removed(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("userdel --remove ubuntu", text)
        # The removal happens in the system stage, before the desktop
        # layers that could come to depend on the account.
        system = text.split("FROM base AS system", 1)[1]
        self.assertLess(system.index("userdel --remove ubuntu"),
                        system.index("plasma-desktop"))

    def test_kde_defaults_come_from_system_wide_paths(self):
        # First-login defaults for the installer-created account ride on
        # the system-wide install (etc/xdg + /etc/skel), which stays; the
        # per-user bake for the removed spike account must not return.
        text = CONTAINERFILE.read_text(encoding="utf-8")
        self.assertIn("/ctx/shared/kde-dark-defaults.sh --root /", text)
        self.assertIn("/ctx/shared/kde-wallpaper-defaults.sh --root /", text)

    def test_image_smoke_checks_assert_no_baked_in_users(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("! id rowyn", workflow)
        self.assertIn("! id ubuntu", workflow)
        self.assertIn('! grep -Eq "^(rowyn|ubuntu):" /etc/passwd', workflow)
        self.assertIn("test ! -e /etc/sudoers.d/90-rowyn-spike", workflow)
        self.assertIn("test ! -e /usr/lib/tmpfiles.d/rowyn-spike.conf", workflow)
        self.assertNotIn("test -s /etc/sudoers.d/90-rowyn-spike", workflow)


if __name__ == "__main__":
    unittest.main()
