#!/usr/bin/env python3
"""Export the minimal Apache-2.0 Kyth installer source, then port it to Ubuntu.

Never executes upstream installer/Containerfile, installer/build.sh or ISO wrappers.
The input checkout is read-only; git archive exports exactly the pinned objects.
"""
import argparse
import io
from pathlib import Path
import re
import subprocess
import tarfile

PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
PATHS = ("src/kyth-installer-web", "src/kyth-shared-rs",
         "third_party/gtk-glib-0.18.5", "build_files/exe-handler-apps.json",
         "build_files/kyth-launch-installer", "build_files/kyth-installerd.service", "LICENSE")
IDENTITY = "https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main"


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one source anchor: {old[:100]!r}")
    return text.replace(old, new)


def export_source(repo, destination):
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", PIN + "^{commit}"], text=True).strip()
    if actual != PIN:
        raise ValueError("installer source commit mismatch")
    if destination.exists():
        raise ValueError("source destination must not exist (export fresh pinned objects)")
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", PIN, *PATHS])
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        # node_modules is an upstream symlink to the developer's workspace.
        # Never export symlinks, submodules, or anything outside the allowlist.
        for member in stream.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                continue
            path = destination / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            member_stream = stream.extractfile(member)
            assert member_stream is not None
            path.write_bytes(member_stream.read())
            path.chmod(member.mode & 0o777)


