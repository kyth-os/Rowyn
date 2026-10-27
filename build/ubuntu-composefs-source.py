#!/usr/bin/env python3
"""Narrow Ubuntu/systemd-boot composefs adaptation of the pinned installer."""
import hashlib
import json
import os
from pathlib import Path


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError('composefs source anchor changed: ' + old[:100])
    return text.replace(old, new, 1)


def fingerprints(root):
    # Only exported installer inputs, never build outputs or dependency trees.
    result = {}
    for base in ('src/kyth-installer-web', 'src/kyth-shared-rs',
                 'third_party/gtk-glib-0.18.5', 'build_files'):
        for directory, dirs, files in os.walk(root / base, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ('target', 'node_modules', 'dist', '.git')]
            for name in files:
                path = Path(directory) / name
                if path.is_file() and not path.is_symlink():
                    result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('LICENSE', 'ROWYN-PORT-NOTICE'):
        path = root / name
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def tool_fingerprint():
    return hashlib.sha256(Path(__file__).read_bytes() +
                          Path(__file__).with_name('ubuntu-installer-source.py').read_bytes()).hexdigest()


def completed(root, digest):
    state = root / '.rowyn-port-state.json'
    if not state.exists():
        return False
    expected = json.loads(state.read_text())
    if (expected.get('digest') != digest or expected.get('tool') != tool_fingerprint()
            or expected.get('files') != fingerprints(root)):
        raise ValueError('previously adapted installer differs; re-export the pinned source')
    return True


def record(root, digest):
    (root / '.rowyn-port-state.json').write_text(json.dumps(
        dict(digest=digest, tool=tool_fingerprint(), files=fingerprints(root)), sort_keys=True))


MOUNT_POLICY = r'''
/// Validate the fixed, executor-owned bootc deployment view before any writes.
/// Probe results contain real-directory status and exact findmnt target/options.
pub(crate) fn validate_view_with<F>(physical: &str, view: &str, mut probe: F) -> Result<(), String>
where F: FnMut(&Path) -> Result<(bool, String), String> {
    if physical != "/run/kyth-installer/install-root"
        || view != "/run/kyth-installer/deployment-root" {
        return Err("deployment view is not the executor-owned mountpoint".into());
    }
    for (path, writable) in [(physical.to_string(), false), (view.to_string(), false),
        (format!("{view}/etc"), true), (format!("{view}/var"), true)] {
        let (real, record) = probe(Path::new(&path))?;
        if !real { return Err(format!("deployment path must be a real directory: {path}")); }
        let lines: Vec<_> = record.lines().filter(|line| !line.trim().is_empty()).collect();
        if lines.len() != 1 { return Err(format!("missing or ambiguous deployment mount: {path}")); }
        let fields: Vec<_> = lines[0].split_whitespace().collect();
        if fields.len() != 2 || fields[0] != path {
            return Err(format!("deployment mount does not match exact path: {path}"));
        }
        let options: Vec<_> = fields[1].split(',').collect();
        if writable && !options.contains(&"rw") {
            return Err(format!("deployment state mount is not writable: {path}"));
        }
        if path == view && !options.contains(&"ro") {
            return Err("deployment image mount is not read-only".into());
        }
    }
    Ok(())
}

pub(crate) fn verify_unused_view() -> Result<(), String> {
    let path = Path::new("/run/kyth-installer/deployment-root");
    let real = fs::symlink_metadata(path)
        .map(|m| m.is_dir() && !m.file_type().is_symlink()).unwrap_or(false);
    if !real || fs::read_dir(path).map_err(|e| e.to_string())?.next().is_some() {
        return Err("deployment mountpoint must be a real empty directory".into());
    }
    let output = std::process::Command::new("/usr/bin/findmnt")
        .args(["--noheadings", "--mountpoint"]).arg(path).output()
        .map_err(|e| format!("cannot check deployment mountpoint ownership: {e}"))?;
    if output.status.code() != Some(1) {
        return Err("deployment mountpoint is already mounted or cannot be verified".into());
    }
    Ok(())
}

pub(crate) fn verify_mounted_view(physical: &str, view: &str) -> Result<(), String> {
    validate_view_with(physical, view, |path| {
        let real = fs::symlink_metadata(path)
            .map(|m| m.is_dir() && !m.file_type().is_symlink()).unwrap_or(false);
        if !real { return Ok((false, String::new())); }
        let output = std::process::Command::new("/usr/bin/findmnt")
            .args(["--noheadings", "--raw", "--output", "TARGET,OPTIONS", "--mountpoint"])
            .arg(path).output().map_err(|e| format!("cannot inspect deployment mounts: {e}"))?;
        if !output.status.success() { return Err(format!("not a deployment mountpoint: {}", path.display())); }
        if output.stdout.len() > 16384 { return Err("deployment mount record too large".into()); }
        Ok((true, String::from_utf8(output.stdout).map_err(|_| "invalid mount record".to_string())?))
    })
}
'''

