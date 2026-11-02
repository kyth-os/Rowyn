#!/usr/bin/env python3
"""Compile source-derived immutable-reference regression probes with Ubuntu rustc.

The functions are extracted afresh from the exported real Rust source, not
reimplemented in a test double. No disks, registry calls or host installs.

The release-source contract lives in the installer crate since the legacy
junk cut removed the shared crate's image-source selection helpers: the
bootc plan normalizes references (installer_bootc.rs), the executor pins
the release constants and refuses any other source before partitioning
or fetching (installer_job_executor.rs), and assurance checks the
installed kernel pair (installer_assurance.rs).
"""
from pathlib import Path
import subprocess
import sys


def extract_function(source, name):
    start = source.index('fn ' + name + '(')
    end = source.index('\n}', start) + 2
    return source[start:end]


def probes(root):
    crate = root / 'src/rowyn-installer-web/src-tauri/src'
    executor = (crate / 'installer_job_executor.rs').read_text()
    constants = '\n'.join(
        line for line in executor.splitlines()
        if line.startswith('const ROWYN_')
    )
    for name in ('ROWYN_RELEASE_SOURCE', 'ROWYN_UPDATE_TARGET'):
        if f'const {name}:' not in constants:
            raise ValueError(f'installer_job_executor.rs no longer pins {name}')
    functions = [extract_function(executor, 'is_sha256_digest')]
    bootc = (crate / 'installer_bootc.rs').read_text()
    for name in ('safe_reference', 'source_reference'):
        functions.append(extract_function(bootc, name))
    assurance = (crate / 'installer_assurance.rs').read_text()
    functions.append(extract_function(assurance, 'kernel_detail'))
    return 'use std::{fs, path::Path};\n' + constants + '\n' + '\n'.join(functions) + '''
#[test]
fn only_the_pinned_release_matches_the_gate() {
    // The executor's Prepare and image gates accept exactly one source:
    // the pinned release reference behind the docker:// transport. The
    // bootc plan's normalization must land the bare release ref on that
    // exact string, and no plausible substitute may normalize onto it.
    let gate = format!("docker://{ROWYN_RELEASE_SOURCE}");
    assert_eq!(source_reference(ROWYN_RELEASE_SOURCE).unwrap(), gate);
    assert_eq!(source_reference(&gate).unwrap(), gate);
    let digest = ROWYN_RELEASE_SOURCE.rsplit_once('@').unwrap().1;
    let hex = &digest[7..];
    let first = if hex.as_bytes()[0] == b'a' { 'b' } else { 'a' };
    let other_digest = format!("ghcr.io/mrtrickdotorg/rowyn@sha256:{first}{}", &hex[1..]);
    for candidate in [ROWYN_UPDATE_TARGET, other_digest.as_str(),
                      "docker.io/library/ubuntu:26.10",
                      "docker://ghcr.io/mrtrickdotorg/rowyn:stable"] {
        assert_ne!(source_reference(candidate).unwrap(), gate, "{candidate}");
    }
    for transported in [format!("containers-storage:{ROWYN_RELEASE_SOURCE}"),
                        format!("oci:{ROWYN_RELEASE_SOURCE}"),
                        format!("ostree:{ROWYN_RELEASE_SOURCE}")] {
        assert_ne!(source_reference(&transported).unwrap(), gate, "{transported}");
    }
}
#[test]
fn kernel_requires_nonempty_matching_initramfs_in_same_version() {
    let root = std::env::temp_dir().join(format!("rowyn-source-probe-{}", std::process::id()));
    let one = root.join("usr/lib/modules/ubuntu-one");
    let two = root.join("usr/lib/modules/ubuntu-two");
    fs::create_dir_all(&one).unwrap(); fs::create_dir_all(&two).unwrap();
    fs::write(one.join("vmlinuz"), "kernel").unwrap();
    assert!(kernel_detail(&root, &root).is_err());
    fs::write(two.join("initramfs.img"), "wrong-version").unwrap();
    assert!(kernel_detail(&root, &root).is_err());
    fs::write(one.join("initramfs.img"), "").unwrap();
    assert!(kernel_detail(&root, &root).is_err());
    fs::write(one.join("initramfs.img"), "matching-initramfs").unwrap();
    assert!(kernel_detail(&root, &root).is_ok());
    fs::remove_dir_all(&root).unwrap();
}
#[test]
fn transport_is_not_duplicated() {
    let reference = format!("ghcr.io/kyth-os/rowyn@sha256:{}", "b".repeat(64));
    let transported = format!("docker://{reference}");
    assert_eq!(source_reference(&transported).unwrap(), transported);
    assert_eq!(source_reference(&reference).unwrap(), transported);
}
#[test]
fn caller_target_cannot_replace_the_fixed_update_channel() {
    // The update channel is a pinned constant, not a caller choice: the
    // The executor's Prepare gate requires target_imgref == ROWYN_UPDATE_TARGET,
    // a mutable destination stable tag rather than a caller-supplied digest.
    assert_eq!(ROWYN_UPDATE_TARGET, "ghcr.io/mrtrickdotorg/rowyn:stable");
    let (source_repo, digest) = ROWYN_RELEASE_SOURCE.rsplit_once('@').unwrap();
    assert_eq!(source_repo, "ghcr.io/kyth-os/rowyn");
    assert!(is_sha256_digest(digest));
    assert!(!ROWYN_UPDATE_TARGET.contains('@'));
}
#[test]
fn empty_source_is_refused_and_the_default_is_the_exact_release() {
    // An empty source no longer falls back silently: safe_reference
    // rejects it before any normalization, so the install fails closed.
    // The job's default source — the same pinned ref the port bakes into
    // ROWYN_RELEASE_SOURCE — normalizes to exactly the docker:// form
    // the executor gates require.
    assert!(source_reference("").is_err());
    assert!(source_reference("   ").is_err());
    assert_eq!(source_reference(ROWYN_RELEASE_SOURCE).unwrap(),
        format!("docker://{ROWYN_RELEASE_SOURCE}"));
}
'''


def main():
    root, destination = map(Path, sys.argv[1:])
    destination.mkdir(parents=True, exist_ok=True)
    source = destination / 'source-probes.rs'
    source.write_text(probes(root))
    binary = destination / 'source-probes'
    subprocess.run(['rustc', '--edition=2021', '--test', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
