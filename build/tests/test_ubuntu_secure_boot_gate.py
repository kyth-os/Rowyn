"""Behavioral tests for the emitted Ubuntu Secure Boot preflight policy."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PINNED_REPO = Path(os.environ.get("ROWYN_TEST_KYTH_REPO", "/var/home/phendrick/git/kyth.worktrees/main"))
PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"


def load_port_module():
    spec = importlib.util.spec_from_file_location(
        "ubuntu_source_secure_boot", ROOT / "build/ubuntu-installer-source.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


port = load_port_module()


class EmittedSecureBootGateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("rustc"), "rustc is required for emitted Rust behavior tests")
    def test_source_extracted_policy_rejects_unsafe_states_and_accepts_only_corroborated_states(self):
        with tempfile.TemporaryDirectory(prefix="rowyn-secure-boot-") as temp:
            exported = Path(temp) / "source"
            port.export_source(PINNED_REPO, exported)
            port.port_source(exported, "sha256:" + "b" * 64)
            source = (exported / "src/kyth-installer-web/src-tauri/src/installer_job_executor.rs").read_text()
            self.assertIn('env("LC_ALL", "C")', source)
            self.assertIn("run_output_timeout_capture", source)
            self.assertIn("/sys/firmware/efi/efivars", source)
            self.assertIn("EFI variables are not supported on the system.", source)

            begin = source.index("// ROWYN_SECURE_BOOT_POLICY_BEGIN")
            end = source.index("// ROWYN_SECURE_BOOT_POLICY_END", begin)
            policy = source[begin:end]
            probe = Path(temp) / "policy.rs"
            probe.write_text(
                policy
                + r'''
fn evidence(status: Option<i32>, stdout: &str, stderr: &str) -> SecureBootCommandEvidence {
    SecureBootCommandEvidence {
        status,
        stdout: stdout.to_string(),
        stderr: stderr.to_string(),
        error: None,
    }
}

fn main() {
    let disabled_boot = evidence(Some(0), "Secure Boot: disabled\n", "");
    let disabled_mok = evidence(Some(0), "SecureBoot disabled\n", "");
    assert_eq!(
        classify_secure_boot_evidence(true, Some(false), &disabled_boot, &disabled_mok).unwrap(),
        SecureBootProbeState::Disabled
    );

    let unsupported_boot = evidence(Some(0), "Secure Boot: disabled (unsupported)\n", "");
    let unsupported_mok = evidence(
        Some(255),
        "",
        "EFI variables are not supported on the system.\n",
    );
    assert_eq!(
        classify_secure_boot_evidence(true, None, &unsupported_boot, &unsupported_mok).unwrap(),
        SecureBootProbeState::Unsupported
    );

    let enabled_boot = evidence(Some(0), "Secure Boot: enabled\n", "");
    let enabled_mok = evidence(Some(0), "SecureBoot enabled\n", "");
    let enabled = classify_secure_boot_evidence(true, Some(true), &enabled_boot, &enabled_mok)
        .expect_err("enabled must be rejected");
    assert!(enabled.contains("enabled"), "{enabled}");

    let contradictory = classify_secure_boot_evidence(true, Some(false), &enabled_boot, &disabled_mok)
        .expect_err("contradictory command states must be rejected");
    assert!(contradictory.contains("contradictory"), "{contradictory}");

    let empty = evidence(Some(0), "", "");
    let ambiguous = classify_secure_boot_evidence(true, None, &empty, &empty)
        .expect_err("empty command output must be rejected");
    assert!(ambiguous.contains("bootctl") || ambiguous.contains("unknown"), "{ambiguous}");

    let exec_failure = SecureBootCommandEvidence {
        status: None,
        stdout: String::new(),
        stderr: String::new(),
        error: Some("Permission denied (os error 13)".to_string()),
    };
    let failed = classify_secure_boot_evidence(true, None, &exec_failure, &unsupported_mok)
        .expect_err("exec/permission failures must be rejected");
    assert!(failed.contains("Permission denied (os error 13)"), "{failed}");

    let unreadable_efi = classify_secure_boot_evidence(false, None, &unsupported_boot, &unsupported_mok)
        .expect_err("unsupported output cannot hide an EFI read failure");
    assert!(unreadable_efi.contains("EFI variable filesystem"), "{unreadable_efi}");
}
'''
            )
            binary = Path(temp) / "policy"
            compiled = subprocess.run(
                ["rustc", str(probe), "-o", str(binary)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