MOUNT_METHOD = r'''    fn mount_wipe_deployment(
        &self, phase: Phase, cancellation: &CancellationToken,
    ) -> Result<(), NativePhaseError> {
        self.execute_disk_helper(phase, cancellation, &serde_json::json!({
            "operation": "ensure_directory", "path": DEPLOYMENT_STAGING_MOUNTPOINT,
        }))?;
        installer_configuration::verify_unused_view()
            .map_err(|message| NativePhaseError::Execution { phase, message })?;
        // Register before spawning: timeout/cancellation/partial assembly must
        // recursively clean this view before the physical sysroot too.
        self.register_mount(DEPLOYMENT_STAGING_MOUNTPOINT)?;
        let mut command = Command::new("/usr/bin/bootc");
        command.args(["install", "mount", "--sysroot", WIPE_STAGING_MOUNTPOINT,
            "--latest", DEPLOYMENT_STAGING_MOUNTPOINT]);
        let status = super::installer_stream::run_command_timeout(
            &mut command, || cancellation.is_cancelled(), std::time::Duration::from_secs(180),
        ).map_err(|message| NativePhaseError::Execution { phase, message })?;
        if !status.success() {
            return Err(NativePhaseError::Execution { phase,
                message: format!("bootc install mount failed: {status}") });
        }
        installer_configuration::verify_mounted_view(
            WIPE_STAGING_MOUNTPOINT, DEPLOYMENT_STAGING_MOUNTPOINT,
        ).map_err(|message| NativePhaseError::Execution { phase, message })
    }

'''

