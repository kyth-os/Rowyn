"""Regression tests for the Ubuntu composefs deployment adapter."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def mount_fixture_command(*, privileged):
    """Return the fixed namespace command selected by the caller's policy."""
    if privileged:
        return ['sudo', '-n', 'unshare', '--mount', '--propagation', 'private']
    return ['unshare', '--user', '--map-root-user', '--mount', '--propagation', 'private']


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'build' / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MountedViewPolicyTests(unittest.TestCase):
    def test_real_generated_policy_rejects_foreign_missing_unmounted_or_readonly_state(self):
        # Exercises the production Rust policy; the probe seam is the only fake.
        code = load('ubuntu-composefs-source').MOUNT_POLICY.split('pub(crate) fn verify_unused_view', 1)[0]
        harness = r'''
fn run(physical: &str, view: &str, bad: &str) -> Result<(), String> {
    validate_view_with(physical, view, |path| {
        let p = path.to_str().unwrap();
        let mode = if p.ends_with("/etc") || p.ends_with("/var") { "rw" } else { "ro" };
        if bad == "missing" && p.ends_with("/var") { return Ok((false, String::new())); }
        if bad == "notmount" && p.ends_with("/etc") { return Ok((true, String::new())); }
        if bad == "parent" && p.ends_with("/var") { return Ok((true, "/run rw".into())); }
        if bad == "readonly" && p.ends_with("/var") { return Ok((true, format!("{p} ro"))); }
        if bad == "image-rw" && p == view { return Ok((true, format!("{p} rw"))); }
        Ok((true, format!("{p} {mode},relatime\n")))
    })
}
fn main() {
    let physical = "/run/kyth-installer/install-root";
    let view = "/run/kyth-installer/deployment-root";
    assert!(run(physical, view, "").is_ok());
    for bad in ["missing", "notmount", "parent", "readonly", "image-rw"] {
        assert!(run(physical, view, bad).is_err(), "{bad}");
    }
    assert!(run(physical, "/foreign", "").is_err());
    assert!(run("/foreign", view, "").is_err());
    assert!(run(physical, physical, "").is_err());
}
'''
        with tempfile.TemporaryDirectory(prefix='rowyn-mounted-policy-') as tmp:
            source = Path(tmp) / 'probe.rs'
            source.write_text('use std::path::Path;\n' + code + harness)
            subprocess.run(['rustc', '--edition=2021', str(source), '-o', str(Path(tmp) / 'probe')], check=True)
            subprocess.run([str(Path(tmp) / 'probe')], check=True)

    def test_fstab_esp_must_resolve_to_selected_partition(self):
        policy = load('ubuntu-composefs-source').ESP_POLICY
        code = 'fn validate_esp_match' + policy.split('fn validate_esp_match', 1)[1].split('fn finish_esp_check', 1)[0]
        harness = r'''
fn main() {
    let root = std::path::PathBuf::from(std::env::args().nth(1).unwrap());
    let selected = root.join("selected");
    let other = root.join("other");
    fs::write(&selected, b"selected").unwrap();
    fs::write(&other, b"other").unwrap();
    let alias = root.join("uuid-selected");
    std::os::unix::fs::symlink(&selected, &alias).unwrap();
    assert!(validate_esp_match(None, alias.to_str().unwrap()).is_ok());
    assert!(validate_esp_match(selected.to_str(), alias.to_str().unwrap()).is_ok());
    assert!(validate_esp_match(other.to_str(), alias.to_str().unwrap()).is_err());
    assert!(validate_esp_match(Some("/missing-esp"), alias.to_str().unwrap()).is_err());
}
'''
        with tempfile.TemporaryDirectory(prefix='rowyn-esp-policy-') as tmp:
            source = Path(tmp) / 'probe.rs'
            source.write_text('use std::fs;\n' + code + harness)
            subprocess.run(['rustc', '--edition=2021', str(source), '-o', str(Path(tmp) / 'probe')], check=True)
            subprocess.run([str(Path(tmp) / 'probe'), tmp], check=True)

    def test_live_runtime_gate_requires_backend_capabilities(self):
        text = (ROOT / 'build/ubuntu-live-preflight.py').read_text()
        for helper in ('zstd', 'setpriv', 'systemctl'):
            self.assertIn('/usr/bin/' + helper, text)
        self.assertIn('--composefs-backend', text)
        self.assertIn("'mount'", text)

    def test_esp_verifier_uses_private_bind_remount_and_exact_mount_identity(self):
        policy = load('ubuntu-composefs-source').ESP_POLICY
        verifier = load('ubuntu-composefs-source').ESP_VERIFY_METHOD
        self.assertIn('--bind', verifier)
        self.assertIn('remount,bind,ro', verifier)
        self.assertIn('VFS-OPTIONS', policy)
        self.assertIn('"filesystems"', policy)
        self.assertIn('validate_esp_mount_record', policy)
        self.assertNotIn('ro=vfs', policy + verifier)

        code = 'fn validate_esp_mount_record' + policy.split('fn validate_esp_mount_record', 1)[1].split('fn mounted_esp_records', 1)[0]
        harness = r'''
fn main() {
    assert!(validate_esp_mount_record(
        "/dev/vda2", "/run/kyth-installer/esp-journal", "/dev/vda2", "vfat", "ro,relatime", true
    ).is_ok());
    assert!(validate_esp_mount_record(
        "/dev/vda2", "/run/kyth-installer/esp-journal", "/dev/vda3", "vfat", "ro,relatime", true
    ).is_err());
    assert!(validate_esp_mount_record(
        "/dev/vda2", "/run/kyth-installer/esp-journal", "/dev/vda2", "vfat", "rw,relatime", true
    ).is_err());
    assert!(validate_esp_mount_record(
        "/dev/vda2", "/run/kyth-installer/esp-journal", "/dev/vda2", "ext4", "ro,relatime", true
    ).is_err());
}
'''
        with tempfile.TemporaryDirectory(prefix='rowyn-esp-mount-policy-') as tmp:
            source = Path(tmp) / 'probe.rs'
            source.write_text(code + harness)
            subprocess.run(['rustc', '--edition=2021', str(source), '-o', str(Path(tmp) / 'probe')], check=True)
            subprocess.run([str(Path(tmp) / 'probe')], check=True)

    def test_esp_verifier_rejects_cleanup_failure_after_read_check(self):
        policy = load('ubuntu-composefs-source').ESP_POLICY
        code = 'fn finish_esp_check' + policy.split('fn finish_esp_check', 1)[1].split('fn validate_esp_mount_record', 1)[0]
        harness = r'''
fn main() {
    assert!(finish_esp_check(Ok("bootx64.efi landed".into()), true).is_ok());
    assert!(finish_esp_check(Err("missing loader".into()), true).is_err());
    assert!(finish_esp_check(Ok("bootx64.efi landed".into()), false).is_err());
}
'''
        with tempfile.TemporaryDirectory(prefix='rowyn-esp-cleanup-policy-') as tmp:
            source = Path(tmp) / 'probe.rs'
            source.write_text(code + harness)
            subprocess.run(['rustc', '--edition=2021', str(source), '-o', str(Path(tmp) / 'probe')], check=True)
            subprocess.run([str(Path(tmp) / 'probe')], check=True)

    def test_mount_fixture_command_selection_is_explicit_and_fixed(self):
        self.assertEqual(
            mount_fixture_command(privileged=False),
            ['unshare', '--user', '--map-root-user', '--mount', '--propagation', 'private'],
        )
        self.assertEqual(
            mount_fixture_command(privileged=True),
            ['sudo', '-n', 'unshare', '--mount', '--propagation', 'private'],
        )

    def test_util_linux_bind_remount_is_private_and_leaves_source_writable(self):
        script = r'''
set -eu
root="$1"
propagation="$(findmnt --noheadings --output PROPAGATION /)"
test "$propagation" = private
mkdir "$root/source" "$root/private"
mount -t tmpfs tmpfs "$root/source"
touch "$root/source/journal-file"
mount --bind "$root/source" "$root/private"
mount -o remount,bind,ro "$root/private"
opts="$(findmnt --noheadings --mountpoint "$root/private" --output VFS-OPTIONS)"
case ",$opts," in *",ro,"*) ;; *) exit 11 ;; esac
if touch "$root/private/must-fail" 2>/dev/null; then exit 12; fi
touch "$root/source/journal-still-rw"
umount "$root/private"
umount "$root/source"
'''
        with tempfile.TemporaryDirectory(prefix='rowyn-esp-real-mount-') as tmp:
            privileged = os.environ.get('ROWYN_MOUNT_FIXTURE_PRIVILEGED') == '1'
            result = subprocess.run(
                mount_fixture_command(privileged=privileged) + ['sh', '-c', script, 'fixture', tmp],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(os.environ.get('ROWYN_TEST_KYTH_REPO'), 'optional pinned-source integration requires ROWYN_TEST_KYTH_REPO')
class ComposefsAdapterGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rowyn-composefs-source-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.adapter = load('ubuntu-installer-source')
        self.adapter.export_source(Path(os.environ['ROWYN_TEST_KYTH_REPO']), self.root)
        self.adapter.port_source(self.root, 'sha256:' + 'b' * 64)
        self.crate = self.root / 'src/kyth-installer-web/src-tauri/src'

    def test_generated_source_parses_without_losing_existing_tests_or_mount_registry(self):
        for name in ('installer_bootc.rs', 'installer_job_executor.rs', 'installer_configuration.rs',
                     'installer_accounts.rs', 'installer_assurance.rs'):
            result = subprocess.run(['rustfmt', '--edition', '2021', '--emit', 'stdout', str(self.crate / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        executor = (self.crate / 'installer_job_executor.rs').read_text()
        self.assertNotIn('struct MountRegistry', executor)
        config = (self.crate / 'installer_configuration.rs').read_text()
        self.assertIn('mod tests', config)
        self.assertIn('fn for_deployment(', config)

    def test_backend_and_account_flow_use_fixed_mounted_view(self):
        bootc = (self.crate / 'installer_bootc.rs').read_text()
        self.assertIn('argv.push("--composefs-backend".to_string())', bootc)
        self.assertIn('argv.push("--enforce-container-sigpolicy".to_string())', bootc)
        self.assertNotIn('pub composefs_backend:', bootc)
        executor = (self.crate / 'installer_job_executor.rs').read_text()
        method = executor.split('fn mount_wipe_deployment(', 1)[1].split('fn execute_configuration(', 1)[0]
        self.assertLess(method.index('verify_unused_view('), method.index('self.register_mount('))
        self.assertLess(method.index('self.register_mount('), method.index('run_command_timeout('))
        self.assertIn('"--sysroot", WIPE_STAGING_MOUNTPOINT', method)
        self.assertIn('"--latest", DEPLOYMENT_STAGING_MOUNTPOINT', method)
        self.assertIn('account.target_root = deploy_root.clone()', executor)
        self.assertNotIn('UUID={uuid} /boot/efi', executor)
        self.assertIn('verify_mounted_view', (self.crate / 'installer_configuration.rs').read_text())
        self.assertNotIn('.join("ostree/deploy/default/var/home")', (self.crate / 'installer_accounts.rs').read_text())
        assurance = (self.crate / 'installer_assurance.rs').read_text()
        self.assertIn('selected_disk_esp(&input.target_disk)', assurance)
        self.assertIn('validate_esp_match', assurance)
        self.assertIn('verify_esp_bootloaders(&discovered)', assurance)

    def test_generated_source_uses_exact_release_source_and_stable_update_target(self):
        images = (self.root / 'src/kyth-shared-rs/src/system/installer_source.rs').read_text()
        self.assertIn('ROWYN_RELEASE_SOURCE', images)
        self.assertIn('ghcr.io/kyth-os/rowyn@sha256:' + 'b' * 64, images)
        self.assertIn('ROWYN_UPDATE_TARGET', images)
        self.assertIn('ghcr.io/kyth-os/rowyn:stable', images)
        executor = (self.crate / 'installer_job_executor.rs').read_text()
        self.assertIn('self.source_imgref != format!("docker://{ROWYN_RELEASE_SOURCE}")', executor)
        self.assertIn('self.target_imgref != ROWYN_UPDATE_TARGET', executor)

    def test_repeated_port_verifies_digest_and_all_source_instead_of_skipping_trust(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.crate.glob('*.rs')}
        self.adapter.port_source(self.root, 'sha256:' + 'b' * 64)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.crate.glob('*.rs')})
        with self.assertRaises(ValueError):
            self.adapter.port_source(self.root, 'sha256:' + 'c' * 64)
        executor = self.crate / 'installer_job_executor.rs'
        executor.write_text(executor.read_text() + '\n// foreign modification\n')
        with self.assertRaises(ValueError):
            self.adapter.port_source(self.root, 'sha256:' + 'b' * 64)
