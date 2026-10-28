#!/usr/bin/env python3
"""Compile source-derived immutable-reference regression probes with Ubuntu rustc.

The functions are extracted afresh from the exported real Rust source, not
reimplemented in a test double. No disks, registry calls or host installs.
"""
from pathlib import Path
import subprocess
import sys


def probes(root):
    source = (root / 'src/kyth-shared-rs/src/system/installer_source.rs').read_text()
    functions = []
    for name in ('source_image_ref', 'install_images'):
        start = source.index('pub fn ' + name + '(')
        end = source.index('\n}', start) + 2
        functions.append(source[start:end])
    constants = '\n'.join(
        line for line in source.splitlines()
        if line.startswith('pub const ROWYN_')
    )
    assurance = (root / 'src/kyth-installer-web/src-tauri/src/installer_assurance.rs').read_text()
    start = assurance.index('fn kernel_detail(')
    end = assurance.index('\n}', start) + 2
    functions.append(assurance[start:end])
    return 'use std::{fs, path::Path};\n' + constants + '\n' + '\n'.join(functions) + '''
#[test]
fn every_selector_preserves_the_release_digest() {
    let reference = format!("ghcr.io/kyth-os/rowyn@sha256:{}", "a".repeat(64));
    for selector in ["ubuntu", "fedora", "cachyos", "", "untrusted"] {
        let (source, target) = install_images(selector, &reference, &reference);
        assert_eq!(source, format!("docker://{reference}"));
        assert_eq!(target, "ghcr.io/kyth-os/rowyn:stable");
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
    assert_eq!(install_images("ubuntu", &transported, &reference),
        (transported, "ghcr.io/kyth-os/rowyn:stable".to_string()));
}
#[test]
fn caller_target_cannot_replace_the_fixed_update_channel() {
    let reference = format!("ghcr.io/kyth-os/rowyn@sha256:{}", "c".repeat(64));
    let (_, target) = install_images("ubuntu", &reference, "ghcr.io/kyth-os/rowyn:old");
    assert_eq!(target, "ghcr.io/kyth-os/rowyn:stable");
}
#[test]
fn empty_source_still_selects_the_exact_docker_release() {
    let (source, target) = install_images("ubuntu", "", "ghcr.io/kyth-os/rowyn:old");
    assert_eq!(source, format!("docker://{ROWYN_RELEASE_SOURCE}"));
    assert_eq!(target, "ghcr.io/kyth-os/rowyn:stable");
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
