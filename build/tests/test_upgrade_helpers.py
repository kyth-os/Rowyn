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
    def test_staging_targets_the_destination_stable_channel(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertIn("readonly UPDATE_REF=ghcr.io/mrtrickdotorg/rowyn:stable", text)
        self.assertIn('switch) args=(switch "$UPDATE_REF")', text)
        self.assertIn('upgrade) args=(upgrade)', text)
        self.assertIn("mrtrickdotorg/rowyn", text)
        self.assertNotIn("/usr/bin/bootc upgrade --progress-fd", text)

    def test_manual_switch_targets_the_destination_stable_channel(self):
        text = (ROOT / "shared/rowyn-bootc-guard.sh").read_text(encoding="utf-8")
        self.assertIn("readonly STABLE_REF=ghcr.io/mrtrickdotorg/rowyn:stable", text)
        self.assertIn('bootc switch "$STABLE_REF"', text)
        self.assertIn('/usr/bin/bootc upgrade', text)

    def test_only_the_physical_root_space_is_checked(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertIn('check_free /sysroot "$SYSROOT_FREE_MIN_BYTES"', text)
        # /boot is an empty directory on the read-only composefs root, so a
        # /boot free-space check always failed with "0 MiB free".
        self.assertNotIn("check_free /boot", text)
        self.assertNotIn("BOOT_FREE_MIN_BYTES", text)


class BootcGuardOperationTests(unittest.TestCase):
    FAKE_BOOTC = """#!/bin/sh
case "$1" in
status)
    printf '{"status":{"booted":{"image":{"image":{"image":"%s"}}}}}' "$BOOTED_IMAGE_REF"
    ;;
switch|upgrade)
    printf '%s' "$*" >> "$BOOTC_CALLS"
    ;;
*) exit 9 ;;
esac
"""

    def run_guard(self, image_ref):
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-guard-") as temp:
            base = Path(temp)
            bootc = base / "bootc"
            bootc.write_text(self.FAKE_BOOTC, encoding="utf-8")
            bootc.chmod(0o755)
            finalize = base / "finalize"
            finalize.write_text("#!/bin/sh\necho \"$*\" >> \"$FINALIZE_CALLS\"\nexit 0\n",
                                encoding="utf-8")
            finalize.chmod(0o755)
            calls = base / "bootc-calls"
            finalizer_calls = base / "finalizer-calls"
            text = (ROOT / "shared/rowyn-bootc-guard.sh").read_text(encoding="utf-8")
            text = text.replace("/usr/bin/bootc", str(bootc))
            text = text.replace("/run/rowyn-bootc.lock", str(base / "lock"))
            text = text.replace("/usr/libexec/rowyn-finalize-staged", str(finalize))
            script = base / "guard"
            script.write_text(text, encoding="utf-8")
            env = dict(os.environ, BOOTED_IMAGE_REF=image_ref,
                       BOOTC_CALLS=str(calls), FINALIZE_CALLS=str(finalizer_calls))
            result = subprocess.run(["bash", str(script), "switch-latest"],
                                    capture_output=True, text=True, timeout=30, env=env)
            return result, calls.read_text(encoding="utf-8") if calls.exists() else ""

    def test_old_registry_booted_image_switches_to_destination(self):
        result, calls = self.run_guard("ghcr.io/kyth-os/rowyn:stable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("switch ghcr.io/mrtrickdotorg/rowyn:stable", calls)
        self.assertNotIn("upgrade", calls)

    def test_destination_booted_image_uses_upgrade_to_avoid_switch_collision(self):
        result, calls = self.run_guard("ghcr.io/mrtrickdotorg/rowyn:stable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.strip(), "upgrade")
        self.assertNotIn("switch", calls)

    def test_unrecognized_booted_image_fails_closed(self):
        result, calls = self.run_guard("ghcr.io/attacker/rowyn:stable")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, "")


class SafeUpgradeProgressPipeTests(unittest.TestCase):
    """bootc's --progress-fd must be a real pipe ("not a pipe" otherwise).

    Runs the real script, with its fixed paths rewritten into a temp dir,
    against a fake bootc that enforces what the real one does.
    """

    FAKE_BOOTC = """#!/bin/sh
case "$1" in
switch|upgrade)
    if [ "$1" = switch ]; then
        [ "$BOOTED_IMAGE_REF" = "ghcr.io/kyth-os/rowyn:stable" ] || exit 9
        found=0
        for arg in "$@"; do
            [ "$arg" = "ghcr.io/mrtrickdotorg/rowyn:stable" ] && found=1
        done
        [ "$found" = 1 ] || exit 9
    else
        [ "$BOOTED_IMAGE_REF" = "ghcr.io/mrtrickdotorg/rowyn:stable" ] || exit 9
    fi
    for arg in "$@"; do
        if [ "$arg" = "--progress-fd" ]; then
            if [ ! -p /dev/fd/3 ]; then
                echo "error: Upgrading composefs: not a pipe" >&2
                exit 1
            fi
            echo '{"type":"ProgressBytes","task":"pulling","bytes":50,"bytesTotal":100,"steps":0,"stepsTotal":2}' >&3
        fi
    done
    echo "Staged update"
    ;;
status)
    printf '{"status":{"booted":{"image":{"image":{"image":"%s"}}},"staged":{"image":{}}}}\n' "$BOOTED_IMAGE_REF"
    ;;
esac
"""

    def run_script(self, fake_bootc: str, booted_image_ref="ghcr.io/kyth-os/rowyn:stable"):
        with tempfile.TemporaryDirectory(prefix="rowyn-safe-upgrade-") as temp:
            temp_path = Path(temp)
            bootc = temp_path / "bootc"
            bootc.write_text(fake_bootc, encoding="utf-8")
            bootc.chmod(0o755)
            finalize = temp_path / "finalize"
            finalize.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            finalize.chmod(0o755)
            text = SAFE_UPGRADE.read_text(encoding="utf-8")
            text = text.replace("if (( EUID != 0 )); then", "if false; then")
            text = text.replace("/usr/bin/bootc", str(bootc))
            text = text.replace("/run/rowyn-bootc.lock", str(temp_path / "lock"))
            text = text.replace("/usr/libexec/rowyn-finalize-staged", str(finalize))
            script = temp_path / "rowyn-safe-upgrade"
            script.write_text(text, encoding="utf-8")
            env = dict(os.environ, BOOTED_IMAGE_REF=booted_image_ref)
            return subprocess.run(["bash", str(script)], capture_output=True,
                                  text=True, timeout=60, env=env)

    def test_progress_fd_is_a_pipe_and_progress_is_streamed(self):
        result = self.run_script(self.FAKE_BOOTC)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("not a pipe", result.stderr)
        # 85 * 50 / 100 = 42: the byte progress written to the pipe reached
        # the Hub's marker stream.
        self.assertIn("ROWYN_STAGE_PROGRESS pct=42", result.stdout)

    def test_destination_image_uses_upgrade_not_switch(self):
        result = self.run_script(self.FAKE_BOOTC,
                                 "ghcr.io/mrtrickdotorg/rowyn:stable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ROWYN_STAGE_PROGRESS pct=42", result.stdout)

    def test_falls_back_without_progress_when_the_fd_is_rejected(self):
        always_rejects = self.FAKE_BOOTC.replace(
            'if [ ! -p /dev/fd/3 ]; then',
            'if true; then',
        )
        result = self.run_script(always_rejects)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ROWYN_STAGE_PROGRESS pct=42", result.stdout)

    def test_script_never_redirects_fd3_to_a_regular_file(self):
        text = SAFE_UPGRADE.read_text(encoding="utf-8")
        self.assertNotIn('3>"$workdir/bootc.progress"', text)
        self.assertIn('3> >(cat >>"$workdir/bootc.progress")', text)


class FinalizeStagedTests(unittest.TestCase):
    def run_finalize(self, cmdline: str, *args: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="rowyn-finalize-") as temp:
            path = Path(temp) / "cmdline"
            path.write_text(cmdline + "\n", encoding="utf-8")
            env = dict(os.environ, ROWYN_PROC_CMDLINE=str(path))
            return subprocess.run(["bash", str(FINALIZE), *args], env=env,
                                  capture_output=True, text=True, timeout=30)

    # bootc 1.17.3 defaults to EROFS format V1, whose karg is composefs.digest=.
    COMPOSEFS = ("BOOT_IMAGE=/vmlinuz root=UUID=0000 rw rootflags=noatime,compress=zstd:1 "
                 "composefs.digest=v1-sha512-12:" + "f" * 128 + " quiet splash")
    COMPOSEFS_V2 = "root=UUID=0000 rw composefs=abc123 quiet"

    def test_v2_composefs_karg_is_detected_too(self):
        result = self.run_finalize(self.COMPOSEFS_V2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootc finalizes the staged deployment at shutdown", result.stdout)

    def test_similar_kargs_are_not_mistaken_for_composefs(self):
        text = FINALIZE.read_text(encoding="utf-8")
        self.assertIn("(^| )composefs(\\.digest)?=", text)

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
        # The ostree path is kept for non-composefs boots.
        self.assertIn("/usr/bin/ostree admin finalize-staged", text)

    def test_usage_errors_are_unchanged(self):
        result = self.run_finalize(self.COMPOSEFS, "bogus")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
