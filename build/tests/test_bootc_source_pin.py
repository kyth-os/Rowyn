"""Behavioral tests for the offline bootc source fetch contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FETCH_SCRIPT = ROOT / "build/fetch-assets.sh"
VALIDATE_SCRIPT = ROOT / "build/validate.sh"
BOOTC_COMMIT = "6d7893f2dbdd289a4a04a5727e4df29433d43024"


MOCK_GIT = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

log = Path(os.environ["MOCK_LOG"])
args = sys.argv[1:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(["git", *args]) + "\n")

if args[:1] == ["-C"]:
    worktree = Path(args[1])
    command = args[2:]
else:
    worktree = None
    command = args

if command[:1] == ["init"]:
    (worktree / ".git").mkdir(parents=True, exist_ok=True)
elif command[:2] == ["remote", "add"]:
    pass
elif command[:1] == ["fetch"]:
    pass
elif command[:2] == ["checkout", "--detach"]:
    pass
elif command[:2] == ["rev-parse", "--verify"]:
    print(os.environ["MOCK_HEAD"])
else:
    raise SystemExit(f"unexpected mocked git command: {command!r}")
'''


MOCK_CURL = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with Path(os.environ["MOCK_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write("curl " + " ".join(args) + "\n")
target = Path(args[args.index("-o") + 1])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("rust fixture\n", encoding="utf-8")
'''


MOCK_SHA256SUM = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys

with Path(os.environ["MOCK_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write("sha256sum " + " ".join(sys.argv[1:]) + "\n")
'''


MOCK_CARGO = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys

with Path(os.environ["MOCK_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write("cargo " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1:3] != ["vendor", "vendor"]:
    raise SystemExit(f"unexpected mocked cargo command: {sys.argv[1:]!r}")
Path("vendor").mkdir()
print('[source.crates-io]')
print('replace-with = "vendored-sources"')
'''


MOCK_DU = r'''#!/usr/bin/env python3
print("build-assets fixture")
'''


class BootcSourcePinTests(unittest.TestCase):
    def _fixture(self, head: str):
        temp = tempfile.TemporaryDirectory(prefix="rowyn-bootc-pin-")
        root = Path(temp.name)
        (root / "build").mkdir()
        script = root / "build/fetch-assets.sh"
        shutil.copy2(FETCH_SCRIPT, script)
        script.chmod(0o755)
        mock_bin = root / "mock-bin"
        mock_bin.mkdir()
        for name, content in {
            "git": MOCK_GIT,
            "curl": MOCK_CURL,
            "sha256sum": MOCK_SHA256SUM,
            "cargo": MOCK_CARGO,
            "du": MOCK_DU,
        }.items():
            path = mock_bin / name
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755)
        log = root / "commands.jsonl"
        env = dict(
            os.environ,
            PATH=f"{mock_bin}:{os.environ['PATH']}",
            MOCK_LOG=str(log),
            MOCK_HEAD=head,
            HOME=str(root / "home"),
        )
        return temp, root, script, env, log

    def test_exact_commit_is_fetched_verified_and_vendored_without_network(self):
        temp, root, script, env, log = self._fixture(BOOTC_COMMIT)
        try:
            result = subprocess.run(
                ["bash", str(script)],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(line.startswith("curl ") for line in calls))
            self.assertIn(
                json.dumps(
                    [
                        "git",
                        "-C",
                        "build-assets/bootc",
                        "fetch",
                        "--no-tags",
                        "--depth",
                        "1",
                        "origin",
                        BOOTC_COMMIT,
                    ]
                ),
                calls,
            )
            self.assertIn(
                json.dumps(
                    [
                        "git",
                        "-C",
                        "build-assets/bootc",
                        "checkout",
                        "--detach",
                        "--force",
                        BOOTC_COMMIT,
                    ]
                ),
                calls,
            )
            verify = json.dumps(
                [
                    "git",
                    "-C",
                    "build-assets/bootc",
                    "rev-parse",
                    "--verify",
                    "HEAD",
                ]
            )
            vendor_index = next(i for i, line in enumerate(calls) if line.startswith("cargo "))
            self.assertLess(calls.index(verify), vendor_index)
            self.assertTrue((root / "build-assets/bootc/vendor").is_dir())
            provenance = json.loads(
                (root / "build-assets/bootc-source.provenance.json").read_text(encoding="utf-8")
            )
            self.assertEqual(provenance["commit"], BOOTC_COMMIT)
            self.assertEqual(provenance["verified_head"], BOOTC_COMMIT)
            self.assertEqual(provenance["repository"], "https://github.com/containers/bootc.git")
        finally:
            temp.cleanup()

    def test_wrong_head_fails_before_cargo_vendor_or_provenance(self):
        wrong_head = "0" * 40
        temp, root, script, env, log = self._fixture(wrong_head)
        try:
            result = subprocess.run(
                ["bash", str(script)],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bootc source HEAD mismatch", result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertFalse(any(line.startswith("cargo ") for line in calls))
            self.assertFalse((root / "build-assets/bootc-source.provenance.json").exists())
        finally:
            temp.cleanup()

    def test_validate_checks_the_pin_and_head_verification_not_a_shallow_clone(self):
        validate = VALIDATE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(f'BOOTC_COMMIT="{BOOTC_COMMIT}"', validate)
        self.assertIn("grep -Fq 'fetch --no-tags --depth 1 origin' build/fetch-assets.sh", validate)
        self.assertIn("grep -Fq 'rev-parse --verify HEAD' build/fetch-assets.sh", validate)
        self.assertNotIn("grep -Fq 'git clone --depth 1'", validate)


if __name__ == "__main__":
    unittest.main()
