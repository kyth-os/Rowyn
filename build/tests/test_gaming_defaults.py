"""Opt-in gaming helpers (shared/gaming-defaults.sh) and the tuning that
ships with performance-defaults.sh / desktop-tuning.sh."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class GamingDefaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        subprocess.run([str(ROOT / "shared/gaming-defaults.sh"), "--root", str(cls.root)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_and_modes(self):
        for rel, mode in [
            ("usr/bin/rowyn-game-launch", 0o755),
            ("usr/bin/rowyn-mimalloc-run", 0o755),
            ("usr/bin/rowyn-tune", 0o755),
            ("usr/lib/systemd/user/rowyn-gaming.slice", 0o644),
        ]:
            self.assertEqual((self.root / rel).stat().st_mode & 0o777, mode, rel)
        self.assertFalse((self.root / "etc").exists())

    def test_scripts_parse(self):
        for rel in ("usr/bin/rowyn-game-launch", "usr/bin/rowyn-mimalloc-run"):
            subprocess.run(["sh", "-n", str(self.root / rel)], check=True)
        subprocess.run(["bash", "-n", str(self.root / "usr/bin/rowyn-tune")], check=True)

    def test_slice_has_no_memory_cap(self):
        text = (self.root / "usr/lib/systemd/user/rowyn-gaming.slice").read_text()
        self.assertIn("CPUWeight=300", text)
        self.assertNotIn("MemoryMax", text)

    def test_mimalloc_wrapper_preloads_and_execs(self):
        out = subprocess.run(
            [str(self.root / "usr/bin/rowyn-mimalloc-run"), "sh", "-c", "echo $LD_PRELOAD $MIMALLOC_LARGE_OS_PAGES"],
            capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
        self.assertEqual(out.stdout.split(), ["libmimalloc.so.3", "1"])

    def test_launcher_without_args_fails(self):
        for rel in ("usr/bin/rowyn-game-launch", "usr/bin/rowyn-mimalloc-run"):
            self.assertEqual(subprocess.run([str(self.root / rel)], capture_output=True).returncode, 2)

    def test_tune_on_off_status(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"PATH": os.environ["PATH"], "ROWYN_TUNE_CONF": f"{d}/sysctl.d/g.conf"}
            tune = str(self.root / "usr/bin/rowyn-tune")
            run = lambda a: subprocess.run([tune, a], capture_output=True, text=True, env=env, check=True)
            self.assertEqual(run("status").stdout.strip(), "default")
            run("on")
            self.assertEqual(run("status").stdout.strip(), "gaming")
            self.assertIn("vm.dirty_ratio = 5", Path(env["ROWYN_TUNE_CONF"]).read_text())
            run("off")
            self.assertEqual(run("status").stdout.strip(), "default")
            self.assertEqual(subprocess.run([tune, "bogus"], env=env).returncode, 2)

    def test_idempotent(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        subprocess.run([str(ROOT / "shared/gaming-defaults.sh"), "--root", str(self.root)], check=True)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class ShippedTuning(unittest.TestCase):
    def test_khugepaged_and_journal_and_renice(self):
        perf = (ROOT / "shared/performance-defaults.sh").read_text()
        self.assertIn("khugepaged/scan_sleep_millisecs - - - - 10000", perf)
        desk = (ROOT / "shared/desktop-tuning.sh").read_text()
        self.assertIn("SystemMaxUse=200M", desk)
        self.assertIn("ForwardToSyslog=no", desk)
        self.assertIn("renice=10", desk)

    def test_containerfile_wires_helpers(self):
        text = (ROOT / "Containerfile").read_text()
        self.assertIn("/ctx/shared/gaming-defaults.sh --root /", text)
        self.assertIn("libmimalloc3", text)


if __name__ == "__main__":
    unittest.main()