ESP_POLICY = r'''
/// Resolve the selected disk's actual ESP, preserving UUID alias validation.
fn selected_disk_esp(target_disk: &str) -> Result<String, String> {
    let disk = crate::installer_plan::normalize_device_path(target_disk)
        .ok_or_else(|| "invalid installed target disk".to_string())?;
    let snapshot = command_output("/usr/bin/lsblk", &["--json", "--bytes", "--paths", "--output",
        "NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTN,PARTUUID,LABEL,MOUNTPOINT,MOUNTPOINTS,START,RO,PKNAME,PTTYPE", &disk])?;
    let sector_size = command_output("/usr/sbin/blockdev", &["--getss", &disk])?
        .trim().parse::<u64>().map_err(|_| "invalid disk sector size".to_string())?;
    let efi = crate::installer_storage::efi_partition_from_snapshot(&snapshot, &disk, sector_size)?
        .ok_or_else(|| "selected disk has no EFI system partition".to_string())?;
    let uuid = crate::installer_probe::lookup_uuid(crate::installer_probe::UuidInput { device: efi.name.clone() })?;
    let alias = format!("/dev/disk/by-uuid/{uuid}");
    if fs::canonicalize(&alias).map_err(|e| e.to_string())?
        != fs::canonicalize(&efi.name).map_err(|e| e.to_string())? {
        return Err("selected ESP UUID does not resolve to its partition".into());
    }
    Ok(alias)
}

fn validate_esp_match(fstab: Option<&str>, discovered: &str) -> Result<(), String> {
    if let Some(fstab) = fstab {
        if fs::canonicalize(fstab).map_err(|e| e.to_string())?
            != fs::canonicalize(discovered).map_err(|e| e.to_string())? {
            return Err("fstab ESP does not match selected disk ESP".into());
        }
    }
    Ok(())
}

fn finish_esp_check(check: Result<String, String>, unmounted: bool) -> Result<String, String> {
    if !unmounted {
        return Err("ESP bootloader check mount could not be released".into());
    }
    check
}

fn validate_esp_mount_record(
    selected: &str, target: &str, source: &str, fstype: &str, vfs_options: &str,
    require_readonly: bool,
) -> Result<(), String> {
    if !target.starts_with('/') || target == "/" {
        return Err("ESP mountpoint is not an absolute private directory".into());
    }
    if source != selected {
        return Err("ESP mount source does not match the selected partition".into());
    }
    if !fstype.eq_ignore_ascii_case("vfat") {
        return Err("selected ESP mount is not vfat".into());
    }
    let options: Vec<&str> = vfs_options.split(',').map(str::trim).collect();
    if require_readonly && (!options.contains(&"ro") || options.contains(&"rw")) {
        return Err("ESP verification mount is not read-only".into());
    }
    Ok(())
}

fn mounted_esp_records(output: &str) -> Result<Vec<(String, String, String, String)>, String> {
    fn walk(value: &serde_json::Value, records: &mut Vec<(String, String, String, String)>) {
        if let Some(object) = value.as_object() {
            if let (Some(target), Some(source), Some(fstype), Some(options)) = (
                object.get("target").and_then(serde_json::Value::as_str),
                object.get("source").and_then(serde_json::Value::as_str),
                object.get("fstype").and_then(serde_json::Value::as_str),
                object.get("vfs-options").and_then(serde_json::Value::as_str),
            ) {
                records.push((target.into(), source.into(), fstype.into(), options.into()));
            }
            for key in ["filesystems", "children"] {
                if let Some(children) = object.get(key) {
                    walk(children, records);
                }
            }
        } else if let Some(array) = value.as_array() {
            for child in array { walk(child, records); }
        }
    }
    let value: serde_json::Value = serde_json::from_str(output)
        .map_err(|error| format!("could not parse ESP mount table: {error}"))?;
    let mut records = Vec::new();
    walk(&value, &mut records);
    Ok(records)
}

fn canonical_block_device(path: &str) -> Result<PathBuf, String> {
    use std::os::unix::fs::FileTypeExt;
    let canonical = fs::canonicalize(path)
        .map_err(|error| format!("ESP device {path} is not usable: {error}"))?;
    let metadata = fs::symlink_metadata(&canonical)
        .map_err(|error| format!("ESP device {path} is not usable: {error}"))?;
    if !metadata.file_type().is_block_device() {
        return Err(format!("ESP device {path} is not a block device"));
    }
    Ok(canonical)
}

fn mounted_esp_target(device: &Path) -> Result<Option<String>, String> {
    let output = command_output("/usr/bin/findmnt", &[
        "--json", "--real", "--output", "TARGET,SOURCE,FSTYPE,VFS-OPTIONS",
    ])?;
    let selected = device.to_string_lossy();
    let mut matches = Vec::new();
    for (target, source, fstype, options) in mounted_esp_records(&output)? {
        let Ok(canonical_source) = canonical_block_device(&source) else { continue; };
        if canonical_source == device {
            let target_path = Path::new(&target);
            let real = fs::symlink_metadata(target_path)
                .map(|metadata| metadata.is_dir() && !metadata.file_type().is_symlink())
                .unwrap_or(false);
            if real && fstype.eq_ignore_ascii_case("vfat") {
                validate_esp_mount_record(&selected, &target, &canonical_source.to_string_lossy(), &fstype, &options, false)?;
                matches.push(target);
            }
        }
    }
    if matches.iter().any(|target| target == "/run/kyth-installer/esp-journal") {
        return Ok(Some("/run/kyth-installer/esp-journal".into()));
    }
    match matches.len() {
        0 => Ok(None),
        1 => Ok(matches.into_iter().next()),
        _ => Err("selected ESP has multiple ambiguous existing mounts".into()),
    }
}

fn verify_esp_mount(target: &Path, device: &Path) -> Result<(), String> {
    let target_string = target.to_string_lossy().into_owned();
    let output = command_output("/usr/bin/findmnt", &[
        "--json", "--mountpoint", &target_string,
        "--output", "TARGET,SOURCE,FSTYPE,VFS-OPTIONS",
    ])?;
    let records = mounted_esp_records(&output)?;
    if records.len() != 1 {
        return Err("ESP verification mount is missing or ambiguous".into());
    }
    let (record_target, source, fstype, options) = &records[0];
    let canonical_source = canonical_block_device(source)?;
    if canonical_source != device {
        return Err("ESP verification mount source changed".into());
    }
    validate_esp_mount_record(
        &device.to_string_lossy(), record_target, &canonical_source.to_string_lossy(),
        fstype, options, true,
    )
}
'''

