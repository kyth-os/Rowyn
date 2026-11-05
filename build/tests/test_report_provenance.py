import importlib.machinery
import importlib.util
import json
import pathlib
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "shared" / "rowyn-report"
loader = importlib.machinery.SourceFileLoader("rowyn_report_fixture", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
report = importlib.util.module_from_spec(spec)
loader.exec_module(report)

REV = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64
REF = "ghcr.io/mrtrickdotorg/rowyn:stable"


def status_document():
    return json.dumps({"status": {
        "booted": {"image": {"image": {"image": REF}, "imageDigest": DIGEST}},
        "staged": {"image": {"image": {"image": "ghcr.io/mrtrickdotorg/rowyn@" + DIGEST}, "imageDigest": DIGEST}},
    }})


class ProvenanceFixtures(unittest.TestCase):
    def tearDown(self):
        report._PUBLIC_VALUES.clear()

    def test_build_metadata_requires_exact_revision_and_bounded_json(self):
        self.assertEqual(report.parse_build_info(json.dumps({"source_revision": REV, "image_version": "26.10"})), [REV])
        self.assertEqual(report.parse_build_info(json.dumps({"source_revision": "../private", "image_version": "26.10"})), [])
        self.assertEqual(report.parse_build_info("{" + " " * 17000), [])

    def test_public_image_refs_validate_exact_repository_and_full_digest(self):
        self.assertEqual(report.public_image_values(status_document()), [REF, DIGEST, "ghcr.io/mrtrickdotorg/rowyn@" + DIGEST])
        bad = json.dumps({"status": {"booted": {"image": {"image": {"image": "ghcrXio/mrtrickdotorg/rowyn:tag"}, "imageDigest": "sha256:" + "b" * 63}}}})
        self.assertEqual(report.public_image_values(bad), [])
        self.assertEqual(report.public_image_values(" " * 66000), [])

    def test_gather_reads_only_fixed_bounded_sources_and_guard_command(self):
        build = json.dumps({"source_revision": REV, "image_version": "26.10"})
        def fake_read(path, limit=20000):
            if path == "/usr/share/rowyn/build-info.json":
                self.assertEqual(limit, 16384)
                return True, build
            return True, "fixture"
        def fake_run(cmd, timeout=15):
            if cmd[0] == "sudo":
                self.assertEqual(cmd, ["sudo", "-n", "/usr/bin/rowyn-bootc-guard", "status-json"])
                self.assertEqual(timeout, 35)
                return True, status_document()
            return True, "fixture"
        with mock.patch.object(report, "read_file", side_effect=fake_read), mock.patch.object(report, "run", side_effect=fake_run):
            sections, facts = report.gather(True)
        self.assertIn(REV, report._PUBLIC_VALUES)
        self.assertIn(REF, report._PUBLIC_VALUES)
        self.assertIn(DIGEST, report._PUBLIC_VALUES)
        image = dict(sections)["Image"]
        self.assertIn(REF, image)
        self.assertIn(DIGEST, image)
        self.assertEqual(facts["image"], REF)

    def test_scrubbing_preserves_only_complete_trusted_tokens(self):
        report._PUBLIC_VALUES[:] = [REV, REF, DIGEST]
        private = "f" * 64
        text = f"{REV} {REF} {DIGEST} prefix{REV} {private} password=hunter2 10.2.3.4 /home/alice/private"
        result = report.scrub_public(text, {"users": [], "hosts": []})
        for value in (REV, REF, DIGEST):
            self.assertIn(value, result)
        for value in (private, "hunter2", "10.2.3.4", "/home/alice"):
            self.assertNotIn(value, result)
        self.assertEqual(report.scrub_public("prefix" + REV, {"users": [], "hosts": []}), report.scrub("prefix" + REV, {"users": [], "hosts": []}))
        for surround in ("x" + REV, REV + "x", "prefix" + REF, REF + ".suffix"):
            self.assertEqual(report.scrub_public(surround, {"users": [], "hosts": []}), report.scrub(surround, {"users": [], "hosts": []}))
        self.assertNotIn(REV, report.scrub_public("password=" + REV, {"users": [], "hosts": []}))

    def test_run_deadline_still_applies_after_child_closes_output(self):
        import sys
        import time
        started = time.monotonic()
        ok, output = report.run([sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(2)"], timeout=0.15)
        self.assertFalse(ok)
        self.assertIn("timed out", output)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_timeout_kill_wait_is_bounded_and_reaped_asynchronously(self):
        import threading
        import time
        class Child:
            def __init__(self): self.entered = threading.Event(); self.release = threading.Event(); self.killed = False
            def kill(self): self.killed = True
            def wait(self, timeout=None):
                if timeout is not None:
                    self.entered.set()
                    if not self.release.wait(timeout):
                        raise report.subprocess.TimeoutExpired("mock", timeout)
                    return -9
                self.release.wait()
                return -9
        child = Child()
        started = time.monotonic()
        with mock.patch.object(report.subprocess, "TimeoutExpired", report.subprocess.TimeoutExpired):
            report._kill_and_reap(child)
        self.assertTrue(child.killed)
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertTrue(child.entered.wait(0.5))
        child.release.set()


    def test_run_bounds_flooded_child_output(self):
        import sys
        ok, output = report.run([sys.executable, "-c", "import sys; sys.stdout.write('x'*100000)"], timeout=5)
        self.assertFalse(ok)
        self.assertIn("exceeded 65536-byte limit", output)

    def test_build_metadata_is_rendered_and_read_is_bytes_bounded(self):
        build = json.dumps({"source_revision": REV, "image_version": "26.10"})
        def fake_read(path, limit=20000):
            if path == "/usr/share/rowyn/build-info.json":
                self.assertEqual(limit, 16384)
                return True, build
            return True, "fixture"
        with mock.patch.object(report, "read_file", side_effect=fake_read), mock.patch.object(report, "run", return_value=(True, "fixture")):
            sections, _ = report.gather(False)
        self.assertIn(REV, dict(sections)["Build metadata"])
        self.assertIn("26.10", dict(sections)["Build metadata"])

    def test_private_write_is_exclusive(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "report.md"
            report.write_private(str(path), "private report")
            self.assertEqual(path.read_text(), "private report")
            with self.assertRaises(FileExistsError):
                report.write_private(str(path), "overwrite")
            self.assertEqual(path.read_text(), "private report")


if __name__ == "__main__":
    unittest.main()
