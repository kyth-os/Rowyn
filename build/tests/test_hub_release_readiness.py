#!/usr/bin/env python3
"""Complete-export contracts for the explicit read-only readiness surface."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = os.environ.get("ROWYN_TEST_UPSTREAM_REPO")


@unittest.skipUnless(UPSTREAM, "set ROWYN_TEST_UPSTREAM_REPO for the complete pinned export")
class ReleaseReadinessExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not UPSTREAM:
            raise unittest.SkipTest("set ROWYN_TEST_UPSTREAM_REPO for the complete pinned export")
        cls.fixture = tempfile.TemporaryDirectory(
            prefix="rowyn-release-readiness-", dir=os.environ.get("TMPDIR", str(Path.home() / ".hermes/cache/scratch"))
        )
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.export = Path(cls.fixture.name) / "export"
        result = subprocess.run(
            ["python3", str(ROOT / "build/ubuntu-hub-source.py"), UPSTREAM, str(cls.export)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        if result.returncode:
            raise AssertionError(f"Pinned export failed:\n{result.stdout}\n{result.stderr}")
        cls.web = cls.export / "src/rowyn-hub-web"

    def test_readiness_command_is_registered_exactly_once(self):
        modules = (self.web / "src-tauri/src/commands/mod.rs").read_text()
        main = (self.web / "src-tauri/src/main.rs").read_text()
        self.assertEqual(modules.count("pub(crate) mod rowyn_readiness;"), 1)
        self.assertEqual(main.count("commands::rowyn_readiness::rowyn_readiness_snapshot,"), 1)

    def test_both_existing_pages_mount_the_shipping_component(self):
        for page in ["Dashboard.tsx", "RowynDiagnostics.tsx"]:
            with self.subTest(page=page):
                source = (self.web / "src/pages" / page).read_text()
                self.assertEqual(source.count("<RowynReadiness"), 1)
                self.assertIn("../components/RowynReadiness", source)

    def test_complete_pipeline_copies_the_reviewable_readiness_source(self):
        for relative in [
            "src-tauri/src/commands/rowyn_readiness.rs",
            "src-tauri/src/commands/rowyn_readiness_tests.rs",
            "src/components/RowynReadiness.tsx",
            "src/services/rowynReadiness.ts",
            "src/services/rowynReadinessModel.ts",
            "tests/rowyn-readiness.test.mjs",
        ]:
            with self.subTest(path=relative):
                source = ROOT / "build/hub" / relative
                self.assertTrue(source.is_file(), f"Missing checked-in overlay: {relative}")
                self.assertEqual((self.web / relative).read_bytes(), source.read_bytes())
        import json
        scripts = json.loads((self.web / "package.json").read_text())["scripts"]
        self.assertIn("tests/rowyn-readiness.test.mjs", scripts["test:rowyn-mvp"])
        self.assertIn("tests/rowyn-home-workflow.test.mjs", scripts["test:rowyn-mvp"])


if __name__ == "__main__":
    unittest.main()