ESP_VERIFY_METHOD = r'''fn verify_esp_bootloaders(device: &str) -> Result<String, String> {
    let selected = canonical_block_device(device)?;
    let mountpoint = tempfile::Builder::new()
        .prefix("kyth-esp-verify-")
        .tempdir()
        .map_err(|error| format!("could not create ESP check mountpoint: {error}"))?;
    let target = mountpoint.path();

    let existing = mounted_esp_target(&selected)?;
    if let Some(journal) = existing {
        let bind = Command::new("/usr/bin/mount")
            .args(["--bind", &journal, &target.to_string_lossy()])
            .output()
            .map_err(|error| format!("could not create private ESP verification mount: {error}"))?;
        if !bind.status.success() {
            return Err(format!(
                "could not create private ESP verification mount: {}",
                String::from_utf8_lossy(&bind.stderr).trim()
            ));
        }
        let remount = match Command::new("/usr/bin/mount")
            .args(["-o", "remount,bind,ro", &target.to_string_lossy()])
            .output()
        {
            Ok(output) => output,
            Err(error) => {
                let cleanup = Command::new("/usr/bin/umount").arg(target).output();
                if cleanup.as_ref().map(|output| !output.status.success()).unwrap_or(true) {
                    return Err("ESP verification mount remount failed and private mount could not be released".into());
                }
                return Err(format!("could not remount ESP verification mount read-only: {error}"));
            }
        };
        if !remount.status.success() {
            let cleanup = Command::new("/usr/bin/umount").arg(target).output();
            if cleanup.as_ref().map(|output| !output.status.success()).unwrap_or(true) {
                return Err("ESP verification mount remount failed and private mount could not be released".into());
            }
            return Err(format!(
                "could not remount ESP verification mount read-only: {}",
                String::from_utf8_lossy(&remount.stderr).trim()
            ));
        }
    } else {
        let mounted = Command::new("/usr/bin/mount")
            .args(["-o", "ro", device, &target.to_string_lossy()])
            .output()
            .map_err(|error| format!("could not mount ESP read-only: {error}"))?;
        if !mounted.status.success() {
            return Err(format!(
                "could not mount ESP read-only: {}",
                String::from_utf8_lossy(&mounted.stderr).trim()
            ));
        }
    }

    let check = (|| {
        verify_esp_mount(target, &selected)?;
        let boot_dir = target.join("EFI").join("BOOT");
        let loader = ["bootx64.efi", "BOOTX64.EFI", "Bootx64.efi"]
            .iter()
            .map(|name| boot_dir.join(name))
            .find(|path| {
                fs::symlink_metadata(path)
                    .map(|metadata| metadata.is_file())
                    .unwrap_or(false)
            })
            .ok_or_else(|| "ESP has no EFI/BOOT/bootx64.efi".to_string())?;
        if !efi_file_present(&target.join("EFI")) {
            return Err("ESP has no .efi bootloader payload under EFI/".to_string());
        }
        Ok(format!(
            "{} landed on the ESP",
            loader.strip_prefix(target).unwrap_or(&loader).display()
        ))
    })();
    let unmounted = Command::new("/usr/bin/umount")
        .arg(target)
        .output()
        .map_err(|error| format!("could not release ESP check mount: {error}"))?;
    finish_esp_check(check, unmounted.status.success())
}
'''

