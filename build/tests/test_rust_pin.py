import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RustPinTests(unittest.TestCase):
    def test_containerfile_and_fetch_assets_agree_and_checksum_is_committed(self):
        fetch = (ROOT / "build/fetch-assets.sh").read_text()
        version = re.search(r'^RUST_VERSION="([^"$]+)"$', fetch, re.M)
        sha = re.search(r'^RUST_SHA256="([0-9a-f]{64})"$', fetch, re.M)
        self.assertIsNotNone(version, "RUST_VERSION must be a fixed literal")
        self.assertIsNotNone(sha, "RUST_SHA256 must be a 64-hex literal")
        arg = re.search(r"^ARG RUST_VERSION=(\S+)$", (ROOT / "Containerfile").read_text(), re.M)
        self.assertIsNotNone(arg)
        self.assertEqual(arg[1], version[1])
        self.assertNotIn(".sha256", fetch)


if __name__ == "__main__":
    unittest.main()
