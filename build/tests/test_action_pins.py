import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.M)
PINNED = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}")


class ActionPinTests(unittest.TestCase):
    def test_every_uses_is_local_or_sha_pinned(self):
        files = sorted((ROOT / ".github/workflows").glob("*.yml")) + sorted((ROOT / ".github/actions").glob("**/action.yml"))
        self.assertTrue(files)
        count = 0
        for file in files:
            for ref in USES.findall(file.read_text()):
                count += 1
                ref = ref.strip("'\"")
                if ref.startswith("./"):
                    continue
                self.assertRegex(ref, PINNED, f"{file.relative_to(ROOT)}: unpinned action {ref}")
                self.assertRegex(ref.split("@")[1], r"^[0-9a-f]{40}$")
        self.assertGreater(count, 0)

    def test_dependabot_covers_github_actions(self):
        text = (ROOT / ".github/dependabot.yml").read_text()
        self.assertIn("package-ecosystem: github-actions", text)
        self.assertIn("weekly", text)


if __name__ == "__main__":
    unittest.main()
