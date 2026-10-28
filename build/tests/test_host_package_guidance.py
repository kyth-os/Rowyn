#!/usr/bin/env python3
"""Behavioral tests for the installed-host package-manager guard."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "shared/host-package-guidance.sh"
GUIDANCE = "usr/share/doc/rowyn/host-package-guidance.txt"


class HostPackageGuidanceTests(unittest.TestCase):
    def make_ubuntu_fixture(self, temporary: str) -> tuple[Path, Path]:
        root = Path(temporary)
        (root / "usr/lib").mkdir(parents=True)
        (root / "usr/lib/os-release").write_text(
            'ID=ubuntu\nVERSION_ID="26.10"\n', encoding="utf-8"
        )
        (root / "etc").mkdir(parents=True)
        (root / "etc/sudoers").write_text(
            'Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"\n',
            encoding="utf-8",
        )
        (root / "usr/bin").mkdir(parents=True)
        log = root / "var/tmp/package-manager-argv.log"
        log.parent.mkdir(parents=True)
        fake = (
            "#!/bin/sh\n"
            "printf '%s\\n' \"$0\" \"$@\" >> \"$FAKE_PACKAGE_MANAGER_LOG\"\n"
            "exit \"${FAKE_PACKAGE_MANAGER_EXIT:-0}\"\n"
        )
        for command in ("apt", "apt-get", "dpkg", "dpkg-query"):
            path = root / "usr/bin" / command
            path.write_text(fake, encoding="utf-8")
            path.chmod(0o755)
        return root, log

    def compose(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(HELPER), "--root", str(root)],
            text=True,
            capture_output=True,
            env=dict(os.environ, PATH="/usr/bin:/bin"),
        )

    def run_command(
        self,
        root: Path,
        command: str,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{root}/usr/sbin:{root}/usr/bin:/usr/bin:/bin",
                "FAKE_PACKAGE_MANAGER_LOG": str(root / "var/tmp/package-manager-argv.log"),
            }
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [command, *args], text=True, capture_output=True, env=env
        )

    def read_log(self, root: Path) -> list[str]:
        return (root / "var/tmp/package-manager-argv.log").read_text().splitlines()

    def test_payload_installs_guidance_after_apt_and_not_in_live_installer(self):
        payload = (ROOT / "Containerfile").read_text()
        live = (ROOT / "build/configure-live.sh").read_text()
        invocation = "/ctx/shared/host-package-guidance.sh --root /"
        self.assertIn(invocation, payload)
        self.assertGreater(payload.index(invocation), payload.rindex("apt-get "))
        self.assertGreater(payload.index(invocation), payload.index("/ctx/shared/bootc-rootfs.sh"))
        self.assertNotIn("host-package-guidance", live)

    def test_image_smoke_exercises_guidance(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("/usr/sbin/apt-get install rowyn-test-package", workflow)
        self.assertIn("grep -Fq Distrobox", workflow)

    def test_composes_idempotently_without_removing_ubuntu_package_binaries(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-host-package-guidance-") as temporary:
            root, _ = self.make_ubuntu_fixture(temporary)
            first = self.compose(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            tracked = [
                root / "usr/sbin/apt",
                root / "usr/sbin/apt-get",
                root / "usr/sbin/dpkg",
                root / GUIDANCE,
            ]
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
            originals = {name: (root / "usr/bin" / name).read_bytes() for name in ("apt", "apt-get", "dpkg")}

            second = self.compose(root)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked})
            self.assertEqual(originals, {name: (root / "usr/bin" / name).read_bytes() for name in originals})
            self.assertTrue(all(path.stat().st_mode & 0o111 for path in tracked[:3]))

    def test_ubuntu_sudo_secure_path_resolves_sbin_wrappers_and_mutations_fail_with_guidance(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-host-package-guidance-apt-") as temporary:
            root, _ = self.make_ubuntu_fixture(temporary)
            self.assertEqual(self.compose(root).returncode, 0)

            self.assertIn("/usr/sbin", (root / "etc/sudoers").read_text(encoding="utf-8"))
            sudo = root / "usr/bin/sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                f"test -n \"$(sed -n 's/^Defaults secure_path=\\\"//p' {root / 'etc/sudoers'} | grep -F /usr/sbin)\"\n"
                f"PATH={root}/usr/sbin:{root}/usr/bin:/usr/bin:/bin; export PATH\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            sudo.chmod(0o755)
            for command, args in (
                ("sudo", ("apt-get", "-y", "install", "ripgrep")),
                ("sudo", ("apt-get", "--", "install", "ripgrep")),
                ("sudo", ("apt", "remove", "old-package")),
                ("apt-get", ("--option", "Dpkg::Use-Pty=0", "update")),
                ("apt", ("full-upgrade",)),
            ):
                result = self.run_command(root, command, *args)
                self.assertNotEqual(result.returncode, 0, args)
                self.assertIn("immutable", result.stderr.lower())
                self.assertIn("discover", result.stderr.lower())
                self.assertIn("distrobox", result.stderr.lower())
                self.assertIn("apt-get", result.stderr.lower())

            log = root / "var/tmp/package-manager-argv.log"
            self.assertFalse(log.exists() and log.read_text().strip())

    def test_dpkg_mutations_are_blocked_but_dpkg_query_and_read_only_dpkg_options_delegate_exact_argv(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-host-package-guidance-dpkg-") as temporary:
            root, _ = self.make_ubuntu_fixture(temporary)
            self.assertEqual(self.compose(root).returncode, 0)

            for args in (
                ("-i", "example.deb"),
                ("--install", "example.deb"),
                ("--unpack", "example.deb"),
                ("--configure", "example"),
                ("--remove", "example"),
                ("--purge", "example"),
                ("--triggers-only", "example"),
                ("--set-selections",),
                ("--add-architecture", "arm64"),
                ("--update-avail",),
            ):
                result = self.run_command(root, "dpkg", *args)
                self.assertNotEqual(result.returncode, 0, args)
                self.assertIn("package state", result.stderr.lower(), args)

            log = root / "var/tmp/package-manager-argv.log"
            self.assertFalse(log.exists() and log.read_text().strip())

            read_only = (
                ("--status", "bash"),
                ("--print-architecture",),
                ("--compare-versions", "1", "lt", "2"),
                ("--audit",),
                ("--verify",),
            )
            for args in read_only:
                result = self.run_command(root, "dpkg", *args)
                self.assertEqual(result.returncode, 0, args)

            query = self.run_command(root, "dpkg-query", "-W", "bash")
            self.assertEqual(query.returncode, 0)
            self.assertEqual(
                self.read_log(root),
                [
                    str(root / "usr/bin/dpkg"), "--status", "bash",
                    str(root / "usr/bin/dpkg"), "--print-architecture",
                    str(root / "usr/bin/dpkg"), "--compare-versions", "1", "lt", "2",
                    str(root / "usr/bin/dpkg"), "--audit",
                    str(root / "usr/bin/dpkg"), "--verify",
                    str(root / "usr/bin/dpkg-query"), "-W", "bash",
                ],
            )

    def test_read_only_apt_options_and_exit_codes_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-host-package-guidance-readonly-") as temporary:
            root, _ = self.make_ubuntu_fixture(temporary)
            self.assertEqual(self.compose(root).returncode, 0)

            result = self.run_command(root, "apt-get", "--version")
            self.assertEqual(result.returncode, 0)
            result = self.run_command(root, "apt", "--help")
            self.assertEqual(result.returncode, 0)

            result = self.run_command(
                root,
                "apt-get",
                "--version",
                extra_env={"FAKE_PACKAGE_MANAGER_EXIT": "23"},
            )
            self.assertEqual(result.returncode, 23)

            self.assertEqual(
                self.read_log(root),
                [
                    str(root / "usr/bin/apt-get"), "--version",
                    str(root / "usr/bin/apt"), "--help",
                    str(root / "usr/bin/apt-get"), "--version",
                ],
            )

    def test_distrobox_choice_is_not_modified_by_host_composition(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-host-package-guidance-distrobox-") as temporary:
            root, _ = self.make_ubuntu_fixture(temporary)
            chosen = root / "home/user/.config/distrobox/containers/ubuntu-dev.conf"
            chosen.parent.mkdir(parents=True)
            chosen.write_text("image=docker.io/library/ubuntu:26.10\n", encoding="utf-8")
            before = chosen.read_bytes()
            self.assertEqual(self.compose(root).returncode, 0)

            self.assertEqual(chosen.read_bytes(), before)
            self.assertFalse((root / "var/usrlocal").exists())
            self.assertFalse((root / "var/home").exists())


if __name__ == "__main__":
    unittest.main()