def adapt_configuration_tests(text):
    """Keep upstream coverage, updating only intentional Ubuntu contracts."""
    prefix, tests = text.split('#[cfg(test)]\nmod tests {', 1)
    tests = tests.replace('create_dir(&etc)', 'create_dir_all(etc.join("default"))')
    tests = tests.replace('create_dir_all(deploy.join("etc"))', 'create_dir_all(deploy.join("etc/default"))')
    tests = tests.replace('create_dir_all(deploy.join(name).join("etc"))',
                          'create_dir_all(deploy.join(name).join("etc/default"))')
    tests = tests.replace('join("locale.conf")', 'join("default/locale")')
    tests = tests.replace('join("vconsole.conf")', 'join("default/keyboard")')
    tests = tests.replace('KEYMAP=us', 'XKBLAYOUT=us')
    # These two upstream tests require a Fedora-only service deliberately not
    # shipped in Ubuntu. Retain both cases, asserting the actual Ubuntu contract.
    for name in ('legacy_boot_dependencies_require_a_full_unit_override_not_a_dropin_reset',
                 'installed_configuration_breaks_the_udev_local_filesystem_wait_cycle'):
        start = tests.index('    fn ' + name + '() {')
        end = tests.index('\n    #[test]', start)
        body = '''    fn NAME() {
        let directory = tempfile::tempdir().unwrap();
        let etc = directory.path().join("etc");
        fs::create_dir_all(etc.join("default")).unwrap();
        let plan = build_plan(ConfigurationInput {
            target_root: directory.path().to_string_lossy().into_owned(),
            hostname: "rowyn".into(), timezone: "UTC".into(),
            locale: "en_US.UTF-8".into(), keymap: "us".into(),
        }).unwrap();
        apply_plan(plan).unwrap();
        assert!(!etc.join("systemd/system/kyth-system-accounts.service").exists());
        assert!(!etc.join("plasma-setup-done").exists());
        assert_eq!(fs::read_to_string(etc.join("hostname")).unwrap(), "rowyn\\n");
    }
'''.replace('NAME', 'ubuntu_' + name)
        tests = tests[:start] + body + tests[end:]
    return prefix + '#[cfg(test)]\nmod tests {' + tests


