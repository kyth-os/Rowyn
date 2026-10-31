#!/usr/bin/env python3
"""Coverage for Rowyn's oomd, sleep, slice, PowerDevil, GameMode power and
Wi-Fi regulatory-domain defaults (shared/desktop-tuning.sh and friends)."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/desktop-tuning.sh"
REGDOM = ROOT / "shared/rowyn-wifi-regdom"
REGDOM_UNIT = ROOT / "shared/rowyn-wifi-regdom.service"
CONTAINERFILE = ROOT / "Containerfile"
WORKFLOW = ROOT / ".github/workflows/build.yml"

POWER = "usr/libexec/rowyn-gamemode-power"


def ini(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
        elif "=" in line:
            key, value = line.split("=", 1)
            sections[current][key.strip()] = value.strip()
    return sections


def fake(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


class InstalledTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        done = subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(cls.root)],
            text=True, capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin"))
        assert done.returncode == 0, done.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class SystemdDropIns(InstalledTree):
    def test_oomd_thresholds(self):
        self.assertEqual(ini(self.root / "usr/lib/systemd/oomd.conf.d/90-rowyn.conf"), {
            "OOM": {
                "SwapUsedLimit": "85%",
                "DefaultMemoryPressureLimit": "65%",
                "DefaultMemoryPressureDurationSec": "40s",
            }})

    def test_oomd_user_slice_and_user_manager(self):
        self.assertEqual(ini(self.root / "usr/lib/systemd/system/user.slice.d/90-rowyn-oomd.conf"), {
            "Slice": {
                "ManagedOOMSwap": "kill",
                "ManagedOOMMemoryPressure": "kill",
                "ManagedOOMMemoryPressureLimit": "65%",
            }})
        # The package's user@.service drop-in pins 50%; ours sorts after it.
        self.assertEqual(ini(self.root / "usr/lib/systemd/system/user@.service.d/90-rowyn-oomd.conf"), {
            "Service": {
                "ManagedOOMMemoryPressure": "kill",
                "ManagedOOMMemoryPressureLimit": "65%",
            }})

    def test_sleep_policy(self):
        self.assertEqual(ini(self.root / "usr/lib/systemd/sleep.conf.d/90-rowyn.conf"), {
            "Sleep": {
                "AllowHibernation": "no",
                "AllowHybridSleep": "no",
                "AllowSuspendThenHibernate": "no",
                "SuspendState": "mem standby freeze",
            }})

    def test_slice_weights_separate_from_oomd(self):
        weights = self.root / "usr/lib/systemd/system/user.slice.d/90-rowyn-weights.conf"
        self.assertEqual(ini(weights), {"Slice": {"CPUWeight": "1000", "IOWeight": "1000"}})
        self.assertFalse((self.root / "usr/lib/systemd/system/system.slice.d").exists())

    def test_powerdevil_defaults(self):
        self.assertEqual(ini(self.root / "etc/xdg/powerdevilrc"), {
            "AC][Performance": {"PowerProfile": "balanced"},
            "Battery][Performance": {"PowerProfile": "power-saver"},
            "LowBattery][Performance": {"PowerProfile": "power-saver"},
        })

    def test_all_new_files_commented_and_vendor_located(self):
        for rel in [
            "usr/lib/systemd/oomd.conf.d/90-rowyn.conf",
            "usr/lib/systemd/system/user.slice.d/90-rowyn-oomd.conf",
            "usr/lib/systemd/system/user@.service.d/90-rowyn-oomd.conf",
            "usr/lib/systemd/sleep.conf.d/90-rowyn.conf",
            "usr/lib/systemd/system/user.slice.d/90-rowyn-weights.conf",
            "etc/xdg/powerdevilrc",
            "usr/lib/systemd/system/rowyn-wifi-regdom.service",
        ]:
            path = self.root / rel
            self.assertEqual(path.stat().st_mode & 0o777, 0o644, rel)
            self.assertTrue(any(l.startswith("#") for l in path.read_text().splitlines()), rel)
        self.assertFalse((self.root / "var").exists())
        # Nothing under /etc except the documented GameMode and KDE defaults.
        etc = {str(p.relative_to(self.root)) for p in (self.root / "etc").rglob("*") if p.is_file()}
        self.assertEqual(etc, {"etc/xdg/baloofilerc", "etc/gamemode.ini", "etc/xdg/powerdevilrc"})

    def test_idempotent(self):
        def snap():
            return {str(p.relative_to(self.root)): p.read_bytes()
                    for p in sorted(self.root.rglob("*")) if p.is_file()}
        before = snap()
        done = subprocess.run(["/bin/bash", str(HELPER), "--root", str(self.root)],
                              capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin"))
        self.assertEqual(done.returncode, 0)
        self.assertEqual(snap(), before)

    def test_gamemode_ini_hooks_in_order(self):
        lines = [l for l in (self.root / "etc/gamemode.ini").read_text().splitlines()
                 if l and not l.startswith("#")]
        self.assertEqual(lines, [
            "[custom]",
            "start=/usr/libexec/rowyn-gamemode-audio start",
            "end=/usr/libexec/rowyn-gamemode-audio end",
            "start=/usr/libexec/rowyn-gamemode-power start",
            "end=/usr/libexec/rowyn-gamemode-power end",
        ])


class GamemodePowerHook(InstalledTree):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.run_dir = self.dir / "run"
        self.run_dir.mkdir()
        self.log = self.dir / "calls.log"
        self.profile = self.dir / "profile"
        self.profile.write_text("balanced\n")

    def ppc(self, available="performance balanced power-saver", exit_code=0):
        listing = "".join(
            f"{'*' if n == 'balanced' else ' '} {n}:\n    Driver: x\n\n"
            for n in available.split())
        fake(self.bin / "powerprofilesctl", f"""
