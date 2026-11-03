#!/usr/bin/env python3
"""Contracts for the Rowyn Plymouth boot splash.

The installed system must boot behind a splash: the Rowyn theme
(script module, black background, compact Rowyn mark and rotating loading indicator) is
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

    def test_script_uses_black_background_and_scaled_static_logo(self):
        text = (THEME / "rowyn.script").read_text(encoding="utf-8")
        self.assertIn('Image ("rowyn-logo.png")', text)
        self.assertIn("Window.SetBackgroundTopColor (0, 0, 0)", text)
        self.assertIn("Window.SetBackgroundBottomColor (0, 0, 0)", text)
        self.assertIn("max_size = 160", text)
        self.assertIn("logo.source.Scale", text)
        self.assertIn("spinner.source.Scale", text)
        self.assertIn("last_screen_w != screen_w", text)
        self.assertIn("last_screen_h != screen_h", text)
        self.assertNotIn("Math.Sin", text)
        self.assertNotIn("logo.sprite.SetOpacity", text)

    def test_script_retries_scaling_when_window_dimensions_become_positive(self):
        text = (THEME / "rowyn.script").read_text(encoding="utf-8")
        self.assertIn("logo.source = Image (\"rowyn-logo.png\")", text)
        self.assertIn("spinner.source = Image (\"rowyn-logo.png\")", text)
        self.assertIn("fun resize_splash ()", text)
        self.assertIn("last_screen_w != screen_w", text)
        self.assertIn("last_screen_h != screen_h", text)
        self.assertIn("resize_splash ();", text)
        self.assertIn("logo.source.Scale", text)
        self.assertIn("spinner.source.Scale", text)

    def test_script_animates_centered_loading_spinner_and_handles_messages(self):
        text = (THEME / "rowyn.script").read_text(encoding="utf-8")
        self.assertIn("Plymouth.SetRefreshFunction", text)
        self.assertIn("spinner.image", text)
        self.assertIn("spinner.image.Rotate", text)
        self.assertIn("frame * 0.20944", text)
        self.assertIn("spinner_size = 24", text)
        self.assertIn("Window.GetWidth ()", text)
        self.assertIn("Window.GetHeight ()", text)
        self.assertIn("Plymouth.SetMessageFunction", text)
        self.assertIn('Image.Text (text, 0.85, 0.86, 0.95, 1)', text)

    def test_kargs_dropin_suppresses_scrolling_boot_and_shutdown_status(self):
        with (THEME / "99-rowyn-splash.toml").open("rb") as handle:
            parsed = tomllib.load(handle)
        self.assertIn("quiet", parsed["kargs"])
        self.assertIn("splash", parsed["kargs"])
        self.assertIn("loglevel=3", parsed["kargs"])
        self.assertIn("rd.systemd.show_status=0", parsed["kargs"])
        self.assertIn("systemd.show_status=0", parsed["kargs"])

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
            # plymouth-populate-initrd follows the default.plymouth
            # alternative, so it must resolve to the Rowyn theme.
            generic = root / "usr/share/plymouth/themes/default.plymouth"
            self.assertEqual(
                os.path.realpath(generic),
                str(theme_dir / "rowyn.plymouth"),
            )

    def test_installer_script_registers_the_alternative(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("update-alternatives", text)
        self.assertIn("default.plymouth", text)
        self.assertIn("/usr/share/plymouth/themes/default.plymouth", text)

    def test_containerfile_wires_theme_before_initramfs(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        kde_layer = text.split("FROM base AS system", 1)[1].split("RUN systemctl enable", 1)[0]
        self.assertIn("plymouth", kde_layer)
        logo = text.index("COPY docs/assets/rowyn-logo.png /usr/share/plymouth/themes/rowyn/rowyn-logo.png")
        theme = text.index("/ctx/shared/plymouth-theme.sh --root /")
        initramfs = text.index("/ctx/shared/initramfs.sh")
        self.assertLess(logo, theme)
        self.assertLess(theme, initramfs)

    def test_containerfile_installs_plymouth_text_renderer(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        package_line = next(
            line for line in text.splitlines()
            if "network-manager pipewire wireplumber upower udisks2 plymouth" in line
        )
        self.assertIn("plymouth-label", package_line.split())

    def test_initramfs_includes_and_verifies_plymouth(self):
        text = INITRAMFS.read_text(encoding="utf-8")
        self.assertIn('add_dracutmodules+=" bootc plymouth "', text)
        self.assertIn("lsinitrd", text)
        for required in ("usr/sbin/plymouthd",
                         "usr/share/plymouth/themes/rowyn/rowyn.script",
                         "usr/share/plymouth/themes/rowyn/rowyn-logo.png"):
            self.assertIn(required, text)

    def test_initramfs_verifies_text_renderer_plugin_and_font(self):
        text = INITRAMFS.read_text(encoding="utf-8")
        self.assertIn("/plymouth/label-pango.so", text)
        self.assertIn("usr/share/fonts/truetype/ubuntu/", text)

    def test_image_smoke_checks_cover_the_splash(self):
        workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
        for smoke in (
            "test -s /usr/share/plymouth/themes/rowyn/rowyn.plymouth",
            "test -s /usr/share/plymouth/themes/rowyn/rowyn.script",
            'grep -Fx "Theme=rowyn" /etc/plymouth/plymouthd.conf',
            'test "$(readlink -f /usr/share/plymouth/themes/default.plymouth)" = /usr/share/plymouth/themes/rowyn/rowyn.plymouth',
            'grep -Fq "\\"splash\\"" /usr/lib/bootc/kargs.d/99-rowyn-splash.toml',
            'lsinitrd /usr/lib/modules/*/initramfs.img | grep -Fq "usr/share/plymouth/themes/rowyn/rowyn.script"',
        ):
            self.assertIn(smoke, workflow)


if __name__ == "__main__":
    unittest.main()
