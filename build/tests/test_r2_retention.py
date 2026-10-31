"""R2 retention planning/execution and the R2 upload workflow contracts."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "build/r2_retention.py"
SPEC = importlib.util.spec_from_file_location("r2_retention", SCRIPT)
assert SPEC and SPEC.loader
r2 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r2
SPEC.loader.exec_module(r2)

SUFFIXES = ("", ".sha256", ".sigstore.json", ".json")
ENDPOINT = "https://acct.r2.cloudflarestorage.com"


def build_keys(date, sha, run, attempt=1, suffixes=SUFFIXES, channel="stable"):
    stem = f"rowyn-live-{channel}-{date}-{sha}-{run}-{attempt}.iso"
    return [stem + s for s in suffixes]


class PlanTests(unittest.TestCase):
    def test_keeps_newest_n_and_deletes_whole_sets(self):
        keys = []
        for run in range(1, 8):
            keys += build_keys(f"202610{run:02d}", "a94b429e", run)
        doomed = r2.plan_deletions(keys, keep=5)
        self.assertEqual(doomed, sorted(build_keys("20261001", "a94b429e", 1)
                                        + build_keys("20261002", "a94b429e", 2)))

    def test_date_dominates_run_number(self):
        keys = build_keys("20260814", "5d6b01f3", 1736) + build_keys("20261028", "a94b429e", 993)
        self.assertEqual(r2.plan_deletions(keys, keep=1),
                         sorted(build_keys("20260814", "5d6b01f3", 1736)))

    def test_same_day_orders_by_run_then_attempt(self):
        keys = (build_keys("20261025", "6f4ef4be", 984) + build_keys("20261025", "2baf8496", 986)
                + build_keys("20261025", "6f4ef4be", 983))
        self.assertEqual(r2.plan_deletions(keys, keep=2), sorted(build_keys("20261025", "6f4ef4be", 983)))

    def test_rerun_attempts_order(self):
        keys = build_keys("20261025", "6f4ef4be", 984, 1) + build_keys("20261025", "6f4ef4be", 984, 2)
        self.assertEqual(r2.plan_deletions(keys, keep=1), sorted(build_keys("20261025", "6f4ef4be", 984, 1)))

    def test_partial_set_deleted_as_unit(self):
        keys = []
        for run in range(1, 7):
            keys += build_keys(f"202608{run:02d}", "b0513646", run)
        keys += build_keys("20260613", "31d4e50f", 684, suffixes=("", ".json"))
        doomed = r2.plan_deletions(keys, keep=5)
        self.assertEqual(len([k for k in doomed if "20260613" in k]), 2)

    def test_pointers_and_unknown_keys_never_planned(self):
        keys = []
        for run in range(1, 9):
            keys += build_keys(f"202610{run:02d}", "a94b429e", run)
        foreign = ["rowyn-live-stable.iso", "rowyn-live-stable.iso.sha256",
                   "rowyn-live-stable.iso.sigstore.json", "rowyn-live-stable.iso.json",
                   "rowyn-live-beta-20261001-a94b429e-1-1.iso", "rowyn-live-stable-20261001-a94b429e-1-1.iso.sig",
                   "rowyn-live-stable-20261001-A94B429E-1-1.iso", "x/rowyn-live-stable-20261001-a94b429e-1-1.iso",
                   "README.txt"]
        doomed = r2.plan_deletions(keys + foreign, keep=5)
        for key in foreign:
            self.assertNotIn(key, doomed)
        self.assertEqual(len(doomed), 3 * len(SUFFIXES))

    def test_kyth_keys_untouched_in_mixed_bucket(self):
        kyth = []
        for run in range(1, 12):
            for channel in ("testing", "latest"):
                stem = f"kyth-live-{channel}-202610{run:02d}-a94b429e-{run}-1.iso"
                kyth += [stem + s for s in ("", "-CHECKSUM", ".sig", ".bundle", ".intoto.jsonl", ".json")]
        kyth += ["kyth-live-testing.iso", "kyth-live-latest.iso-CHECKSUM", "kyth-secureboot.der"]
        rowyn = []
        for run in range(1, 9):
            rowyn += build_keys(f"202610{run:02d}", "a94b429e", run)
        doomed = r2.plan_deletions(kyth + ["other.bin"] + rowyn, keep=5)
        self.assertEqual(len(doomed), 3 * len(SUFFIXES))
        self.assertTrue(all(k.startswith("rowyn-live-stable-") for k in doomed))
        self.assertFalse(set(doomed) & set(kyth + ["other.bin"]))

    def test_fewer_than_keep_deletes_nothing(self):
        self.assertEqual(r2.plan_deletions(build_keys("20261028", "a94b429e", 993), keep=5), [])

    def test_keep_must_be_positive(self):
        with self.assertRaises(ValueError):
            r2.plan_deletions([], keep=0)

    def test_default_prefix_and_single_channel(self):
        self.assertEqual(r2.CHANNELS, ("stable",))
        self.assertIn('"rowyn-live-"', SCRIPT.read_text())


class FakeRunner:
    def __init__(self, pages):
        self.pages = list(pages)
        self.deleted = []
        self.tokens = []
        self.batches = []

    def __call__(self, argv):
        if "list-objects-v2" in argv:
            self.tokens.append(argv[argv.index("--continuation-token") + 1]
                               if "--continuation-token" in argv else None)
            return self.pages.pop(0)
        if "delete-objects" in argv:
            ref = next(a for a in argv if a.startswith("file://"))
            payload = json.loads(Path(ref[len("file://"):]).read_text())
            self.batches.append(len(payload["Objects"]))
            self.deleted.extend(o["Key"] for o in payload["Objects"])
            return {}
        raise AssertionError(argv)


def page(keys, truncated=False, token=None):
    p = {"Contents": [{"Key": k, "Size": 1000} for k in keys], "IsTruncated": truncated}
    if token:
        p["NextContinuationToken"] = token
    return p


class ApplyTests(unittest.TestCase):
    def test_pagination_and_deletes_only_planned(self):
        old = build_keys("20261001", "a94b429e", 1)
        keys = list(old)
        for run in range(2, 7):
            keys += build_keys(f"202610{run:02d}", "a94b429e", run)
        keys.append("rowyn-live-stable.iso")
        runner = FakeRunner([page(keys[:15], True, "tok-1"), page(keys[15:])])
        seen, deleted = r2.apply_retention(runner, "b", ENDPOINT, keep=5)
        self.assertEqual((seen, deleted), (6, len(old)))
        self.assertEqual(sorted(runner.deleted), sorted(old))
        self.assertEqual(runner.tokens, [None, "tok-1"])

    def test_truncated_without_token_errors(self):
        with self.assertRaises(RuntimeError):
            r2.apply_retention(FakeRunner([page(["a"], truncated=True)]), "b", ENDPOINT)

    def test_dry_run_deletes_nothing(self):
        keys = []
        for run in range(1, 8):
            keys += build_keys(f"202610{run:02d}", "a94b429e", run)
        runner = FakeRunner([page(keys)])
        _, deleted = r2.apply_retention(runner, "b", ENDPOINT, keep=5, dry_run=True)
        self.assertEqual((deleted, runner.deleted), (0, []))

    def test_batches_at_most_1000(self):
        keys = []
        for run in range(1, 508):
            keys += build_keys(f"2026{run:04d}", "a94b429e", run)
        runner = FakeRunner([page(keys)])
        _, deleted = r2.apply_retention(runner, "b", ENDPOINT, keep=5)
        self.assertEqual(deleted, 502 * len(SUFFIXES))
        self.assertGreater(len(runner.batches), 1)
        self.assertTrue(all(n <= 1000 for n in runner.batches))

    def test_runner_failure_exit_code_one(self):
        def failing(argv):
            raise RuntimeError("aws exploded")
        self.assertEqual(r2.main(["--bucket", "b", "--endpoint-url", ENDPOINT], runner=failing), 1)

    def test_main_rejects_keep_below_one(self):
        with self.assertRaises(SystemExit):
            r2.main(["--bucket", "b", "--endpoint-url", ENDPOINT, "--keep", "0"], runner=FakeRunner([]))


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/installer-iso.yml").read_text()
        cls.workflow = yaml.safe_load(cls.text)
        cls.action_text = (ROOT / ".github/actions/upload-r2/action.yml").read_text()
        cls.action = yaml.safe_load(cls.action_text)
        cls.steps = cls.workflow["jobs"]["iso"]["steps"]
        cls.names = [s.get("name", "") for s in cls.steps]

    def test_r2_steps_follow_signing_in_iso_job(self):
        sign = self.names.index("ISO summary, checksums, and signature")
        upload = self.names.index("Upload ISO to Cloudflare R2")
        self.assertGreater(upload, sign)
        self.assertGreater(self.names.index("Sign R2 copies of the ISO checksum"), sign)
        self.assertEqual(self.steps[upload]["uses"], "./.github/actions/upload-r2")
        self.assertNotIn("Upload ISO to Cloudflare R2", [s.get("name") for s in self.workflow["jobs"]["validate"]["steps"]])

    def test_missing_secrets_skip_path(self):
        self.assertIn("::notice::R2 upload skipped", self.text)
        self.assertIn("configured=false", self.text)
        upload = self.steps[self.names.index("Upload ISO to Cloudflare R2")]
        self.assertIn("configured == 'true'", upload["if"])
        self.assertNotIn("secrets.", upload["if"])

    def test_artifact_upload_independent_of_r2(self):
        art = next(s for s in self.steps if s.get("name") == "Upload custom installer ISO")
        self.assertIn("!cancelled()", art["if"])

    def test_job_permissions_unchanged(self):
        self.assertEqual(self.workflow["jobs"]["iso"]["permissions"],
                         {"contents": "read", "actions": "read", "id-token": "write"})

    def test_bucket_default_and_resigning(self):
        self.assertIn("vars.R2_BUCKET || 'rowyn-releases'", self.text)
        self.assertIn("cosign sign-blob --yes --bundle", self.text)
        self.assertIn("vars.R2_PUBLIC_URL", self.text)

    def test_no_secret_echoed(self):
        for text in (self.text, self.action_text):
            self.assertIsNone(re.search(r'echo[^\n]*\$\{\{?\s*(secrets\.|[A-Z0-9_]*SECRET)', text))
            self.assertIsNone(re.search(r'echo[^\n]*\$\{?(AWS_SECRET_ACCESS_KEY|R2_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID)', text))

    def test_action_validation_retention_and_ordering(self):
        self.assertIn(r"^https://[A-Za-z0-9.-]+\.r2\.cloudflarestorage\.com$", self.action_text)
        self.assertIn("AWS_DEFAULT_REGION: auto", self.action_text)
        self.assertEqual(self.action["inputs"]["bucket"]["default"], "rowyn-releases")
        self.assertEqual(self.action["inputs"]["retention-keep"]["default"], "5")
        steps = self.action["runs"]["steps"]
        self.assertEqual(len(steps), 2)
        self.assertIs(steps[1]["continue-on-error"], True)
        self.assertNotIn("continue-on-error", steps[0])
        self.assertIn("r2_retention.py", steps[1]["run"])
        self.assertLess(self.action_text.index("aws s3 cp"), self.action_text.index("r2_retention.py"))
        self.assertTrue((ROOT / ".github/actions/upload-r2/../../../build/r2_retention.py").exists())


if __name__ == "__main__":
    unittest.main()
