"""Fresh/upgrade fixtures for service identity and journald-only policy."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/service-accounts-and-logging.sh"
SYSUSERS = shutil.which("systemd-sysusers")


class ServiceAccountPolicyTests(unittest.TestCase):
    def run_policy(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(HELPER), "--root", str(root)],
            text=True, capture_output=True,
        )

    def run_sysusers(self, root: Path) -> subprocess.CompletedProcess[str]:
        if SYSUSERS is None:
            self.skipTest("systemd-sysusers not installed")
        return subprocess.run(
            [SYSUSERS, f"--root={root}"], text=True, capture_output=True,
        )

    def test_fresh_provisioning_creates_cups_identity_and_journald_policy(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-accounts-fresh-") as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_policy(root).returncode, 0)
            result = self.run_sysusers(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            passwd = (root / "etc/passwd").read_text()
            groups = (root / "etc/group").read_text()
            cups = next(line for line in passwd.splitlines() if line.startswith("cups-browsed:"))
            self.assertEqual(cups.split(":")[5], "/")
            self.assertRegex(cups, r"^cups-browsed:x:[0-9]+:[0-9]+:")
            self.assertRegex(groups, r"(?m)^cups-browsed:x:[0-9]+:$")
            self.assertFalse(any(line.startswith("syslog:") for line in passwd.splitlines()))
            for unit in ("rsyslog.service", "syslog.socket"):
                self.assertTrue((root / "etc/systemd/system" / unit).is_symlink())
                self.assertEqual(os.readlink(root / "etc/systemd/system" / unit), "/dev/null")
            self.assertTrue((root / "usr/lib/sysusers.d/rowyn-cups-browsed.conf").is_file())

    def test_upgrade_preserves_existing_users_groups_uids_and_interactive_user(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-accounts-upgrade-") as tmp:
            root = Path(tmp)
            (root / "etc").mkdir()
            (root / "etc/passwd").write_text(
                "operator:x:1002:1002:Operator:/home/operator:/bin/bash\n"
                "cups-browsed:x:234:245:Existing print service:/var/empty:/usr/sbin/nologin\n"
            )
            (root / "etc/group").write_text(
                "operator:x:1002:\ncups-browsed:x:245:operator\n"
            )
            (root / "etc/shadow").write_text("operator:!:1::::::\ncups-browsed:!:1::::::\n")
            self.assertEqual(self.run_policy(root).returncode, 0)
            result = self.run_sysusers(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "etc/passwd").read_text().splitlines(), [
                "operator:x:1002:1002:Operator:/home/operator:/bin/bash",
                "cups-browsed:x:234:245:Existing print service:/var/empty:/usr/sbin/nologin",
            ])
            self.assertEqual((root / "etc/group").read_text().splitlines(), [
                "operator:x:1002:", "cups-browsed:x:245:operator",
            ])
            self.assertEqual((root / "etc/shadow").read_text().splitlines(), [
                "operator:!:1::::::", "cups-browsed:!:1::::::",
            ])
            before = {p: p.read_bytes() for p in (root / "etc/passwd", root / "etc/group")}
            again = self.run_sysusers(root)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_upgrade_unit_provisions_the_fixed_identity_before_cups(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-accounts-ordering-") as tmp:
            root = Path(tmp)
            (root / "etc").mkdir()
            account = "operator:x:1002:1002:Operator:/home/operator:/bin/bash\n"
            (root / "etc/passwd").write_text(account)
            (root / "etc/group").write_text("operator:x:1002:\n")
            self.assertEqual(self.run_policy(root).returncode, 0)
            unit = (root / "usr/lib/systemd/system/rowyn-cups-account.service").read_text()
            ordering = (root / "usr/lib/systemd/system/cups-browsed.service.d/20-rowyn-account.conf").read_text()
            self.assertIn("Type=oneshot", unit)
            self.assertIn("User=root", unit)
            self.assertIn("ExecStart=/usr/bin/systemd-sysusers rowyn-cups-browsed.conf", unit)
            self.assertNotIn("ConditionNeedsUpdate", unit)
            self.assertIn("Requires=rowyn-cups-account.service", ordering)
            self.assertIn("After=rowyn-cups-account.service", ordering)
            result = self.run_sysusers(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            passwd = (root / "etc/passwd").read_text()
            self.assertIn(account, passwd)
            self.assertIn("cups-browsed:", passwd)

    def test_image_installs_declarations_before_rootfs_normalization(self):
        source = (ROOT / "Containerfile").read_text()
        call = "/ctx/shared/service-accounts-and-logging.sh --root /"
        self.assertIn(call, source)
        self.assertLess(source.index(call), source.index("/ctx/shared/bootc-rootfs.sh"))

    def test_existing_logging_opt_in_and_journald_retention_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-accounts-optin-") as tmp:
            root = Path(tmp)
            service_dir = root / "etc/systemd/system"
            service_dir.mkdir(parents=True)
            override = service_dir / "rsyslog.service"
            override.write_text("[Service]\nExecStart=/usr/sbin/rsyslogd\n")
            journal_dir = root / "usr/lib/systemd/journald.conf.d"
            journal_dir.mkdir(parents=True)
            retention = journal_dir / "90-rowyn.conf"
            retention.write_text("[Journal]\nSystemMaxUse=200M\nMaxRetentionSec=2week\nForwardToSyslog=no\n")
            self.assertEqual(self.run_policy(root).returncode, 0)
            self.assertFalse(override.is_symlink())
            self.assertFalse((service_dir / "syslog.socket").is_symlink(), "preserve the complete explicit logging opt-in, not just its service file")
            self.assertEqual(override.read_text(), "[Service]\nExecStart=/usr/sbin/rsyslogd\n")
            self.assertEqual(retention.read_text(), "[Journal]\nSystemMaxUse=200M\nMaxRetentionSec=2week\nForwardToSyslog=no\n")

    def test_dropin_directory_is_preserved_as_explicit_logging_opt_in(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-accounts-dropin-dir-") as tmp:
            root = Path(tmp)
            dropins = root / "etc/systemd/system/rsyslog.service.d"
            dropins.mkdir(parents=True)
            self.assertEqual(self.run_policy(root).returncode, 0)
            self.assertTrue(dropins.is_dir())
            self.assertFalse((root / "etc/systemd/system/rsyslog.service").is_symlink())
            self.assertFalse((root / "etc/systemd/system/syslog.socket").is_symlink())

    def test_policy_is_root_scoped_and_does_not_add_state_directory_guesses(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-accounts-policy-") as tmp:
            root = Path(tmp)
            self.assertEqual(self.run_policy(root).returncode, 0)
            self.assertFalse((root / "var").exists())
            self.assertFalse((root / "etc/passwd").exists())
            self.assertTrue((root / "etc/systemd/system/rsyslog.service").is_symlink())
            self.assertTrue((root / "etc/systemd/system/syslog.socket").is_symlink())


if __name__ == "__main__":
    unittest.main()
