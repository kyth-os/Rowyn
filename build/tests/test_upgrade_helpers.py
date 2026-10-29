#!/usr/bin/env python3
"""Rowyn's update helpers must match its composefs + systemd-boot layout."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SAFE_UPGRADE = ROOT / "shared/rowyn-safe-upgrade.sh"
FINALIZE = ROOT / "shared/rowyn-finalize-staged.sh"


class SafeUpgradeTests(unittest.TestCase):
    def test_only_the_physical_root_space_is_checked(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertIn('check_free /sysroot "$SYSROOT_FREE_MIN_BYTES"', text)
        # /boot is an empty directory on the read-only composefs root, so a
        # /boot free-space check always failed with "0 MiB free".
        self.assertNotIn("check_free /boot", text)
        self.assertNotIn("BOOT_FREE_MIN_BYTES", text)


class FinalizeStagedTests(unittest.TestCase):
    def run_finalize(self, cmdline: str, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="rowyn-finalize-") as temp:
            path = Path(temp) / "cmdline"
            path.write_text(cmdline + "\n", encoding="utf-8")
            env = dict(os.environ, ROWYN_PROC_CMDLINE=str(path))
            return subprocess.run(["bash", str(FINALIZE), *args], env=env,
                                  capture_output=True, text=True, timeout=30)

    COMPOSEFS = ("BOOT_IMAGE=/vmlinuz root=UUID=0000 rw "
                 "rootflags=noatime,compress=zstd:1 composefs=abc123 quiet splash")

    def test_composefs_finalize_defers_to_bootc_at_shutdown(self):
        result = self.run_finalize(self.COMPOSEFS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootc finalizes the staged deployment at shutdown", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_composefs_prepare_boot_is_a_no_op(self):
        result = self.run_finalize(self.COMPOSEFS, "prepare-boot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_composefs_must_be_a_whole_argument(self):
        text = FINALIZE.read_text(encoding="utf-8")
        self.assertIn("(^| )composefs=", text)
        # The ostree path is kept for non-composefs boots.
        self.assertIn("/usr/bin/ostree admin finalize-staged", text)

    def test_usage_errors_are_unchanged(self):
        result = self.run_finalize(self.COMPOSEFS, "bogus")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
