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


def load_module(name, file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


port = load_module("ubuntu_source", "build/ubuntu-installer-source.py")
live = load_module("ubuntu_live", "build/ubuntu-live-preflight.py")
guard = load_module("ubuntu_guard", "build/ubuntu-only-check.py")
network = load_module("ubuntu_live_network", "build/ubuntu-live-network.py")


class UbuntuGuardTests(unittest.TestCase):
    def test_current_executable_path_is_ubuntu(self):
        self.assertTrue(guard.validate(ROOT).startswith("docker.io/library/ubuntu@sha256:"))

    def test_guard_rejects_non_ubuntu_stages_and_executables(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-guard-") as temp:
            root = Path(temp)
            files = ["Containerfile", ".github/workflows/installer-iso.yml", "build/custom-installer-iso.sh",
                     "build/configure-live.sh", "build/assemble-iso.sh", "build/ubuntu-live.Containerfile"]
            for file in files:
                target = root / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / file).read_bytes())
            for file, old, new in [
                ("build/ubuntu-live.Containerfile", "FROM ${UBUNTU_IMAGE}", "FROM registry.fedoraproject.org/fedora:45"),
                ("build/configure-live.sh", "set -euo pipefail", "set -euo pipefail\ndnf install -y rust"),
                ("build/configure-live.sh", "set -euo pipefail", "set -euo pipefail\n    sudo rpm -q bootc"),
                ("build/assemble-iso.sh", "set -euo pipefail", "set -euo pipefail\nbash build_files/build-live-iso.sh"),
                (".github/workflows/installer-iso.yml", "docker.io/library/ubuntu@sha256:", "ghcr.io/kyth-os/kyth@sha256:"),
            ]:
                path = root / file
                text = path.read_text()
                self.assertIn(old, text)
                path.write_text(text.replace(old, new))
                with self.assertRaises(ValueError, msg=file):
                    guard.validate(root)
                path.write_text(text)
            guard.validate(root)

    def test_port_rejects_ambiguous_anchors_and_bad_digest(self):
        for text in ("", "anchor anchor"):
            with self.assertRaises(ValueError):
                port.replace_once(text, "anchor", "replacement")
        with self.assertRaises(ValueError):
            port.port_source(Path("/does/not/exist"), "sha256:bad")

    def test_legacy_base_rejected_before_container_execution(self):
        env = dict(os.environ, IMAGE_REF="ghcr.io/kyth-os/rowyn:" + "a"*40,
                   SOURCE_TAG="a"*40, IMAGE_DIGEST="sha256:" + "b"*64,
                   KYTH_SOURCE_DIR="/never/read", KYTH_INSTALLER_COMMIT=PIN,
                   UBUNTU_LIVE_IMAGE="ghcr.io/kyth-os/kyth@sha256:" + "c"*64)
        result = subprocess.run(["bash", str(ADAPTER)], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("official Ubuntu", result.stderr)


class LiveNetworkConfigurationTests(unittest.TestCase):
    def test_live_image_installs_netplan_and_generates_policy(self):
        containerfile = (ROOT / "build/ubuntu-live.Containerfile").read_text()
        configure = (ROOT / "build/configure-live.sh").read_text()
        self.assertIn("network-manager netplan.io plasma-nm", containerfile)
        self.assertIn("/usr/bin/python3 /rowyn/ubuntu-live-network.py --root /", configure)
        self.assertIn("netplan generate --root-dir /", configure)

    def test_generated_policy_manages_wired_and_wifi_but_not_other_devices(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-network-") as temp:
            nm_path, netplan_path = network.configure(Path(temp))
            nm_text = nm_path.read_text()
            config = yaml.safe_load(netplan_path.read_text())
            netplan_mode = netplan_path.stat().st_mode & 0o777

        self.assertEqual(nm_path.name, "10-globally-managed-devices.conf")
        self.assertEqual(netplan_mode, 0o600)
        self.assertIn("unmanaged-devices=except:type:ethernet,except:type:wifi,except:type:gsm,except:type:cdma", nm_text)
        self.assertIn("[ifupdown]\nmanaged=true", nm_text)
        self.assertEqual(config["network"]["renderer"], "NetworkManager")
        wired = config["network"]["ethernets"]["rowyn-wired"]
        self.assertEqual(wired["match"]["name"], "en*")
        self.assertTrue(wired["dhcp4"])
        self.assertTrue(wired["dhcp6"])
        self.assertTrue(wired["optional"])

        # Exercise NetworkManager's policy shape: only the named device types
        # are exceptions to the global unmanaged wildcard.
        tokens = nm_text.split("unmanaged-devices=", 1)[1].splitlines()[0].split(",")
        def is_unmanaged(device_type):
            positive = "*" in tokens or all(token.startswith("except:") for token in tokens)
            return positive and f"except:type:{device_type}" not in tokens

        for device_type in ("ethernet", "wifi", "gsm", "cdma"):
            self.assertFalse(is_unmanaged(device_type))
        self.assertTrue(is_unmanaged("bridge"))


class CasperProtectionTests(unittest.TestCase):
    def runner(self, mounts, devices, loops=None):
        calls = []
        def run(*args):
            calls.append(args)
            if args[0] == "/usr/bin/findmnt":
                path = args[-1]
                if path not in mounts:
                    raise subprocess.CalledProcessError(1, args)
                return mounts[path]
            if args[0] == "/usr/bin/lsblk":
                return json.dumps(dict(blockdevices=devices[args[-1]]))
            if args[0] == "/usr/sbin/losetup":
                assert loops is not None
                return json.dumps(dict(loopdevices=[dict(name=args[-1], **{"back-file": loops[args[-1]]})]))
            raise AssertionError(args)
        return run, calls

    def test_live_usb_whole_disk_and_partition_are_protected(self):
        devices = {"/dev/sda1": [dict(name="/dev/sda1", type="part", children=[dict(name="/dev/sda", type="disk")])],
                   "/dev/sda": [dict(name="/dev/sda", type="disk")],
                   "/dev/nvme0n1": [dict(name="/dev/nvme0n1", type="disk")]}
        run, calls = self.runner({"/cdrom": "/dev/sda1"}, devices)
        live.assert_safe_target("/dev/nvme0n1", run)
        for target in ("/dev/sda", "/dev/sda1"):
            with self.assertRaisesRegex(ValueError, "overlaps"):
                live.assert_safe_target(target, run)
        self.assertTrue(all(a[0] in ("/usr/bin/findmnt", "/usr/bin/lsblk") for a in calls))

    def test_loop_iso_backing_file_resolves_physical_media(self):
        devices = {"/dev/loop0": [dict(name="/dev/loop0", type="loop")],
                   "/dev/sda2": [dict(name="/dev/sda2", type="part", children=[dict(name="/dev/sda", type="disk")])],
                   "/dev/sda": [dict(name="/dev/sda", type="disk")]}
        run, _ = self.runner({"/cdrom": "/dev/loop0", "/isodevice": "/dev/sda2", "/isodevice/rowyn.iso": "/dev/sda2"},
                             devices, {"/dev/loop0": "/isodevice/rowyn.iso"})
        with self.assertRaisesRegex(ValueError, "overlaps"):
            live.assert_safe_target("/dev/sda", run)

    def test_unresolved_and_missing_live_media_fail_closed(self):
        for mounts, devices, loops in (({}, {}, {}), ({"/cdrom": "overlay"}, {}, {}),
            ({"/cdrom": "/dev/loop0", "/bad.iso": "overlay"},
             {"/dev/loop0": [dict(name="/dev/loop0", type="loop")]}, {"/dev/loop0": "/bad.iso"}),
            ({"/cdrom": "/dev/loop0", "/cycle.iso": "/dev/loop0"},
             {"/dev/loop0": [dict(name="/dev/loop0", type="loop")]}, {"/dev/loop0": "/cycle.iso"})):
            run, _ = self.runner(mounts, devices, loops)
            with self.assertRaises(ValueError):
                live.assert_safe_target("/dev/sda", run)


@unittest.skipUnless(os.environ.get("ROWYN_TEST_KYTH_REPO"), "set ROWYN_TEST_KYTH_REPO for pinned-source integration")
class PinnedSourceTests(unittest.TestCase):
    def test_exact_object_export_is_repeatable_and_ports_actual_source(self):
        repo = Path(os.environ["ROWYN_TEST_KYTH_REPO"])
        original = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"])
        with tempfile.TemporaryDirectory(prefix="rowyn-pinned-") as temp:
            snapshots = []
            for name in ("first", "second"):
                root = Path(temp) / name
                port.export_source(repo, root)
                port.port_source(root, "sha256:" + "b"*64)
                snapshots.append({str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})
                self.assertFalse((root / "installer/Containerfile").exists())
                self.assertFalse((root / "build_files/build-live-iso.sh").exists())
                self.assertFalse((root / "src/kyth-installer-web/node_modules").exists())
                executor = (root / EXECUTOR).read_text()
                prepare = executor[executor.index("            Phase::Prepare => {"):executor.index("            Phase::Storage =>")]
                self.assertLess(prepare.index("self.verify_install_source(phase)?;"), prepare.index("rowyn-live-preflight"))
                self.assertIn('Command::new("/usr/bin/kyth-cosign")', executor)
                self.assertIn('"--certificate-identity"', executor)
                self.assertIn(port.IDENTITY, executor)
                image = executor[executor.index('    fn execute_image('):]
                self.assertLess(image.index('self.verify_install_source(phase)?;'), image.index('self.check_storage_preflight(phase, cancellation)?;'))
                crate = root / "src/kyth-installer-web/src-tauri"
                accounts = (crate / "src/installer_accounts.rs").read_text()
                self.assertNotIn("wheel", accounts)
                self.assertIn('"sudo,video,audio,render"', accounts)
                self.assertNotIn('apply_boot_ordering_fix(&plan.target_root)?;', (crate / "src/installer_configuration.rs").read_text())
                self.assertIn('"/cdrom"', (crate / "src/installer_storage.rs").read_text())
                daemon = (crate / "src/installer_daemon.rs").read_text()
                dispatch = daemon[daemon.index('    fn dispatch('):]
                self.assertLess(dispatch.index('return (403'), dispatch.index('let disk ='))
                service = (root / 'build_files/kyth-installerd.service').read_text()
                self.assertIn('EnvironmentFile=/etc/kyth-installer.env', service)
                self.assertIn('RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6', service)
                self.assertIn('ReadWritePaths=/run/kyth-installer', service)
                images = (root / 'src/kyth-shared-rs/src/system/installer_source.rs').read_text()
                fn = images[images.index('pub fn install_images('):images.index('/// Classify')]
                self.assertNotIn('-cachy', fn)
                with self.assertRaises(ValueError):
                    port.export_source(repo, root)
            self.assertEqual(*snapshots)
        self.assertEqual(original, subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"]))


if __name__ == "__main__":
    unittest.main()
