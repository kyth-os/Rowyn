#!/usr/bin/env python3
"""Safe behavioral regressions; no sudo, mounts, Podman, or network are used.

Run: python3 -B -m unittest discover -s build/tests -v
Optional real-source check: set ROWYN_TEST_KYTH_REPO to a local Kyth checkout.
All writes, including disposable worktrees, live below TMPDIR.
ROWYN_TEST_ACTION / ROWYN_TEST_ADAPTER allow testing saved pre-fix files.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = Path(os.environ.get("ROWYN_TEST_ACTION", ROOT / ".github/actions/setup-compressed-storage/action.yml"))
ADAPTER = Path(os.environ.get("ROWYN_TEST_ADAPTER", ROOT / "build/custom-installer-iso.sh"))
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
EXECUTOR = "src/kyth-installer-web/src-tauri/src/installer_job_executor.rs"

# This executable NEVER delegates to the host, even for an unexpected command.
# Its virtual directory/mount table models reset deleting storage directories.
STORAGE_MOCK = r'''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
state_path = Path(os.environ['MOCK_STATE'])
s = json.loads(state_path.read_text())
a = sys.argv[1:]
command = Path(sys.argv[0]).name
s['calls'].append([command] + a)
result = ''
if command == 'sudo':
    if a[:2] == ['mkdir', '-p']:
        s['dirs'] = sorted(set(s['dirs'] + a[2:]))
    elif a[:3] == ['podman', 'system', 'reset']:
        s['dirs'] = [p for p in s['dirs'] if p not in [os.environ['MOUNT_POINT'], os.environ['RUNROOT']]]
    elif a[:2] == ['mount', '--bind']:
        source, target = a[2:]
        assert source in s['dirs'], 'bind source missing'
        assert target in s['dirs'], 'post-reset bind target missing'
        s['bind_source'] = source
        s['mounts'][target] = source
    elif a[:2] == ['mount', '-o']:
        assert a[-1] in s['dirs'], 'post-reset graphroot missing'
        s['mounts'][a[-1]] = 'loop'
    elif a[:2] == ['podman', 'info']:
        result = os.environ['MOUNT_POINT'] + ' ' + os.environ['RUNROOT']
        if s['fault'] == 'podman_graphroot': result = '/wrong ' + os.environ['RUNROOT']
        if s['fault'] == 'podman_runroot': result = os.environ['MOUNT_POINT'] + ' /wrong'
    elif a and a[0] in ['truncate', 'mkfs.btrfs', 'systemctl']:
        pass
    else:
        raise AssertionError('unsafe/unexpected mocked sudo command: ' + repr(a))
elif command == 'findmnt':
    assert a[:2] == ['-n', '-o'] and a[3] == '--target', a
    column, path = a[2], a[4]
    assert column in ['FSTYPE', 'MAJ:MIN'], column
    s['queries'].append([path, column])
    backing = s['mounts'].get(path, path)
    fs, device = ('tmpfs', '0:25') if backing.startswith('/run/') else ('ext4', '8:1')
    if backing == 'loop': fs, device = 'btrfs', '7:0'
    if s['fault'] == 'graphroot_fs' and path == os.environ['MOUNT_POINT']: fs = 'ext4'
    if s['fault'] == 'bind_tmpfs' and path == os.environ['RUNROOT']: fs, device = 'tmpfs', '0:25'
    if s['fault'] == 'bind_device' and path == os.environ['RUNROOT']: device = '8:2'
    if s['fault'] == 'source_device' and path == s.get('bind_source'): device = '8:2'
    if s['fault'] == 'image_tmpfs': fs, device = 'tmpfs', '0:25'
    if s['fault'] == 'image_ramfs': fs, device = 'ramfs', '0:26'
    if s['fault'] == 'empty_device': device = ''
    if s['fault'] == 'findmnt_error':
        state_path.write_text(json.dumps(s))
        sys.exit(1)
    result = fs if column == 'FSTYPE' else device
else:
    raise AssertionError('unexpected mock executable: ' + command)
state_path.write_text(json.dumps(s))
if result: print(result)
'''


class StorageActionTests(unittest.TestCase):
    def run_action(self, fault="", image="/disk/custom image.img", runroot="/run/custom/storage"):
        script = yaml.safe_load(ACTION.read_text())["runs"]["steps"][0]["run"]
        with tempfile.TemporaryDirectory(prefix="rowyn-storage-") as temp:
            temp = Path(temp)
            mock = temp / "mock"
            mock.write_text(STORAGE_MOCK)
            mock.chmod(0o755)
            for name in ("sudo", "findmnt"):
                (temp / name).symlink_to(mock)
            state_path = temp / "state.json"
            state_path.write_text(json.dumps(dict(dirs=[], mounts={}, calls=[], queries=[], fault=fault)))
            env = dict(os.environ, PATH=str(temp), MOCK_STATE=str(state_path),
                       IMAGE_PATH=image, MOUNT_POINT="/var/lib/custom/storage", RUNROOT=runroot, SIZE="65G")
            result = subprocess.run(["/bin/bash", "-c", script], env=env, text=True, capture_output=True)
            return result, json.loads(state_path.read_text())

    def test_disk_source_and_post_reset_targets(self):
        for image, runroot in (("/disk/custom image.img", "/run/custom/storage"),
                               ("/mnt/rowyn-podman-storage.img", "/run/containers/storage")):
            with self.subTest(image=image):
                result, state = self.run_action(image=image, runroot=runroot)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["bind_source"], image + ".runroot")
                for path in (image, image + ".runroot", runroot):
                    for column in ("FSTYPE", "MAJ:MIN"):
                        self.assertIn([path, column], state["queries"])
                self.assertIn(["/var/lib/custom/storage", "FSTYPE"], state["queries"])

    def test_rejects_wrong_backing_filesystems_and_devices(self):
        for fault in ("graphroot_fs", "bind_tmpfs", "bind_device", "source_device",
                      "image_tmpfs", "image_ramfs", "empty_device", "findmnt_error"):
            with self.subTest(fault=fault):
                result, state = self.run_action(fault)
                self.assertNotEqual(result.returncode, 0, "accepted " + fault)
                self.assertNotIn("Traceback", result.stderr, "mock setup failed before assertion")
                self.assertTrue(state["queries"], "filesystem queries were never reached")

    def test_preserves_podman_path_assertions(self):
        for fault in ("podman_graphroot", "podman_runroot"):
            with self.subTest(fault=fault):
                result, state = self.run_action(fault)
                self.assertNotEqual(result.returncode, 0, "accepted " + fault)
                self.assertNotIn("Traceback", result.stderr, "mock setup failed before assertion")
                self.assertTrue(state["queries"], "filesystem queries were never reached")

    def test_rejects_image_on_run_tmpfs(self):
        result, _ = self.run_action(image="/run/storage.img")
        self.assertNotEqual(result.returncode, 0)


# A deliberately small adapter fixture, not a substitute for pinned-source proof.
FIXTURE = {
    "build_files/scripts/rootful-podman.sh": "#!/bin/bash\nexec unshare --cgroup podman \"$@\"\n",
    "build_files/build-live-iso.sh": "#!/bin/bash\nexit 99 # must never build in preflight\n",
    "installer/Containerfile": "FROM fixture\n",
    "installer/iso.yaml": "fixture: true\n",
    "installer/build.sh": '''#!/bin/bash
target_image=upstream
cosign_identity=upstream
cosign_bin=upstream
cosign_transient=upstream
''',  # No optional installer env file or token-file anchor exists here.
    "src/kyth-installer-web/src/InstallerApp.tsx": 'options={["fedora", "cachyos"]} hint="CachyOS kernel: tuned for gaming and latency. Fedora: the stock, most conservative kernel."\n',
    "src/kyth-installer-web/src-tauri/ui/installer.slint": '                                Button { text: "CachyOS"; clicked => { kernel = "cachyos"; select-kernel("cachyos"); } }\n',
    EXECUTOR: '''const FILESYSTEM_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/alongside-target";
    fn verify_install_source(&self) {
        Ok(())
    }

    fn check_storage_preflight() {}
    fn run_phase() {
        match phase {
            Phase::Prepare => {
                let power = crate::installer_orchestration::power_check();
            }
            Phase::Storage => self.storage(),
            Phase::Image => self.execute_image(phase, cancellation),
        }
    }
    fn execute_image() {
        self.verify_install_source(phase)?;
        self.check_storage_preflight(phase, cancellation)?;
    }
''',
    "build_files/kyth-installerd.service": "RestrictAddressFamilies=AF_UNIX\n",
}


def preflight(source, extra_env=None):
    env = dict(os.environ, IMAGE_REF="ghcr.io/kyth-os/rowyn:" + "a" * 40,
               IMAGE_DIGEST="sha256:" + "b" * 64, SOURCE_TAG="a" * 40,
               KYTH_SOURCE_DIR=str(source), KYTH_INSTALLER_COMMIT=PIN,
               KYTH_INSTALLER_BASE_IMAGE="ghcr.io/kyth-os/kyth@sha256:" + "c" * 64,
               ROWYN_CUSTOM_INSTALLER_PREFLIGHT="1")
    env.pop("KYTH_EARLY_SOURCE_VERIFY", None)
    env.update(extra_env or {})
    return subprocess.run(["bash", str(ADAPTER)], cwd=ROOT, env=env, text=True, capture_output=True)


def assert_gate(test, source):
    executor = (source / EXECUTOR).read_text()
    prepare = executor.index("            Phase::Prepare => {")
    power = executor.index("                let power = crate::installer_orchestration::power_check();", prepare)
    test.assertEqual(executor[prepare:power], "            Phase::Prepare => {\n                self.verify_install_source(phase)?;\n")
    test.assertNotIn("KYTH_EARLY_SOURCE_VERIFY", executor)
    test.assertIn('Command::new("/usr/bin/kyth-cosign")', executor)
    test.assertEqual(executor.count("const ROWYN_RELEASE_DIGEST:"), 1)
    image = executor[executor.index("    fn execute_image("):]
    test.assertLess(image.index("self.verify_install_source(phase)?;"), image.index("self.check_storage_preflight(phase, cancellation)?;"))
    test.assertNotIn("KYTH_EARLY_SOURCE_VERIFY", (source / "installer/build.sh").read_text())


class AdapterFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rowyn-adapter-")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        for name, content in FIXTURE.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        # Only the fixture's git queries are mocked; no fabricated real-source claim.
        mockbin = Path(self.temp.name) / "bin"
        mockbin.mkdir()
        git = mockbin / "git"
        real_git = shutil.which("git")
        assert real_git is not None, "git is required for adapter tests"
        git.write_text('#!/bin/bash\nif [[ "$1" == "-C" && "$2" == "$KYTH_SOURCE_DIR" ]]; then\n'
                       '  case "$4" in --show-toplevel) printf "%s\\n" "$KYTH_SOURCE_DIR";; HEAD) printf "%s\\n" "$KYTH_INSTALLER_COMMIT";; *) exit 98;; esac\n'
                       'else exec ' + real_git + ' "$@"; fi\n')
        git.chmod(0o755)
        self.env = {"PATH": str(mockbin) + os.pathsep + os.environ["PATH"]}

    def test_unconditional_prepare_without_optional_env_and_idempotence(self):
        for early in (None, "0", "1"):
            env = dict(self.env)
            if early is not None:
                env["KYTH_EARLY_SOURCE_VERIFY"] = early
            result = preflight(self.source, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            assert_gate(self, self.source)
            snapshot = {p: (self.source / p).read_bytes() for p in FIXTURE}
            result = preflight(self.source, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(snapshot, {p: (self.source / p).read_bytes() for p in FIXTURE})

    def test_hosted_wrapper_replacement_is_narrow_and_idempotent(self):
        path = self.source / "build_files/scripts/rootful-podman.sh"
        original = path.read_bytes()
        result = preflight(self.source, dict(self.env, GITHUB_ACTIONS="false"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), original)
        for _ in range(2):
            result = preflight(self.source, dict(self.env, GITHUB_ACTIONS="true", RUNNER_ENVIRONMENT="github-hosted"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_bytes(), (ROOT / "build/rootful-podman.sh").read_bytes())
            self.assertTrue(os.access(path, os.X_OK))

    def test_duplicate_prepare_anchor_is_rejected(self):
        path = self.source / EXECUTOR
        path.write_text(path.read_text() + '            Phase::Prepare => {\n                let power = crate::installer_orchestration::power_check();\n')
        result = preflight(self.source, self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prepare", result.stderr)

    def test_mixed_original_and_patched_prepare_is_rejected(self):
        result = preflight(self.source, self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.source / EXECUTOR
        path.write_text(path.read_text() + '            Phase::Prepare => {\n                let power = crate::installer_orchestration::power_check();\n')
        result = preflight(self.source, self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prepare", result.stderr)

    def test_missing_prepare_anchor_is_rejected(self):
        path = self.source / EXECUTOR
        path.write_text(path.read_text().replace("Phase::Prepare =>", "Phase::Missing =>"))
        result = preflight(self.source, self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prepare", result.stderr)


@unittest.skipUnless(os.environ.get("ROWYN_TEST_KYTH_REPO"), "set ROWYN_TEST_KYTH_REPO for pinned-source integration")
class PinnedSourceTests(unittest.TestCase):
    def test_repeated_preflight_at_pinned_commit(self):
        repo = Path(os.environ["ROWYN_TEST_KYTH_REPO"])
        with tempfile.TemporaryDirectory(prefix="rowyn-pinned-") as temp:
            source = Path(temp) / "kyth"
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(source), PIN], check=True, capture_output=True)
            try:
                head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
                self.assertEqual(head, PIN)
                hosted = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
                first = preflight(source, hosted)
                self.assertEqual((source / "build_files/scripts/rootful-podman.sh").read_bytes(),
                                 (ROOT / "build/rootful-podman.sh").read_bytes())
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                assert_gate(self, source)
                diff = subprocess.check_output(["git", "-C", str(source), "diff", "--binary"])
                second = preflight(source, dict(hosted, KYTH_EARLY_SOURCE_VERIFY="0"))
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertEqual(diff, subprocess.check_output(["git", "-C", str(source), "diff", "--binary"]))
                assert_gate(self, source)
                subprocess.run(["git", "-C", str(source), "diff", "--check"], check=True)
                if shutil.which("rustfmt"):
                    subprocess.run(["rustfmt", "--check", "--edition", "2021", str(source / EXECUTOR)], check=True)
            finally:
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(source)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