echo "$*" >> '{self.log}'
case "$1" in
    get) cat '{self.profile}' ;;
    list) printf '%s' '{listing}' ;;
    set) printf '%s\\n' "$2" > '{self.profile}' ;;
esac
exit {exit_code}
""")

    def hook(self, arg, runtime=True, path=None):
        env = {"PATH": path or f"{self.bin}:/usr/bin:/bin"}
        if runtime:
            env["XDG_RUNTIME_DIR"] = str(self.run_dir)
        return subprocess.run([str(self.root / POWER), arg], env=env,
                              capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_executable_valid_shell(self):
        path = self.root / POWER
        self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(subprocess.run(["sh", "-n", str(path)]).returncode, 0)

    def test_start_then_end_restores(self):
        self.ppc()
        self.assertEqual(self.hook("start").returncode, 0)
        self.assertEqual(self.profile.read_text().strip(), "performance")
        self.assertEqual((self.run_dir / "rowyn-gamemode-power").read_text().strip(), "balanced")
        self.assertEqual(self.hook("end").returncode, 0)
        self.assertEqual(self.profile.read_text().strip(), "balanced")
        self.assertFalse((self.run_dir / "rowyn-gamemode-power").exists())

    def test_restores_power_saver(self):
        self.ppc()
        self.profile.write_text("power-saver\n")
        self.hook("start")
        self.hook("end")
        self.assertEqual(self.profile.read_text().strip(), "power-saver")

    def test_second_start_keeps_original(self):
        self.ppc()
        self.hook("start")
        self.hook("start")
        self.hook("end")
        self.assertEqual(self.profile.read_text().strip(), "balanced")

    def test_no_performance_profile_is_noop(self):
        self.ppc(available="balanced power-saver")
        self.assertEqual(self.hook("start").returncode, 0)
        self.assertEqual(self.profile.read_text().strip(), "balanced")
        self.assertFalse(any(c.startswith("set") for c in self.calls()))
        self.assertFalse((self.run_dir / "rowyn-gamemode-power").exists())
        self.assertEqual(self.hook("end").returncode, 0)

    def test_already_performance_is_left_alone(self):
        self.ppc()
        self.profile.write_text("performance\n")
        self.hook("start")
        self.hook("end")
        self.assertEqual(self.profile.read_text().strip(), "performance")
        self.assertFalse(any(c.startswith("set") for c in self.calls()))

    def test_user_change_during_game_is_kept(self):
        self.ppc()
        self.hook("start")
        self.profile.write_text("power-saver\n")
        self.hook("end")
        self.assertEqual(self.profile.read_text().strip(), "power-saver")

    def test_failures_never_propagate(self):
        self.ppc(exit_code=1)
        self.assertEqual(self.hook("start").returncode, 0)
        self.assertEqual(self.hook("end").returncode, 0)
        self.assertEqual(self.hook("bogus").returncode, 0)
        self.assertEqual(self.hook("").returncode, 0)

    def test_missing_tool_or_runtime_dir(self):
        self.assertEqual(self.hook("start", path="/usr/bin:/bin").returncode, 0)
        self.ppc()
        self.assertEqual(self.hook("start", runtime=False).returncode, 0)
        self.assertEqual(self.profile.read_text().strip(), "balanced")

    def test_garbage_state_is_ignored(self):
        self.ppc()
        (self.run_dir / "rowyn-gamemode-power").write_text("$(touch /x); rm\n")
        self.assertEqual(self.hook("end").returncode, 0)
        self.assertEqual(self.profile.read_text().strip(), "balanced")


ZONE_TAB = (
    "# comment\n"
    "NL\t+5222+00454\tEurope/Amsterdam\n"
    "US\t+404251-0740023\tAmerica/New_York\tEastern\n"
    "DE\t+5230+01322\tEurope/Berlin\tmost of Germany\n"
)
ZONE1970_TAB = (
    "# comment\n"
    "BE,LU,NL\t+5050+00420\tEurope/Brussels\n"
    "DE,DK,NO,SE,SJ\t+5230+01322\tEurope/Berlin\tmost of Germany\n"
    "FR,MC\t+4852+00220\tEurope/Paris\n"
    "AU\t-3352+15113\tAustralia/Sydney\tNew South Wales\n"
)


class WifiRegdom(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "iw.log"
        self.iw = fake(self.dir / "fakeiw", f'echo "$*" >> \'{self.log}\'\nexit "${{FAKE_IW_EXIT:-0}}"\n')
        self.zone = self.dir / "timezone"
        self.zonetab = self.dir / "zone.tab"
        self.zone1970 = self.dir / "zone1970.tab"
        self.zonetab.write_text(ZONE_TAB)
        self.zone1970.write_text(ZONE1970_TAB)
        self.cfg80211 = self.dir / "cfg80211"
        self.cfg80211.mkdir()

    def run_script(self, zone=None, **extra):
        if zone is not None:
            self.zone.write_text(zone + "\n")
        env = {
            "PATH": "/usr/bin:/bin",
            "IW": str(self.iw),
            "ROWYN_TIMEZONE_FILE": str(self.zone),
            "ROWYN_LOCALTIME_LINK": str(self.dir / "no-localtime"),
            "ROWYN_ZONE_TAB": str(self.zonetab),
            "ROWYN_ZONE1970_TAB": str(self.zone1970),
            "ROWYN_REGDOM_OVERRIDE_FILE": str(self.dir / "no-override"),
            "ROWYN_CFG80211_DIR": str(self.cfg80211),
            "ROWYN_REGDOM_RETRY_SLEEP": "0",
        }
        env.update(extra)
        return subprocess.run(["sh", str(REGDOM)], env=env, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_valid_shell_and_executable(self):
        self.assertTrue(os.access(REGDOM, os.X_OK))
        self.assertEqual(subprocess.run(["sh", "-n", str(REGDOM)]).returncode, 0)

    def test_single_country_zone(self):
        self.assertEqual(self.run_script("America/New_York").returncode, 0)
        self.assertEqual(self.calls(), ["reg set US"])

    def test_zone_tab_covers_link_zones(self):
        self.run_script("Europe/Amsterdam")
        self.assertEqual(self.calls(), ["reg set NL"])

    def test_multi_country_first_wins_in_zone1970(self):
        self.zonetab.write_text("# empty\n")
        self.run_script("Europe/Berlin")
        self.assertEqual(self.calls(), ["reg set DE"])
        self.log.unlink()
        self.run_script("Europe/Brussels")
        self.assertEqual(self.calls(), ["reg set BE"])

    def test_zone1970_fallback_when_zone_tab_lacks_zone(self):
        self.run_script("Australia/Sydney")
        self.assertEqual(self.calls(), ["reg set AU"])

    def test_utc_and_unknown_are_skipped(self):
        for zone in ["Etc/UTC", "UTC", "Etc/GMT+5", "Mars/Olympus", "", "../../etc/passwd",
                     "America/New_York; reboot"]:
            with self.subTest(zone=zone):
                self.assertEqual(self.run_script(zone).returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_localtime_symlink_fallback(self):
        target = self.dir / "usr/share/zoneinfo/Europe"
        target.mkdir(parents=True)
        (target / "Berlin").write_text("")
        (self.dir / "localtime").symlink_to(target / "Berlin")
        self.assertEqual(self.run_script(
            ROWYN_TIMEZONE_FILE=str(self.dir / "absent"),
            ROWYN_LOCALTIME_LINK=str(self.dir / "localtime")).returncode, 0)
        self.assertEqual(self.calls(), ["reg set DE"])

    def test_override_file(self):
        override = self.dir / "override"
        override.write_text("gb\n")
        self.run_script("America/New_York", ROWYN_REGDOM_OVERRIDE_FILE=str(override))
        self.assertEqual(self.calls(), ["reg set GB"])
        self.log.unlink()
        override.write_text("off\n")
        self.run_script("America/New_York", ROWYN_REGDOM_OVERRIDE_FILE=str(override))
        self.assertEqual(self.calls(), [])

    def test_missing_iw_is_not_an_error(self):
        done = self.run_script("America/New_York", IW=str(self.dir / "nonexistent-iw"))
        self.assertEqual(done.returncode, 0)

    def test_no_cfg80211_means_no_call(self):
        self.cfg80211.rmdir()
        self.assertEqual(self.run_script("America/New_York").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_iw_failure_retries_then_exits_zero(self):
        done = self.run_script("America/New_York", FAKE_IW_EXIT="1")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.calls(), ["reg set US"] * 3)

    def test_garbage_tables_and_missing_files_exit_zero(self):
        self.zonetab.write_bytes(b"\xff\xfe\x00garbage\n\t\t\n")
        self.zone1970.write_text("no tabs here\n")
        self.assertEqual(self.run_script("America/New_York").returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_script(
            "America/New_York", ROWYN_ZONE_TAB="/nonexistent",
            ROWYN_ZONE1970_TAB="/nonexistent").returncode, 0)

    def test_real_style_tab_with_lowercase_country_ignored(self):
        self.zonetab.write_text("us\t+0+0\tAmerica/New_York\n")
        self.zone1970.write_text("")
        self.run_script("America/New_York")
        self.assertEqual(self.calls(), [])


class WifiRegdomWiring(InstalledTree):
    def test_installed_copies(self):
        script = self.root / "usr/libexec/rowyn-wifi-regdom"
        self.assertEqual(script.read_bytes(), REGDOM.read_bytes())
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        self.assertEqual(
            (self.root / "usr/lib/systemd/system/rowyn-wifi-regdom.service").read_bytes(),
            REGDOM_UNIT.read_bytes())

    def test_unit(self):
        unit = ini(REGDOM_UNIT)
        self.assertEqual(unit["Service"]["Type"], "oneshot")
        self.assertEqual(unit["Service"]["ExecStart"], "/usr/libexec/rowyn-wifi-regdom")
        self.assertEqual(unit["Service"]["RemainAfterExit"], "yes")
        self.assertEqual(unit["Install"]["WantedBy"], "multi-user.target")
        self.assertEqual(unit["Unit"]["ConditionPathExists"], "/usr/sbin/iw")
        before = unit["Unit"]["Before"].split()
        for name in ("NetworkManager.service", "wpa_supplicant.service", "network-pre.target"):
            self.assertIn(name, before)
        self.assertIn("systemd-udev-trigger.service", unit["Unit"]["After"].split())

    def test_script_never_uses_set_e(self):
        text = REGDOM.read_text()
        self.assertNotRegex(text, r"(?m)^\s*set\s+-[a-z]*e")


class BuildWiring(unittest.TestCase):
    def test_containerfile_installs_and_enables(self):
        text = CONTAINERFILE.read_text()
        self.assertRegex(text, r"systemd-oomd iw")
        self.assertIn("/ctx/shared/desktop-tuning.sh --root /", text)
        self.assertIn(
            "systemctl enable rowyn-wifi-regdom.service systemd-oomd.service systemd-oomd.socket", text)

    def test_workflow_smoke_lines(self):
        text = WORKFLOW.read_text()
        for needle in [
            "/usr/libexec/rowyn-gamemode-power",
            "/usr/lib/systemd/oomd.conf.d/90-rowyn.conf",
            "/usr/lib/systemd/system/user.slice.d/90-rowyn-oomd.conf",
            "/usr/lib/systemd/sleep.conf.d/90-rowyn.conf",
            "/usr/lib/systemd/system/user.slice.d/90-rowyn-weights.conf",
            "/etc/xdg/powerdevilrc",
            "/usr/libexec/rowyn-wifi-regdom",
            "multi-user.target.wants/rowyn-wifi-regdom.service",
            "multi-user.target.wants/systemd-oomd.service",
        ]:
            self.assertIn(needle, text)
        # The smoke block is single-quoted sh: no stray single quotes in new lines.
        new = [l for l in text.splitlines()
               if re.search(r"rowyn-(wifi-regdom|gamemode-power)|90-rowyn-(oomd|weights)|powerdevilrc", l)]
        self.assertTrue(new)
        self.assertFalse(any("'" in l for l in new))


if __name__ == "__main__":
    unittest.main()
