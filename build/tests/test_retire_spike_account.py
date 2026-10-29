#!/usr/bin/env python3
"""Coverage for retiring the development spike account on installed systems."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/retire-spike-account.sh"
CONTAINERFILE = ROOT / "Containerfile"
SPIKE_PUB = (ROOT / "shared/retired-dev-key.pub").read_text()
OTHER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherKeyOtherKeyOtherKeyOtherKeyOtherKey1 laptop\n"
NEEDS_ROOT = unittest.skipUnless(os.geteuid() == 0, "usermod/gpasswd --root require root")


class RetireSpikeAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, rel: str, text: str, mode: int = 0o644) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def make_system(self, *, with_rowyn: bool = True, keys: str = "") -> None:
        passwd = "root:x:0:0:root:/root:/bin/bash\n"
        shadow = "root:*:19000:0:99999:7:::\n"
        group = "root:x:0:\nsudo:x:27:alice\n"
        gshadow = "root:*::\nsudo:*::alice\n"
        if with_rowyn:
            passwd += "rowyn:x:1000:1000::/var/home/rowyn:/bin/bash\n"
            shadow += "rowyn:$6$salt$hash:19000:0:99999:7:::\n"
            group += "rowyn:x:1000:\n"
            gshadow += "rowyn:!::\n"
            group = group.replace("sudo:x:27:alice", "sudo:x:27:alice,rowyn")
            gshadow = gshadow.replace("sudo:*::alice", "sudo:*::alice,rowyn")
        self.write("etc/passwd", passwd)
        self.write("etc/shadow", shadow, 0o640)
        self.write("etc/group", group)
        self.write("etc/gshadow", gshadow, 0o640)
        self.write("usr/share/rowyn/retired-dev-key.pub", SPIKE_PUB)
        if keys:
            self.write("var/home/rowyn/.ssh/authorized_keys", keys, 0o600)

    def snapshot(self) -> dict[str, str]:
        return {
            str(p.relative_to(self.root)): p.read_text()
            for p in sorted(self.root.rglob("*"))
            if p.is_file()
        }

    def run_helper(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(self.root)],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/sbin:/usr/bin:/sbin:/bin"),
        )

    def shadow_field(self, index: int) -> str:
        for line in (self.root / "etc/shadow").read_text().splitlines():
            if line.startswith("rowyn:"):
                return line.split(":")[index]
        self.fail("rowyn missing from shadow")

    def test_helper_is_executable_and_strict(self) -> None:
        self.assertTrue(os.access(HELPER, os.X_OK))
        self.assertIn("set -euo pipefail", HELPER.read_text())

    def test_no_rowyn_user_changes_nothing(self) -> None:
        self.make_system(with_rowyn=False)
        before = self.snapshot()
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_rowyn_without_spike_key_changes_nothing(self) -> None:
        self.make_system(keys=OTHER_KEY)
        before = self.snapshot()
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.snapshot(), before)

    def test_rowyn_without_authorized_keys_changes_nothing(self) -> None:
        self.make_system()
        before = self.snapshot()
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.snapshot(), before)

    @NEEDS_ROOT
    def test_spike_account_is_retired_and_second_run_is_noop(self) -> None:
        spike_line = SPIKE_PUB.strip() + "\n"
        optioned = 'command="/bin/true",no-pty ' + SPIKE_PUB.strip() + " renamed\n"
        self.make_system(keys=OTHER_KEY + spike_line + "# note\n" + optioned)
        sudoers = self.write("etc/sudoers.d/90-rowyn-spike", "rowyn ALL=(ALL) NOPASSWD: ALL\n", 0o440)

        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip())

        keys = self.root / "var/home/rowyn/.ssh/authorized_keys"
        self.assertEqual(keys.read_text(), OTHER_KEY + "# note\n")
        self.assertEqual(keys.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.shadow_field(1).startswith("!"))
        self.assertEqual(self.shadow_field(7), "1")
        group = (self.root / "etc/group").read_text()
        self.assertRegex(group, r"(?m)^sudo:x:27:alice$")
        self.assertFalse(sudoers.exists())
        self.assertIn("rowyn:x:1000", (self.root / "etc/passwd").read_text())
        self.assertTrue((self.root / "var/home/rowyn").is_dir())

        after = self.snapshot()
        second = self.run_helper()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "")
        self.assertEqual(self.snapshot(), after)


class ContainerfileRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CONTAINERFILE.read_text()

    def test_ssh_disabled_and_hardened(self) -> None:
        self.assertNotRegex(self.text, r"systemctl enable systemd-resolved ssh")
        self.assertIn("systemctl disable ssh.service ssh.socket", self.text)
        for line in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "PermitRootLogin no"):
            self.assertIn(line, self.text)
        self.assertIn("/etc/ssh/sshd_config.d/10-rowyn.conf", self.text)

    def test_retirement_migration_is_installed_and_enabled(self) -> None:
        for needle in (
            "/usr/libexec/rowyn-retire-spike-account",
            "/usr/share/rowyn/retired-dev-key.pub",
            "systemctl enable rowyn-retire-spike-account.service",
        ):
            self.assertIn(needle, self.text)
        self.assertNotIn("ROWYN_DEV_ACCESS", self.text)


if __name__ == "__main__":
    unittest.main()
