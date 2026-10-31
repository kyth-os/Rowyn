#!/usr/bin/env python3
"""Contracts for the Rowyn installer port's legacy junk cut
(build/ubuntu-installer-source.py).

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_KYTH_REPO to a local Kyth checkout
that contains the pinned commit; the port integration tests skip otherwise.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
DIGEST = "sha256:" + "b" * 64
# The upstream executor fixtures pin the all-zero digest as the ISO
# release digest; the cargo-suite export must use it so the ported
# ROWYN_RELEASE_SOURCE constant matches the fixtures' references.
CARGO_TEST_DIGEST = "sha256:" + "0" * 64


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_module("ubuntu_installer_source", "build/ubuntu-installer-source.py")
hub = load_module("ubuntu_hub_source", "build/ubuntu-hub-source.py")


class InstallerJunkCutContractTests(unittest.TestCase):
    def test_shared_crate_cut_matches_hub_port(self):
        # Both ports carry the shared-crate cut; the constants must stay
        # in step or the two exports of the same crate would diverge.
        self.assertEqual(installer.SHARED_CRATE_BIN_FILES, hub.SHARED_CRATE_BIN_FILES)
        self.assertEqual(installer.SHARED_CRATE_BIN_SECTIONS, hub.SHARED_CRATE_BIN_SECTIONS)
        self.assertEqual(installer.SHARED_CRATE_KEEP_MODULES, hub.SHARED_CRATE_KEEP_MODULES)
        self.assertEqual(len(installer.SHARED_CRATE_KEEP_MODULES), 58)

    def test_port_script_carries_junk_cut(self):
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        for anchor in ("strip_shared_crate_bins", "prune_shared_crate",
                       "sync_cargo_lock", '"cargo", "fetch"',
                       "kyth-installer-native", "native_main.rs",
                       'slint-build = "1.16.0"', 'slint = "1.16.0"',
                       "slint_build::compile"):
            self.assertIn(anchor, script)
        # Item 8: the dead installer_source rewrite is gone from the port.
        self.assertNotIn("install_images", script)
        self.assertNotIn("installer_source.rs", script)

    def test_port_script_carries_test_debt_fix(self):
        # The ported tree's upstream tests are adapted to Rowyn's
        # deliberate behavior (MOK rejection, the /api/disk/* gate, the
        # Prepare release gate) so `cargo test` on the export is a real
        # signal; the one live-ISO-only success path is gated visibly.
        script = (ROOT / "build/ubuntu-installer-source.py").read_text()
        for anchor in (
            "rejects_import_plans_without_handling_the_password",
            "disk_api_gate_refuses_create_and_keeps_pending_empty",
            "disk_api_gate_fails_closed_for_unsafe_and_unsupported_requests",
            "disk_api_gate_refuses_pending_remove_and_rollback",
            "preparation_refuses_a_non_release_source_before_preparing",
            '#[ignore = "requires the live ISO environment',
            '#[ignore = "load-sensitive socket-deadline timing',
            '#[ignore = "load-sensitive cross-thread timing',
            'contains("source is not this Rowyn release")',
            "Rowyn does not support custom kernels or Kyth MOK enrollment",
        ):
            self.assertIn(anchor, script)


def _pinned_repo():
    repo = os.environ.get("ROWYN_TEST_KYTH_REPO")
    if not repo:
        return None
    result = subprocess.run(
        ["git", "-C", repo, "rev-parse", PIN + "^{commit}"],
        text=True, capture_output=True)
    if result.returncode != 0 or result.stdout.strip() != PIN:
        return None
    return Path(repo)


@unittest.skipUnless(_pinned_repo(), "set ROWYN_TEST_KYTH_REPO to a Kyth checkout containing the pinned commit")
class PinnedInstallerJunkCutTests(unittest.TestCase):
    def test_junk_cut_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-junk-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            crate = root / "src/kyth-installer-web/src-tauri"
            shared = root / "src/kyth-shared-rs"

            # Item 3: the Slint stack is out — bin target, source,
            # dependencies, and the build.rs compile step — while the
            # three shipped bins stay declared.
            self.assertFalse((crate / "src/native_main.rs").exists())
            manifest = (crate / "Cargo.toml").read_text()
            self.assertNotIn("kyth-installer-native", manifest)
            self.assertNotIn("slint", manifest)
            self.assertNotIn("slint", (crate / "build.rs").read_text())
            for kept_bin in ("kyth-installer-shell", "kyth-installerd",
                             "kyth-installer-exec"):
                self.assertIn(f'name = "{kept_bin}"', manifest)

            # Items 1 + 7 + 8: shared crate pruned to the keep set, no
            # bin sources/sections, no pgp, and the dead
            # installer_source module is gone rather than rewritten.
            self.assertEqual(list((shared / "src").glob("*_bin.rs")), [])
            shared_manifest = (shared / "Cargo.toml").read_text()
            self.assertNotIn("[[bin]]", shared_manifest)
            self.assertNotIn("pgp", shared_manifest)
            self.assertFalse((shared / "src/system/installer_source.rs").exists())
            remaining = sorted(
                str(p.relative_to(shared / "src"))
                for p in (shared / "src").rglob("*.rs"))
            keep = set(installer.SHARED_CRATE_KEEP_MODULES)
            expected = sorted(
                ["lib.rs", "system/mod.rs"]
                + [f"{name}.rs" for name in keep if "::" not in name and name != "system"]
                + [f"system/{name.rsplit('::', 1)[-1]}.rs"
                   for name in keep if name.startswith("system::")])
            self.assertEqual(remaining, expected)
            self.assertTrue((shared / "src/system/installer_validation.rs").is_file())
            self.assertTrue((shared / "src/transfer.rs").is_file())

            # The lock was re-synced by cargo: none of the removed
            # packages remain.
            lock = (crate / "Cargo.lock").read_text()
            lock_names = {block.split('"')[1] for block in lock.split("[[package]]")[1:]
                          if 'name = "' in block}
            for gone in ("pgp", "slint", "slint-build"):
                self.assertNotIn(gone, lock_names)

    def test_test_debt_adaptations_applied(self):
        repo = _pinned_repo()
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-tests-") as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, DIGEST)
            src = root / "src/kyth-installer-web/src-tauri/src"

            secure = (src / "installer_secure_boot.rs").read_text()
            self.assertIn("rejects_import_plans_without_handling_the_password", secure)
            self.assertNotIn("plans_import_without_handling_the_password", secure)
            self.assertNotIn("MOK plan should validate", secure)
            self.assertNotIn("fixture input should validate", secure)

            acceptance = (src / "installer_acceptance.rs").read_text()
            self.assertIn("Rowyn must reject MOK import plans", acceptance)

            daemon = (src / "installer_daemon.rs").read_text()
            for adapted in ("disk_api_gate_refuses_create_and_keeps_pending_empty",
                            "disk_api_gate_fails_closed_for_unsafe_and_unsupported_requests",
                            "disk_api_gate_refuses_pending_remove_and_rollback"):
                self.assertIn(adapted, daemon)
            for upstream in ("native_journal_routes_preserve_pending_metadata_without_python",
                             "native_journal_routes_fail_closed_for_unsafe_and_unsupported_requests",
                             "native_journal_pending_remove_and_rollback_clear_state"):
                self.assertNotIn(upstream, daemon)

            executor = (src / "installer_job_executor.rs").read_text()
            self.assertIn("preparation_refuses_a_non_release_source_before_preparing", executor)
            self.assertNotIn("preparation_persists_a_recoverable_native_transaction", executor)
            self.assertIn('#[ignore = "requires the live ISO environment', executor)
            self.assertIn('contains("source is not this Rowyn release")', executor)
            self.assertIn('#[ignore = "load-sensitive socket-deadline timing', daemon)
            job = (src / "installer_job.rs").read_text()
            self.assertIn('#[ignore = "load-sensitive cross-thread timing', job)


@unittest.skipUnless(_pinned_repo() and shutil.which("cargo"),
                     "set ROWYN_TEST_KYTH_REPO to a Kyth checkout containing the pinned commit and provide cargo")
class PinnedInstallerCargoTests(unittest.TestCase):
    def test_installer_cargo_suite_is_green(self):
        repo = _pinned_repo()
        # The debug target dir is ~3G; /tmp is a small tmpfs on the dev
        # VM, so the scratch tree lives on the home filesystem.
        scratch = Path.home() / ".cache" / "rowyn-test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="rowyn-installer-cargo-", dir=scratch) as temp:
            root = Path(temp) / "source"
            installer.export_source(repo, root)
            installer.port_source(root, CARGO_TEST_DIGEST)
            crate = root / "src/kyth-installer-web/src-tauri"
            # The shell binary's tauri::generate_context! requires the
            # frontend dist to exist even for unit tests; the ISO build
            # produces the real one with npm, the suite uses a stub.
            dist = root / "src/kyth-installer-web/dist"
            dist.mkdir(exist_ok=True)
            (dist / "index.html").write_text("<html></html>\n")
            env = dict(os.environ)
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            env["PATH"] = cargo_bin + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                ["cargo", "test", "--locked", "--no-fail-fast"],
                cwd=crate, env=env, text=True, capture_output=True,
                timeout=3600)
            output = result.stdout + result.stderr
            # Keep the full log outside the temp tree for diagnosis.
            log_path = scratch / "last-installer-cargo-test.log"
            log_path.write_text(output)
            failed_names = re.findall(r"^test (\S+) \.\.\. FAILED$", output, re.M)
            summary = (
                f"cargo log: {log_path}\nfailed tests: {failed_names}\n"
                + "\n".join(
                    line for line in output.splitlines()
                    if line.startswith("test result:")))
            totals = re.findall(
                r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored",
                output)
            self.assertTrue(totals, summary)
            self.assertEqual(sum(int(t[1]) for t in totals), 0, summary)
            self.assertEqual(result.returncode, 0, summary)
            # The adaptations actually ran (per target binary), and the
            # live-ISO cosign success path is visibly gated, not deleted.
            for marker in (
                "installer_secure_boot::tests::rejects_import_plans_without_handling_the_password ... ok",
                "installer_secure_boot::tests::classifies_existing_states_and_non_custom_kernel ... ok",
                "installer_secure_boot::tests::matches_shared_decision_fixture ... ok",
                "installer_acceptance::tests::bootc_and_secure_boot_staging_are_explicit_in_acceptance ... ok",
                "installer_daemon::tests::disk_api_gate_refuses_create_and_keeps_pending_empty ... ok",
                "installer_daemon::tests::disk_api_gate_fails_closed_for_unsafe_and_unsupported_requests ... ok",
            "installer_daemon::tests::disk_api_gate_refuses_pending_remove_and_rollback ... ok",
                "installer_job_executor::tests::image_phase_refuses_unpinned_docker_source ... ok",
                "installer_job_executor::tests::preparation_refuses_a_non_release_source_before_preparing ... ok",
                "installer_job_executor::tests::image_phase_verifies_digest_pinned_docker_source ... ignored",
                "installer_daemon::tests::slow_trickled_request_cannot_extend_the_absolute_read_deadline ... ignored",
                "installer_job::tests::cancellation_during_phase_waits_for_executor_cleanup ... ignored",
            ):
                self.assertIn(marker, output, marker)


if __name__ == "__main__":
    unittest.main()
