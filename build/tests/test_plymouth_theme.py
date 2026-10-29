#!/usr/bin/env python3
"""Contracts for the Rowyn Plymouth boot splash.

The installed system must boot behind a splash: the Rowyn theme
(script module, pulsing berry mark on deep navy, modeled on Kyth's) is
the plymouth daemon's default, dracut folds it into the initramfs at
image build (verified there by shared/initramfs.sh), and bootc kargs
give deployments `quiet splash` — without the kargs the theme never
draws, and without the initramfs check a missing theme would ship
silently.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "shared/plymouth"
SCRIPT = ROOT / "shared/plymouth-theme.sh"
CONTAINERFILE = ROOT / "Containerfile"
INITRAMFS = ROOT / "shared/initramfs.sh"
BUILD_WORKFLOW = ROOT / ".github/workflows/build.yml"


class PlymouthThemeTests(unittest.TestCase):
    def test_theme_descriptor_points_at_rowyn_script_theme(self):
        text = (THEME / "rowyn.plymouth").read_text(encoding="utf-8")
        self.assertIn("Name=Rowyn", text)
        self.assertIn("ModuleName=script", text)
        self.assertIn("ImageDir=/usr/share/plymouth/themes/rowyn", text)
        self.assertIn("ScriptFile=/usr/share/plymouth/themes/rowyn/rowyn.script", text)

    def test_script_pulses_the_logo_and_handles_messages(self):
        text = (THEME / "rowyn.script").read_text(encoding="utf-8")
        self.assertIn('Image ("rowyn-logo.png")', text)
        # The Kyth-shaped animation: fade in, then a sine pulse.
        self.assertIn("Plymouth.SetRefreshFunction", text)
        self.assertIn("Math.Sin", text)
        self.assertIn("SetOpacity", text)
        self.assertIn("Plymouth.SetMessageFunction", text)
        # Deep navy background at both gradient stops.
        self.assertIn("Window.SetBackgroundTopColor", text)
        self.assertIn("Window.SetBackgroundBottomColor", text)

    def test_kargs_dropin_carries_quiet_splash(self):
        with (THEME / "99-rowyn-splash.toml").open("rb") as handle:
            parsed = tomllib.load(handle)
        self.assertIn("quiet", parsed["kargs"])
        self.assertIn("splash", parsed["kargs"])

    def test_installer_script_places_theme_config_and_kargs(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-plymouth-") as temp:
            root = Path(temp)
            theme_dir = root / "usr/share/plymouth/themes/rowyn"
            theme_dir.mkdir(parents=True)
            # The logo is the Containerfile's job; without it the script
            # must refuse rather than install a blank splash.
            result = subprocess.run(
                [str(SCRIPT), "--root", str(root)],
                text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            (theme_dir / "rowyn-logo.png").write_bytes(b"png")
            result = subprocess.run(
                [str(SCRIPT), "--root", str(root)],
                text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((theme_dir / "rowyn.plymouth").is_file())
            self.assertTrue((theme_dir / "rowyn.script").is_file())
            conf = (root / "etc/plymouth/plymouthd.conf").read_text()
            self.assertIn("[Daemon]", conf)
            self.assertIn("Theme=rowyn", conf)
            kargs = root / "usr/lib/bootc/kargs.d/99-rowyn-splash.toml"
            self.assertTrue(kargs.is_file())

    def test_containerfile_wires_theme_before_initramfs(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        kde_layer = text.split("FROM base AS system", 1)[1].split("RUN systemctl enable", 1)[0]
        self.assertIn("plymouth", kde_layer)
        logo = text.index("COPY docs/assets/rowyn-logo.png /usr/share/plymouth/themes/rowyn/rowyn-logo.png")
        theme = text.index("/ctx/shared/plymouth-theme.sh --root /")
        initramfs = text.index("/ctx/shared/initramfs.sh")
        self.assertLess(logo, theme)
        self.assertLess(theme, initramfs)

    def test_initramfs_includes_and_verifies_plymouth(self):
        text = INITRAMFS.read_text(encoding="utf-8")
        self.assertIn('add_dracutmodules+=" bootc plymouth "', text)
        self.assertIn("lsinitrd", text)
        for required in ("usr/sbin/plymouthd",
                         "usr/share/plymouth/themes/rowyn/rowyn.script",
                         "usr/share/plymouth/themes/rowyn/rowyn-logo.png"):
            self.assertIn(required, text)

    def test_image_smoke_checks_cover_the_splash(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        for smoke in (
            "test -s /usr/share/plymouth/themes/rowyn/rowyn.plymouth",
            "test -s /usr/share/plymouth/themes/rowyn/rowyn.script",
            'grep -Fx "Theme=rowyn" /etc/plymouth/plymouthd.conf',
            'grep -Fq "\\"splash\\"" /usr/lib/bootc/kargs.d/99-rowyn-splash.toml',
            'lsinitrd /usr/lib/modules/*/initramfs.img | grep -Fq "usr/share/plymouth/themes/rowyn/rowyn.script"',
        ):
            self.assertIn(smoke, workflow)


if __name__ == "__main__":
    unittest.main()