def port_source(root, digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid release digest")
    crate = root / "src/kyth-installer-web/src-tauri"
    executor = crate / "src/installer_job_executor.rs"
    text = executor.read_text()
    source = "ghcr.io/kyth-os/rowyn@" + digest
    anchor = 'const FILESYSTEM_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/alongside-target";\n'
    text = replace_once(text, anchor, anchor + f'const ROWYN_RELEASE_SOURCE: &str = "{source}";\n')
    # All transports, source and future target are bound to this ISO's release.
    # Upstream's non-docker fast path must never bypass the signature check.
    anchor = '        let digest = match source.rfind(\'@\') {'
    text = replace_once(text, anchor, '''        if source != format!("docker://{ROWYN_RELEASE_SOURCE}") {
            return Err(refuse("source is not this Rowyn release".to_string()));
        }
''' + anchor)
    anchor = '        Ok(())\n    }\n\n    fn check_storage_preflight('
    text = replace_once(text, anchor, '''        let mut cosign = Command::new("/usr/bin/kyth-cosign");
        cosign.args([
            "verify", "--certificate-identity",
            "https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main",
            "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com",
            ROWYN_RELEASE_SOURCE,
        ]).env("TUF_ROOT", "/run/kyth-installer/sigstore-tuf");
        std::fs::create_dir_all("/run/kyth-installer/sigstore-tuf")
            .map_err(|error| refuse(format!("cosign cache: {error}")))?;
        super::installer_stream::run_command_timeout(
            &mut cosign, || false, std::time::Duration::from_secs(180),
        ).map_err(|error| refuse(format!("cosign verification failed: {error}")))?;
''' + anchor)
    anchor = '            Phase::Prepare => {\n'
    text = replace_once(text, anchor, anchor + '''                // Rowyn Ubuntu port: fail before any partition/format/resize.
                if self.source_imgref != format!("docker://{ROWYN_RELEASE_SOURCE}")
                    || self.target_imgref != ROWYN_RELEASE_SOURCE
                {
                    return Err(NativePhaseError::Execution { phase,
                        message: "source and target must be this signed Rowyn release".into() });
                }
                self.verify_install_source(phase)?;
                if self.storage_plan.mode != "wipe" || !matches!(self.bootc_request.encryption.as_str(), "" | "none") {
                    return Err(NativePhaseError::Execution { phase,
                        message: "Ubuntu port currently supports whole-disk plaintext installs only; manual, alongside, resize and TPM encryption are not qualified".into() });
                }
                let mut preflight = Command::new("/usr/libexec/rowyn-live-preflight");
                preflight.arg(&self.storage_plan.disk);
                super::installer_stream::run_command_timeout(
                    &mut preflight, || false, std::time::Duration::from_secs(30),
                ).map_err(|error| NativePhaseError::Execution { phase,
                    message: format!("Ubuntu live preflight failed: {error}") })?;
                if !std::path::Path::new("/sys/firmware/efi").is_dir() {
                    return Err(NativePhaseError::Execution { phase,
                        message: "Rowyn currently requires UEFI firmware (CSM/BIOS unsupported)".into() });
                }
                let state = Command::new("/usr/bin/mokutil").arg("--sb-state").output();
                if !state.map(|o| o.status.success() && String::from_utf8_lossy(&o.stdout).contains("SecureBoot disabled")).unwrap_or(false) {
                    return Err(NativePhaseError::Execution { phase,
                        message: "Disable Secure Boot: Rowyn's systemd-boot chain is not qualified; no Kyth MOK is used".into() });
                }
''')
    executor.write_text(text)
    storage = crate / "src/installer_storage.rs"
    text = storage.read_text()
    text = replace_once(text, 'const PROTECTED_MOUNT_TARGETS: [&str; 6]', 'const PROTECTED_MOUNT_TARGETS: [&str; 8]')
    text = replace_once(text, '    "/run/initramfs/iso",', '    "/run/initramfs/iso",\n    "/cdrom",\n    "/isodevice",')
    storage.write_text(text)
    images = root / "src/kyth-shared-rs/src/system/installer_source.rs"
    text = images.read_text()
    start = text.index('pub fn install_images(')
    end = text.index('\n}\n', start) + 2
    text = text[:start] + '''pub fn install_images(_kernel: &str, source_image: &str, target_image: &str) -> (String, String) {
    // Rowyn has one Ubuntu kernel. Never append a variant suffix to a digest.
    (source_image_ref(source_image, source_image), target_image.to_string())
}''' + text[end:]
    images.write_text('// Rowyn: immutable Ubuntu source for every frontend.\n' + text)
    bootc = crate / "src/installer_bootc.rs"
    text = bootc.read_text()
    text = replace_once(text, '        subcommand.clone(),', '        subcommand.clone(),\n        "--bootloader=systemd".to_string(),')
    bootc.write_text(text)
    daemon = crate / "src/installer_daemon.rs"
    text = daemon.read_text()
    anchor = '    fn dispatch(&self, route: &str, value: serde_json::Value) -> (u16, serde_json::Value) {\n'
    text = replace_once(text, anchor, anchor + '''        // Rowyn: manual mutation bypasses the job Prepare trust gate upstream.
        // Disable this separate API entirely until it has equivalent gates.
        if route.starts_with("/api/disk/") {
            return (403, serde_json::json!({"ok": false, "message": "Rowyn Ubuntu port: manual partition operations are unsupported"}));
        }
''')
    daemon.write_text(text)
    # Ubuntu account administration and shell differ from Fedora.
    accounts = crate / "src/installer_accounts.rs"
    text = accounts.read_text().replace('wheel', 'sudo').replace('/bin/zsh', '/bin/bash')
    accounts.write_text(text)
    config = crate / "src/installer_configuration.rs"
    text = config.read_text()
    text = replace_once(text, '    apply_boot_ordering_fix(&plan.target_root)?;\n',
                        '    // Rowyn: Ubuntu has no Kyth system-account repair unit.\n')
    text = text.replace('{etc}/locale.conf', '{etc}/default/locale')
    text = text.replace('{etc}/vconsole.conf', '{etc}/default/keyboard').replace('KEYMAP={keymap}', 'XKBLAYOUT={keymap}')
    config.write_text(text)
    assurance = crate / "src/installer_assurance.rs"
    text = assurance.read_text().replace('etc.join("locale.conf")', 'etc.join("default/locale")')
    text = text.replace('etc.join("vconsole.conf")', 'etc.join("default/keyboard")').replace('KEYMAP={}', 'XKBLAYOUT={}')
    start = text.index('fn kernel_detail(')
    end = text.index('\n}\n', start) + 2
    text = text[:start] + '''fn kernel_detail(_root: &Path, deploy: &Path) -> Result<String, String> {
    // Rowyn: the Ubuntu bootc payload stores a matched pair in each kver.
    // A standalone vmlinuz, or initramfs from another version, is not proof.
    let modules = fs::read_dir(deploy.join("usr/lib/modules"))
        .map_err(|error| format!("cannot inspect installed kernel modules: {error}"))?;
    for entry in modules.flatten() {
        let dir = entry.path();
        let nonempty_file = |name: &str| fs::symlink_metadata(dir.join(name))
            .map(|m| m.is_file() && m.len() > 0).unwrap_or(false);
        if nonempty_file("vmlinuz") && nonempty_file("initramfs.img") {
            return Ok(format!("matching Ubuntu bootc kernel and initramfs: {}", entry.file_name().to_string_lossy()));
        }
    }
    Err("installed Ubuntu payload lacks a matching kernel/initramfs pair".into())
}''' + text[end:]
    assurance.write_text(text)
    # Ubuntu's util-linux puts mount/umount and lsblk in /usr/bin, not /usr/sbin.
    for path in (crate / "src").glob("*.rs"):
        text = path.read_text()
        for binary in ("mount", "umount", "lsblk"):
            text = text.replace(f'/usr/sbin/{binary}', f'/usr/bin/{binary}')
        text = text.replace('/usr/bin/blkid', '/usr/sbin/blkid')
        text = text.replace('ghcr.io/kyth-os/kyth:latest', source)
        text = text.replace('"fedora"', '"ubuntu"')
        path.write_text('// Rowyn: modified for the Ubuntu-only installer port.\n' + text)
    secure = crate / "src/installer_secure_boot.rs"
    text = secure.read_text()
    text = replace_once(text, '    let kernel = input.kernel.trim().to_ascii_lowercase();', '''    if input.force_stage || input.kernel != "ubuntu" {
        return Err("Rowyn does not support custom kernels or Kyth MOK enrollment".into());
    }
    let kernel = input.kernel.trim().to_ascii_lowercase();''')
    text = text.replace('standard KythOS kernel does not require custom MOK enrollment',
                        'Rowyn uses the signed Ubuntu payload kernel; Secure Boot must remain disabled')
    secure.write_text(text)
    ui = crate / "ui/installer.slint"
    text = ui.read_text()
    text = replace_once(text, '                                Button { text: "CachyOS"; clicked => { kernel = "cachyos"; select-kernel("cachyos"); } }\n', '')
    text = text.replace('"fedora"', '"ubuntu"').replace('"Fedora"', '"Ubuntu"').replace('KythOS', 'Rowyn')
    for label in ('Keep Windows', 'Replace partition', 'Free space', 'Manual', 'TPM2'):
        text = replace_once(text, f'Button {{ text: "{label}"; clicked', f'Button {{ text: "{label}"; enabled: false; clicked')
    text = text.replace('Install request', 'Install Rowyn: UEFI, Secure Boot OFF; whole-disk plaintext only')
    text = text.replace('TPM2 needs a to-disk install and a recovery key plan.', 'Encryption and non-wipe modes are not qualified for this Ubuntu port.')
    ui.write_text('// Rowyn: Ubuntu-only kernel choice and identity.\n' + text)
    native = crate / "src/native_main.rs"
    native.write_text(native.read_text().replace('KythOS', 'Rowyn'))
    launcher = root / "build_files/kyth-launch-installer"
    text = launcher.read_text().replace('/usr/bin/kyth-installer-shell --socket-path',
                                        '/usr/bin/kyth-installer-native --socket-path')
    launcher.write_text(text.replace('KythOS', 'Rowyn'))
    service = root / "build_files/kyth-installerd.service"
    text = service.read_text().replace('EnvironmentFile=-', 'EnvironmentFile=')
    text = text.replace('RestrictAddressFamilies=AF_UNIX\n', 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n')
    service.write_text(text.replace('KythOS', 'Rowyn'))
    (root / 'ROWYN-PORT-NOTICE').write_text(
        f'Kyth installer source {PIN}, Apache-2.0 (LICENSE).\n'
        'Modified by Rowyn: Ubuntu accounts, tool paths, native UI, source trust, firmware policy.\n'
        'Upstream OS/image/ISO assembly scripts are not included or invoked.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("digest")
    args = parser.parse_args()
    export_source(args.repo, args.destination)
    port_source(args.destination, args.digest)
    print(f"Ported pinned installer {PIN} to {args.destination}")


if __name__ == "__main__":
    main()
