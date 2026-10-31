#!/usr/bin/env python3
"""Contracts for the Rowyn installer port's legacy junk cut
(build/ubuntu-installer-source.py).

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_KYTH_REPO to a local Kyth checkout
that contains the pinned commit; the port integration tests skip otherwise.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
DIGEST = "sha256:" + "b" * 64


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


if __name__ == "__main__":
    unittest.main()
