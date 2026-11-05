"""Immutable build provenance is explicit, validated, and safe for reports."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BuildMetadataTests(unittest.TestCase):
    def test_valid_source_revision_is_preserved_and_invalid_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            revision = "0123456789abcdef" * 2 + "01234567"
            command = ["python3", str(ROOT / "shared/write-build-info.py"), "--root", temp]
            good = subprocess.run(command + ["--source-revision", revision, "--image-version", "0.1"], capture_output=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            path = root / "usr/share/rowyn/build-info.json"
            self.assertEqual(json.loads(path.read_text()), {"source_revision": revision, "image_version": "0.1"})
            bad = subprocess.run(command + ["--source-revision", "not-a-commit"], capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertEqual(json.loads(path.read_text())["source_revision"], revision)

    def test_local_build_never_invents_a_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(["python3", str(ROOT / "shared/write-build-info.py"), "--root", temp], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads((Path(temp) / "usr/share/rowyn/build-info.json").read_text())
            self.assertIsNone(value["source_revision"])


if __name__ == "__main__":
    unittest.main()
