#!/usr/bin/env python3
"""Coverage for Rowyn's shared performance defaults helper and its wiring."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/performance-defaults.sh"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

SYSCTLS = {
    "vm.swappiness": "180",
    "vm.page-cluster": "0",
    "vm.watermark_boost_factor": "0",
    "vm.watermark_scale_factor": "125",
    "vm.max_map_count": "2147483642",
    "fs.inotify.max_user_watches": "524288",
    "fs.inotify.max_user_instances": "1024",
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
}
PACKAGES = [
    "kwin-wayland", "plasma-workspace", "plasma-session-x11", "xserver-xorg",
    "power-profiles-daemon", "systemd-zram-generator", "gamemode",
    "steam-devices",
]


def install_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"apt-get install -y[^\n]*\\\n(.*?)\s&& \\", text, re.S):
        tokens.update(match.group(1).replace("\\", " ").split())
    return tokens


def settings(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class PerformanceDefaultsTests(unittest.TestCase):
    def run_helper(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root), *args],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def snapshot(self, root: Path) -> dict[str, tuple[str, int]]:
        return {
            str(p.relative_to(root)): (p.read_text(encoding="utf-8"), p.stat().st_mode)
            for p in sorted(root.rglob("*")) if p.is_file()
        }

    def test_helper_is_executable_and_strict(self):
        self.assertTrue(os.access(HELPER, os.X_OK))
        self.assertIn("set -euo pipefail", HELPER.read_text(encoding="utf-8"))

    def test_rejects_unknown_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_helper(Path(tmp), "--bogus").returncode, 2)

    def test_writes_every_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)

            zram = (root / "usr/lib/systemd/zram-generator.conf").read_text()
            self.assertIn("[zram0]", zram)
            zram_values = settings(root / "usr/lib/systemd/zram-generator.conf")
            self.assertEqual(zram_values["zram-size"], "min(ram / 2, 8192)")
            self.assertEqual(zram_values["compression-algorithm"], "zstd")
            self.assertEqual(zram_values["swap-priority"], "100")

            sysctl = settings(root / "usr/lib/sysctl.d/90-rowyn-performance.conf")
            self.assertEqual(sysctl, SYSCTLS)

            self.assertEqual(
                settings(root / "usr/lib/modules-load.d/rowyn-bbr.conf"), {})
            bbr = (root / "usr/lib/modules-load.d/rowyn-bbr.conf").read_text()
            self.assertIn("\ntcp_bbr\n", "\n" + bbr)
            ntsync = (root / "usr/lib/modules-load.d/rowyn-ntsync.conf").read_text()
            self.assertIn("\nntsync\n", "\n" + ntsync)

            tmpfiles = (root / "usr/lib/tmpfiles.d/rowyn-performance.conf").read_text()
            for line in (
                "w /sys/kernel/mm/lru_gen/enabled - - - - y",
                "w /sys/kernel/mm/transparent_hugepage/enabled - - - - madvise",
                "w /sys/kernel/mm/transparent_hugepage/defrag - - - - defer+madvise",
            ):
                self.assertIn(line, tmpfiles.splitlines())

            rule = (root / "usr/lib/udev/rules.d/70-rowyn-ntsync.rules").read_text()
            self.assertIn('KERNEL=="ntsync", MODE="0660", TAG+="uaccess"', rule)

            for scope in ("system", "user"):
                path = root / f"usr/lib/systemd/{scope}.conf.d/90-rowyn-limits.conf"
                text = path.read_text()
                self.assertIn("[Manager]", text)
                self.assertEqual(settings(path)["DefaultLimitNOFILE"], "1024:1048576")

    def test_files_are_world_readable_and_commented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_helper(root).returncode, 0)
            for path, (text, mode) in self.snapshot(root).items():
                self.assertEqual(mode & 0o777, 0o644, path)
                self.assertTrue(any(l.startswith("#") for l in text.splitlines()), path)

    def test_idempotent_and_leaves_etc_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            override = root / "etc/sysctl.d/90-rowyn-performance.conf"
            override.parent.mkdir(parents=True)
            override.write_text("vm.swappiness = 60\n")
            self.assertEqual(self.run_helper(root).returncode, 0)
            first = self.snapshot(root)
            self.assertEqual(self.run_helper(root).returncode, 0)
            self.assertEqual(self.snapshot(root), first)
            self.assertEqual(override.read_text(), "vm.swappiness = 60\n")
            self.assertFalse(any(p.startswith("etc/") and p != "etc/sysctl.d/90-rowyn-performance.conf"
                                 for p in first))


class PerformanceWiringTests(unittest.TestCase):
    def test_containerfile_installs_packages_in_kde_layer(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        kde_layer = text.split("apt-get install -y --no-install-recommends", 1)[0]
        tokens = install_tokens(kde_layer)
        for package in PACKAGES:
            self.assertIn(package, tokens)

    def test_containerfile_calls_helper_after_rootfs_normalization(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        call = "/ctx/shared/performance-defaults.sh --root /"
        self.assertIn(call, text)
        self.assertGreater(text.index(call), text.index("/ctx/shared/bootc-rootfs.sh"))

    def test_power_profiles_daemon_is_enabled(self):
        text = CONTAINERFILE.read_text(encoding="utf-8")
        enables = [e for e in re.findall(r"systemctl enable ([^&]+)&&", text)
                   if "sddm.service" in e]
        self.assertIn("power-profiles-daemon.service", enables[0].replace("\\", " ").split())

    def test_sddm_greeter_stays_on_x11(self):
        text = (ROOT / "shared/installed-desktop-defaults.sh").read_text(encoding="utf-8")
        self.assertIn("DisplayServer=x11", text)

    def test_workflow_smoke_checks_cover_performance_files(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for needle in (
            "/usr/share/wayland-sessions/plasma.desktop",
            "/usr/bin/kwin_wayland",
            "graphical.target.wants/power-profiles-daemon.service",
            "/usr/lib/systemd/system-generators/zram-generator",
            "/usr/lib/sysctl.d/90-rowyn-performance.conf",
            "/usr/games/gamemoderun",
            "/usr/lib/udev/rules.d/60-steam-input.rules",
            "/usr/lib/udev/rules.d/70-rowyn-ntsync.rules",
            "/usr/lib/modules-load.d/rowyn-ntsync.conf",
        ):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