def adapt(root):
    crate = root / 'src/kyth-installer-web/src-tauri/src'
    bootc = crate / 'installer_bootc.rs'
    text = bootc.read_text()
    text = replace(text, '    if subcommand == "to-filesystem" {',
                   '    if subcommand == "to-disk" {\n        argv.push("--composefs-backend".to_string());\n    }\n    if subcommand == "to-filesystem" {')
    head, closing = text.rsplit('\n}', 1)
    text = head + r'''
    #[test]
    fn rowyn_whole_disk_selects_composefs_systemd_without_disabling_preflight() {
        let plan = build_plan(BootcInstallInput {
            skip_fetch_check: false, wipe: true, ..input("to-disk")
        }).unwrap();
        assert!(plan.argv.iter().any(|a| a == "--composefs-backend"));
        assert!(plan.argv.iter().any(|a| a == "--bootloader=systemd"));
        assert!(!plan.argv.iter().any(|a| a == "--skip-fetch-check"));
        let filesystem = build_plan(BootcInstallInput {
            target: "/mnt/target".into(), ..input("to-filesystem")
        }).unwrap();
        assert!(!filesystem.argv.iter().any(|a| a == "--composefs-backend"));
    }
''' + '\n}' + closing
    bootc.write_text(text)

    config = crate / 'installer_configuration.rs'
    text = config.read_text()
    # Retain upstream helper and all unrelated regression tests. Only the fixed
    # physical root uses the caller-owned view; legacy fixtures stay meaningful.
    text = replace(text, '    let root = safe_root(physical_root)?;\n    let deployments',
                   '    let root = safe_root(physical_root)?;\n'
                   '    if root == "/run/kyth-installer/install-root" {\n'
                   '        let view = "/run/kyth-installer/deployment-root";\n'
                   '        verify_mounted_view(&root, view)?;\n'
                   '        return Ok(view.to_string());\n    }\n    let deployments')
    text += MOUNT_POLICY
    config.write_text(adapt_configuration_tests(text))

    executor = crate / 'installer_job_executor.rs'
    text = executor.read_text()
    text = replace(text, 'const WIPE_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/install-root";',
                   'const WIPE_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/install-root";\n'
                   'const DEPLOYMENT_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/deployment-root";')
    text = replace(text, '                self.mount_wipe_root(phase, cancellation)?;',
                   '                self.mount_wipe_root(phase, cancellation)?;\n'
                   '                self.mount_wipe_deployment(phase, cancellation)?;')
    text = replace(text, '    fn execute_configuration(', MOUNT_METHOD + '    fn execute_configuration(')
    text = replace(text, '                account.deploy_root = deploy_root.clone();',
                   '                account.deploy_root = deploy_root.clone();\n'
                   '                account.target_root = deploy_root.clone();')
    # Composefs/systemd-boot uses GPT discovery and bootc-written boot kargs.
    # Do not synthesize a /boot/efi mountpoint absent from the immutable image.
    start = text.index('                "wipe" => {\n                    // bootc can boot from kernel arguments')
    end = text.index('                mode if lays_out_home_subvolume(mode)', start)
    text = text[:start] + '                "wipe" => { /* ESP verified from the selected disk during assurance. */ }\n' + text[end:]
    text = text.replace('write.path.ends_with("/locale.conf")', 'write.path.ends_with("/default/locale")')
    text = text.replace('write.path.ends_with("/vconsole.conf")', 'write.path.ends_with("/default/keyboard")')
    text = text.replace('content.strip_prefix("KEYMAP=")', 'content.strip_prefix("XKBLAYOUT=")')
    executor.write_text(text)

    accounts = crate / 'installer_accounts.rs'
    text = accounts.read_text()
    text = replace(text, '.join("ostree/deploy/default/var/home")', '.join("var/home")')
    start = text.index('    fn successful_account_retry_completes_plasma_setup_and_clears_setup_autologin()')
    end = text.index('\n    #[test]', start)
    test = text[start:end].replace('target_root: temp.path().to_string_lossy().into_owned()',
                                   'target_root: deploy.to_string_lossy().into_owned()')
    test = test.replace('        apply(request.clone()).unwrap();',
                        '        apply(request.clone()).unwrap();\n'
                        '        assert!(deploy.join("var/home/kyth_user").is_dir());\n'
                        '        assert!(!deploy.join("ostree/deploy/default").exists());')
    text = text[:start] + test + text[end:]
    accounts.write_text(text)

    assurance = crate / 'installer_assurance.rs'
    text = assurance.read_text()
    verify_start = text.index('fn verify_esp_bootloaders(')
    verify_end = text.index('\nfn kernel_detail(', verify_start)
    text = text[:verify_start] + ESP_VERIFY_METHOD + text[verify_end:]
    text = replace(text, '    if !deploy.starts_with(root.join("ostree/deploy")) {',
                   '    if root == Path::new("/run/kyth-installer/install-root") {\n'
                   '        crate::installer_configuration::verify_mounted_view(\n'
                   '            &input.target_root, &input.deploy_root)?;\n'
                   '    } else if !deploy.starts_with(root.join("ostree/deploy")) {')
    text = replace(text, '    if contains_real_entry(&root.join("ostree/deploy")) {',
                   '    if contains_real_entry(&root.join("state/deploy")) {\n'
                   '        return Some("composefs deployment metadata is present");\n    }\n'
                   '    if contains_real_entry(&root.join("ostree/deploy")) {')
    start = text.index('    // M15b: when the fstab configures an ESP,')
    end = text.index('\n\n    Ok(vec![', start)
    original = text[start:end]
    # Keep legacy regression fixtures and checks intact outside the live fixed
    # sysroot. The production path always verifies the discovered target ESP.
    legacy = original.replace('    let bootloader_detail = match', '        match').rstrip(';')
    text = text[:start] + '''    let bootloader_detail = if root == Path::new("/run/kyth-installer/install-root") {
        let discovered = selected_disk_esp(&input.target_disk)?;
        let fstab_esp = esp_device_from_entries(&fstab_entries);
        validate_esp_match(fstab_esp.as_deref(), &discovered)?;
        format!("{reason}; {}", verify_esp_bootloaders(&discovered)?)
    } else {
''' + legacy + '\n    };' + text[end:]
    prefix, tests = text.split('#[cfg(test)]\nmod tests {', 1)
    tests = tests.replace('std::fs::create_dir_all(&etc).unwrap();',
                          'std::fs::create_dir_all(etc.join("default")).unwrap();')
    tests = tests.replace('std::fs::write(modules.join("vmlinuz"), "fake kernel").unwrap();',
                          'std::fs::write(modules.join("vmlinuz"), "fake kernel").unwrap();\n'
                          '        std::fs::write(modules.join("initramfs.img"), "fake initramfs").unwrap();')
    tests = tests.replace('KEYMAP=us', 'XKBLAYOUT=us')
    text = prefix + '#[cfg(test)]\nmod tests {' + tests
    text += ESP_POLICY
    assurance.write_text(text)
