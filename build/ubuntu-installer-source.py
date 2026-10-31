#!/usr/bin/env python3
"""Export the minimal Apache-2.0 Kyth installer source, then port it to Ubuntu.

Never executes upstream installer/Containerfile, installer/build.sh or ISO wrappers.
The input checkout is read-only; git archive exports exactly the pinned objects.
"""
import argparse
import io
import importlib.util
from pathlib import Path
import re
import subprocess
import tarfile

PIN = "2baf8496b5aa904c0784f4290df743ee17edae48"
PATHS = ("src/kyth-installer-web", "src/kyth-shared-rs",
         "third_party/gtk-glib-0.18.5", "build_files/exe-handler-apps.json",
         "build_files/kyth-launch-installer", "build_files/kyth-installerd.service", "LICENSE")
IDENTITY = "https://github.com/kyth-os/Rowyn/.github/workflows/build.yml@refs/heads/main"

# --- Legacy junk cut (approved by Patrick, Oct 30, 2026) -------------------
# Identical shared-crate cut to the Hub port's (the two scripts each carry
# it, as they already duplicate the export/replace helpers): the crate's
# 92 helper-binary targets are exported although neither image build
# compiles any of them, and only the modules in SHARED_CRATE_KEEP_MODULES
# — the two shells' reviewed reachability closure — survive; the rest
# leave with their declarations, along with the pgp dependency only the
# pruned updater-manifest verifier used. The installer additionally drops
# its never-built Slint native UI (the ISO builds only the Tauri shell,
# the daemon, and the executor), and stops rewriting
# system::installer_source, a module no shipped frontend references.
SHARED_CRATE_BIN_FILES = 92
SHARED_CRATE_BIN_SECTIONS = 91
SHARED_CRATE_KEEP_MODULES = (
    "atomic_io",
    "commands",
    "diagnostic_report",
    "diagnostics_scrub",
    "system",
    "system::account",
    "system::app_suggestions",
    "system::boot_health",
    "system::boot_runtime",
    "system::bootc",
    "system::bootc_policy",
    "system::bootc_query",
    "system::btrfs_status",
    "system::cloud_oauth",
    "system::controllers",
    "system::deployment_history",
    "system::desktop_stack",
    "system::drivers",
    "system::drives",
    "system::exe_trust",
    "system::firmware",
    "system::firstboot",
    "system::fonts_ready",
    "system::gaming_compat",
    "system::gaming_library",
    "system::gaming_slice",
    "system::gpu",
    "system::hardware_policy",
    "system::hardware_view",
    "system::installer_validation",
    "system::jobs",
    "system::memory_pressure",
    "system::mesa_version",
    "system::mok_verify",
    "system::network_identity",
    "system::network_preset",
    "system::pipewire",
    "system::plasma_hdr",
    "system::printing",
    "system::probe",
    "system::process",
    "system::recovery_status",
    "system::registry",
    "system::runtime_output",
    "system::security_container",
    "system::smb",
    "system::snapshot",
    "system::software_catalog",
    "system::storage",
    "system::telemetry",
    "system::update_availability",
    "system::update_status",
    "system::updates_unified",
    "system::vpn_saml",
    "system::windows_installer",
    "system::windows_verify",
    "transfer",
    "url_encode",
)


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one source anchor: {old[:100]!r}")
    return text.replace(old, new)


def strip_shared_crate_bins(root):
    """Delete the shared crate's helper-binary sources and their [[bin]]
    manifest sections. Neither image build compiles them (the payload
    builds only the Hub shell package; the ISO names its three installer
    bins explicitly), and a [[bin]] section naming a deleted file — or a
    file left behind with no section — would break the crate's manifest,
    so the two leave together, counted."""
    crate = root / "src/kyth-shared-rs"
    bins = sorted((crate / "src").glob("*_bin.rs"))
    if len(bins) != SHARED_CRATE_BIN_FILES:
        raise ValueError(
            f"expected {SHARED_CRATE_BIN_FILES} shared-crate bin sources, found {len(bins)}")
    manifest_path = crate / "Cargo.toml"
    lines = manifest_path.read_text().splitlines(keepends=True)
    kept, sections, index = [], 0, 0
    while index < len(lines):
        if lines[index].rstrip() == "[[bin]]":
            sections += 1
            index += 1
            while index < len(lines) and not lines[index].startswith("["):
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    if sections != SHARED_CRATE_BIN_SECTIONS:
        raise ValueError(
            f"expected {SHARED_CRATE_BIN_SECTIONS} [[bin]] sections, found {sections}")
    manifest_path.write_text("".join(kept))
    for path in bins:
        path.unlink()
    return f"{len(bins)} bin sources + {sections} [[bin]] sections"


def prune_shared_crate(root):
    """Drop the shared crate's unreviewed library modules and the pgp
    dependency. A module survives only if it is in
    SHARED_CRATE_KEEP_MODULES (the shells' reachability closure); its
    file and its `pub mod` declaration leave together, and the manifest
    loses the rPGP dependency only the pruned updater-manifest verifier
    used. Fails closed when a kept module is missing upstream or the
    declared set afterwards is not exactly the keep set."""
    crate = root / "src/kyth-shared-rs"
    manifest_path = crate / "Cargo.toml"
    text = manifest_path.read_text()
    text = replace_once(
        text,
        "# OpenPGP verification for the KythOS-signed updater manifest (see\n"
        "# system/update_manifest.rs). rPGP: pure Rust, so the same code builds\n"
        "# on the dev VM, in CI, and in the Fedora image with no system crypto\n"
        "# libraries. Verify-only use — nothing here can sign.\n"
        "pgp = \"0.19\"\n",
        "")
    manifest_path.write_text(text)

    keep = set(SHARED_CRATE_KEEP_MODULES)
    dropped = []
    for modfile, prefix in ((crate / "src/lib.rs", ""), (crate / "src/system/mod.rs", "system::")):
        lines = modfile.read_text().splitlines(keepends=True)
        declared = {}
        for index, line in enumerate(lines):
            match = re.fullmatch(r"pub mod ([a-z0-9_]+);", line.rstrip("\n"))
            if match:
                declared[prefix + match.group(1)] = index
        expected_here = {name for name in keep
                         if name.startswith(prefix) and "::" not in name[len(prefix):]}
        missing = expected_here - set(declared)
        if missing:
            raise ValueError(f"kept modules missing from {modfile.name}: {sorted(missing)}")
        remove = set()
        for dotted, index in declared.items():
            if dotted in keep:
                continue
            remove.add(index)
            if index > 0 and lines[index - 1].startswith("#[cfg("):
                remove.add(index - 1)
            dropped.append(dotted)
            name = dotted.rsplit("::", 1)[-1]
            path = (crate / "src" / f"{name}.rs") if not prefix else (crate / "src/system" / f"{name}.rs")
            if not path.is_file():
                raise ValueError(f"module file missing for {dotted}: {path}")
            path.unlink()
        modfile.write_text("".join(
            line for index, line in enumerate(lines) if index not in remove))
    remaining = sorted(
        str(path.relative_to(crate / "src"))
        for path in (crate / "src").rglob("*.rs"))
    expected = sorted(
        ["lib.rs", "system/mod.rs"]
        + [f"{name}.rs" for name in keep if "::" not in name and name != "system"]
        + [f"system/{name.rsplit('::', 1)[-1]}.rs" for name in keep if name.startswith("system::")])
    if remaining != expected:
        raise ValueError(
            f"shared-crate tree after prune is not the keep set: {remaining} != {expected}")
    return f"{len(dropped)} modules + pgp dependency"


def sync_cargo_lock(crate, absent_packages):
    """Re-sync a shell crate's Cargo.lock with cargo itself after a
    manifest dependency removal, then assert the removed packages are
    gone. A --locked build rejects any lock cargo would rewrite, and the
    exact rewrite (orphan pruning plus multi-version reference
    normalization) is feature-resolution semantics only cargo owns.
    `cargo fetch` performs the conservative sync: locked versions are
    preserved, only the removed subtrees leave. Fails closed when cargo
    is unavailable or errors."""
    import shutil
    if shutil.which("cargo") is None:
        raise ValueError("cargo is required to re-sync Cargo.lock after a dependency removal")
    result = subprocess.run(
        ["cargo", "fetch"], cwd=crate, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(
            f"cargo fetch failed to re-sync {crate}/Cargo.lock: {result.stderr[-400:]}")
    lock = (crate / "Cargo.lock").read_text()
    present = set()
    for block in lock.split("[[package]]")[1:]:
        for line in block.splitlines():
            if line.startswith('name = "'):
                present.add(line.split('"')[1])
                break
    survivors = sorted(set(absent_packages) & present)
    if survivors:
        raise ValueError(f"packages still in Cargo.lock after sync: {survivors}")
    return f"{len(present)} packages remain"


def adapt_installer_service(text):
    """Apply the narrow writable-runtime contract to the pinned unit.

    Keep every replacement anchored to the exact pinned upstream text.  A
    service layout change must stop the port rather than silently broadening
    or dropping the sandbox.
    """
    text = replace_once(
        text,
        "EnvironmentFile=-/etc/kyth-installer.env\n",
        "EnvironmentFile=/etc/kyth-installer.env\n",
    )
    text = replace_once(
        text,
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n",
    )
    text = replace_once(
        text,
        "UMask=0077\n",
        "UMask=0077\n"
        "# Provision bootc paths without re-owning the launcher's session-token tree.\n"
        "RuntimeDirectory=bootc\n"
        "RuntimeDirectoryMode=0750\n"
        "StateDirectory=containers\n"
        "StateDirectoryMode=0700\n",
    )
    text = replace_once(
        text,
        "ReadWritePaths=/run/kyth-installer /var/tmp /var/log\n",
        "ReadWritePaths=/run/kyth-installer /var/tmp /var/log "
        "/var/lib/containers /run/bootc\n",
    )
    return text.replace("KythOS", "Rowyn")


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
    spec = importlib.util.spec_from_file_location(
        "rowyn_composefs", Path(__file__).with_name("ubuntu-composefs-source.py"))
    assert spec is not None and spec.loader is not None
    composefs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(composefs)
    if composefs.completed(root, digest):
        return
    crate = root / "src/kyth-installer-web/src-tauri"
    executor = crate / "src/installer_job_executor.rs"
    text = executor.read_text()
    source = "ghcr.io/kyth-os/rowyn@" + digest
    target = "ghcr.io/kyth-os/rowyn:stable"
    anchor = 'const FILESYSTEM_STAGING_MOUNTPOINT: &str = "/run/kyth-installer/alongside-target";\n'
    text = replace_once(text, anchor, anchor + f'const ROWYN_RELEASE_SOURCE: &str = "{source}";\n'
                        f'const ROWYN_UPDATE_TARGET: &str = "{target}";\n')
    # All transports and source are bound to this ISO's exact release; the
    # mutable stable channel is only the fixed bootc update target.
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
                    || self.target_imgref != ROWYN_UPDATE_TARGET
                {
                    return Err(NativePhaseError::Execution { phase,
                        message: "source must be this signed Rowyn release and target must be stable".into() });
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
                classify_secure_boot_probe(phase, cancellation)?;
''')
    # Rowyn's firmware policy is deliberately explicit: the only accepted
    # states are ordinary disabled firmware and a documented unsupported state
    # corroborated by both systemd and mokutil. All other evidence fails closed.
    secure_boot_policy = r'''// ROWYN_SECURE_BOOT_POLICY_BEGIN
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SecureBootProbeState {
    Disabled,
    Unsupported,
}

#[derive(Debug, Clone)]
struct SecureBootCommandEvidence {
    status: Option<i32>,
    stdout: String,
    stderr: String,
    error: Option<String>,
}

fn secure_boot_command_diagnostic(name: &str, evidence: &SecureBootCommandEvidence) -> String {
    if let Some(error) = &evidence.error {
        return format!("{name} execution failed: {error}");
    }
    format!(
        "{name} returned status={:?}, stderr={:?}, stdout={:?}",
        evidence.status,
        evidence.stderr.trim_end(),
        evidence.stdout.trim_end(),
    )
}

fn parse_bootctl_secure_boot(evidence: &SecureBootCommandEvidence) -> Result<&'static str, String> {
    if evidence.error.is_some() || evidence.status != Some(0) {
        return Err(secure_boot_command_diagnostic("bootctl", evidence));
    }
    let matches: Vec<&str> = evidence
        .stdout
        .lines()
        .filter_map(|line| {
            let line = line.trim_start();
            line.strip_prefix("Secure Boot:")
                .or_else(|| line.strip_prefix("secure boot:"))
                .map(str::trim)
        })
        .collect();
    if matches.len() != 1 {
        return Err(format!(
            "bootctl Secure Boot status is missing or ambiguous: {}",
            secure_boot_command_diagnostic("bootctl", evidence)
        ));
    }
    match matches[0] {
        "enabled" => Ok("enabled"),
        "disabled" => Ok("disabled"),
        "disabled (unsupported)" => Ok("unsupported"),
        _ => Err(format!(
            "bootctl Secure Boot status is unknown: {}",
            secure_boot_command_diagnostic("bootctl", evidence)
        )),
    }
}

fn parse_mokutil_secure_boot(evidence: &SecureBootCommandEvidence) -> Result<&'static str, String> {
    if evidence.error.is_some() {
        return Err(secure_boot_command_diagnostic("mokutil", evidence));
    }
    let stdout = evidence.stdout.trim();
    let stderr = evidence.stderr.trim();
    match (evidence.status, stdout, stderr) {
        (Some(0), "SecureBoot enabled", "") => Ok("enabled"),
        (Some(0), "SecureBoot disabled", "") => Ok("disabled"),
        // mokutil has used both 1 and 255 for this documented unsupported
        // condition across releases. The exact diagnostic is required; an
        // arbitrary non-zero exit is never treated as unsupported.
        (Some(1 | 255), "", "EFI variables are not supported on the system.")
        | (Some(1 | 255), "", "This system doesn't support Secure Boot")
        | (Some(1 | 255), "This system doesn't support Secure Boot", "") => Ok("unsupported"),
        _ => Err(secure_boot_command_diagnostic("mokutil", evidence)),
    }
}

fn classify_secure_boot_evidence(
    efi_vars_readable: bool,
    efi_secure_boot: Option<bool>,
    bootctl: &SecureBootCommandEvidence,
    mokutil: &SecureBootCommandEvidence,
) -> Result<SecureBootProbeState, String> {
    if !efi_vars_readable {
        return Err("EFI variable filesystem is not readable".into());
    }
    let bootctl_state = parse_bootctl_secure_boot(bootctl)?;
    let mokutil_state = parse_mokutil_secure_boot(mokutil)?;
    if bootctl_state == "enabled" || mokutil_state == "enabled" || efi_secure_boot == Some(true) {
        return Err(format!(
            "Secure Boot is enabled or contradictory: bootctl={bootctl_state}, mokutil={mokutil_state}, EFI SecureBoot={efi_secure_boot:?}"
        ));
    }
    if bootctl_state != mokutil_state {
        return Err(format!(
            "Secure Boot evidence is contradictory: bootctl={bootctl_state}, mokutil={mokutil_state}, EFI SecureBoot={efi_secure_boot:?}"
        ));
    }
    match (bootctl_state, efi_secure_boot) {
        ("disabled", None | Some(false)) => Ok(SecureBootProbeState::Disabled),
        // Missing SecureBoot is not enough by itself. Here it is accepted only
        // because the readable EFI variable store plus exact systemd and
        // mokutil unsupported reports corroborate one another.
        ("unsupported", None) => Ok(SecureBootProbeState::Unsupported),
        ("unsupported", Some(false)) => Err(
            "Secure Boot evidence is contradictory: firmware exposes SecureBoot=0 but systemd reports unsupported".into(),
        ),
        _ => Err(format!(
            "Secure Boot state is unavailable or ambiguous: bootctl={bootctl_state}, mokutil={mokutil_state}, EFI SecureBoot={efi_secure_boot:?}"
        )),
    }
}
// ROWYN_SECURE_BOOT_POLICY_END

fn read_secure_boot_efi_variable() -> Result<Option<bool>, String> {
    let directory = Path::new("/sys/firmware/efi/efivars");
    let mut entries = std::fs::read_dir(directory)
        .map_err(|error| format!("cannot read EFI variable filesystem {directory:?}: {error}"))?;
    let mut has_variable = false;
    let mut secure_boot_path = None;
    while let Some(entry) = entries.next() {
        let entry = entry.map_err(|error| format!("cannot enumerate EFI variables: {error}"))?;
        has_variable = true;
        if entry.file_name() == "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c" {
            secure_boot_path = Some(entry.path());
        }
    }
    if !has_variable {
        return Err("EFI variable filesystem is empty or unavailable".into());
    }
    let Some(path) = secure_boot_path else {
        return Ok(None);
    };
    let bytes = std::fs::read(&path)
        .map_err(|error| format!("cannot read EFI SecureBoot variable {path:?}: {error}"))?;
    if bytes.len() != 5 {
        return Err(format!(
            "EFI SecureBoot variable has invalid length {}; expected 5 bytes",
            bytes.len()
        ));
    }
    match bytes[4] {
        0 => Ok(Some(false)),
        1 => Ok(Some(true)),
        value => Err(format!("EFI SecureBoot variable has invalid value {value}")),
    }
}

fn run_secure_boot_command(
    program: &str,
    args: &[&str],
    cancellation: &CancellationToken,
    operation: &str,
) -> SecureBootCommandEvidence {
    let mut command = Command::new(program);
    command.args(args).env("LC_ALL", "C");
    match super::installer_stream::run_output_timeout_capture(
        &mut command,
        || cancellation.is_cancelled(),
        std::time::Duration::from_secs(15),
        operation,
    ) {
        Ok(output) => {
            let stdout = match String::from_utf8(output.stdout) {
                Ok(value) => value,
                Err(_) => return SecureBootCommandEvidence {
                    status: output.status.code(),
                    stdout: String::new(),
                    stderr: String::new(),
                    error: Some(format!("{operation} stdout was not valid UTF-8")),
                },
            };
            let stderr = match String::from_utf8(output.stderr) {
                Ok(value) => value,
                Err(_) => return SecureBootCommandEvidence {
                    status: output.status.code(),
                    stdout,
                    stderr: String::new(),
                    error: Some(format!("{operation} stderr was not valid UTF-8")),
                },
            };
            SecureBootCommandEvidence {
                status: output.status.code(),
                stdout,
                stderr,
                error: None,
            }
        },
        Err(error) => SecureBootCommandEvidence {
            status: None,
            stdout: String::new(),
            stderr: String::new(),
            error: Some(error),
        },
    }
}

fn classify_secure_boot_probe(
    phase: Phase,
    cancellation: &CancellationToken,
) -> Result<(), NativePhaseError> {
    let efi_secure_boot = read_secure_boot_efi_variable().map_err(|message| {
        NativePhaseError::Execution {
            phase,
            message: format!("Secure Boot EFI evidence failed: {message}"),
        }
    })?;
    let bootctl = run_secure_boot_command(
        "/usr/bin/bootctl",
        &["--no-pager", "status"],
        cancellation,
        "bootctl Secure Boot probe",
    );
    let mokutil = run_secure_boot_command(
        "/usr/bin/mokutil",
        &["--sb-state"],
        cancellation,
        "mokutil Secure Boot probe",
    );
    classify_secure_boot_evidence(true, efi_secure_boot, &bootctl, &mokutil).map_err(|message| {
        NativePhaseError::Execution {
            phase,
            message: format!("Secure Boot probe rejected: {message}"),
        }
    })?;
    Ok(())
}
'''
    text = replace_once(text, "impl NativeInstallRequest {", secure_boot_policy + "\nimpl NativeInstallRequest {")
    executor.write_text(text)
    # The pinned stream helper already bounds command lifetime and cancellation
    # but its original output helper intentionally rejects non-zero status.
    # Secure Boot needs the exact status/stderr pair for a positive unsupported
    # classification, so add a narrow capture variant rather than refactoring
    # the shared streaming path.
    stream = crate / "src/installer_stream.rs"
    text = stream.read_text()
    anchor = "/// Own a long-running executor child until it exits, is cancelled, or times out.\n"
    text = replace_once(text, anchor, '''fn merge_stderr_into_stdout(command: &mut Command) {
    // The streaming model reads stdout only. Preserve helper diagnostics in
    // the failure tail without opening a second unbounded reader for stderr.
    unsafe {
        command.pre_exec(|| {
            if libc::dup2(libc::STDOUT_FILENO, libc::STDERR_FILENO) == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

''' + anchor)
    anchor = '''pub(crate) fn run_command(
    command: &mut Command,
    cancel_requested: impl Fn() -> bool,
) -> Result<ExitStatus, String> {
    let mut child = command
'''
    text = replace_once(text, anchor, anchor.replace("    let mut child = command\n", "    merge_stderr_into_stdout(command);\n    let mut child = command\n"))
    anchor = '''pub(crate) fn run_command_timeout(
    command: &mut Command,
    cancel_requested: impl Fn() -> bool,
    timeout: Duration,
) -> Result<ExitStatus, String> {
    let mut child = command
'''
    text = replace_once(text, anchor, anchor.replace("    let mut child = command\n", "    merge_stderr_into_stdout(command);\n    let mut child = command\n"))
    anchor = "pub(crate) fn run_output_timeout(\n"
    text = replace_once(text, anchor, '''pub(crate) struct CapturedCommandOutput {
    pub(crate) status: ExitStatus,
    pub(crate) stdout: Vec<u8>,
    pub(crate) stderr: Vec<u8>,
}

/// Bounded capture for fixed read-only probes that need status and stderr.
/// Unlike `run_output_timeout`, non-zero exits are returned to the caller so
/// it can distinguish a documented unsupported state from an arbitrary
/// command failure. Output is capped after collection; these fixed probes
/// must remain small and fail closed if they do not.
pub(crate) fn run_output_timeout_capture(
    command: &mut Command,
    cancel_requested: impl Fn() -> bool,
    timeout: Duration,
    operation: &str,
) -> Result<CapturedCommandOutput, String> {
    let mut child = command
        .process_group(0)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("could not spawn {operation}: {error}"))?;
    let started = Instant::now();
    loop {
        if cancel_requested() {
            kill_process_group(&mut child);
            let _ = child.wait();
            return Err(format!("{operation} cancelled"));
        }
        match child.try_wait() {
            Ok(Some(_)) => {
                let output = child
                    .wait_with_output()
                    .map_err(|error| format!("could not collect {operation} output: {error}"))?;
                const MAX_PROBE_OUTPUT: usize = 64 * 1024;
                if output.stdout.len() > MAX_PROBE_OUTPUT || output.stderr.len() > MAX_PROBE_OUTPUT {
                    return Err(format!("{operation} output exceeded {MAX_PROBE_OUTPUT} bytes"));
                }
                return Ok(CapturedCommandOutput {
                    status: output.status,
                    stdout: output.stdout,
                    stderr: output.stderr,
                });
            }
            Ok(None) if started.elapsed() < timeout => {
                std::thread::sleep(Duration::from_millis(50))
            }
            Ok(None) => {
                kill_process_group(&mut child);
                let _ = child.wait();
                return Err(format!(
                    "{operation} timed out after {} seconds",
                    timeout.as_secs()
                ));
            }
            Err(error) => {
                kill_process_group(&mut child);
                let _ = child.wait();
                return Err(format!("could not wait for {operation}: {error}"));
            }
        }
    }
}

''' + anchor)
    stream.write_text(text)
    storage = crate / "src/installer_storage.rs"
    text = storage.read_text()
    text = replace_once(text, 'const PROTECTED_MOUNT_TARGETS: [&str; 6]', 'const PROTECTED_MOUNT_TARGETS: [&str; 8]')
    text = replace_once(text, '    "/run/initramfs/iso",', '    "/run/initramfs/iso",\n    "/cdrom",\n    "/isodevice",')
    storage.write_text(text)
    # Legacy junk cut: system::installer_source is no longer rewritten.
    # Nothing in the Tauri installer references the module (the executor
    # carries its own ROWYN_RELEASE_SOURCE / ROWYN_UPDATE_TARGET constants
    # and rejects any other source itself), so the prune below removes
    # the module from the export entirely instead of the port spending
    # anchors maintaining a rewrite only the never-built Slint UI used.
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
    # Ubuntu's util-linux puts mount/umount and lsblk in /usr/bin, while
    # blkid and blockdev live in /usr/sbin.
    for path in (crate / "src").glob("*.rs"):
        text = path.read_text()
        for binary in ("mount", "umount", "lsblk"):
            text = text.replace(f'/usr/sbin/{binary}', f'/usr/bin/{binary}')
        for binary in ("blkid", "blockdev"):
            text = text.replace(f'/usr/bin/{binary}', f'/usr/sbin/{binary}')
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
    # Rowyn ships the upstream Tauri shell + web frontend, as Kyth does.
    # The crate's native Slint UI is not built or installed (see Containerfile).
    web = root / "src/kyth-installer-web"
    app = web / "src/InstallerApp.tsx"
    text = app.read_text()
    # Ubuntu has one kernel: drop the kernel-choice step entirely.
    text = replace_once(text, 'const steps = ["network", "welcome", "disk", "kernel", "software", "config", "review", "install"] as const;',
                        'const steps = ["network", "welcome", "disk", "software", "config", "review", "install"] as const;')
    text = replace_once(text, '{step === "kernel" && <ChoiceStep title="Choose kernel" value={request.kernel} options={["fedora", "cachyos"]} hint="CachyOS kernel: tuned for gaming and latency. Fedora: the stock, most conservative kernel." onChange={(v) => update("kernel", v)} onBack={() => setStep("disk")} onNext={() => setStep("software")} />}', '')
    # ChoiceStep existed only for the kernel step; tsc rejects the orphan.
    app_lines = text.splitlines(keepends=True)
    kept = [line for line in app_lines if not line.startswith('function ChoiceStep(')]
    if len(kept) != len(app_lines) - 1:
        raise ValueError("expected exactly one ChoiceStep definition to remove")
    text = ''.join(kept)
    text = replace_once(text, 'onBack={() => setStep("welcome")} onNext={() => setStep("kernel")}',
                        'onBack={() => setStep("welcome")} onNext={() => setStep("software")}')
    text = replace_once(text, 'onBack={() => setStep("kernel")} onNext={() => setStep("config")}',
                        'onBack={() => setStep("disk")} onNext={() => setStep("config")}')
    text = replace_once(text, 'kernel: "fedora"', 'kernel: "ubuntu"')
    text = replace_once(text, 'hostname: "kyth"', 'hostname: "rowyn"')
    # Only whole-disk wipe is qualified in this port (the executor enforces it too).
    text = replace_once(text, '<option value="wipe">Erase full disk</option><option value="resize_ntfs">Keep Windows and install alongside</option><option value="alongside">Replace a partition</option><option value="free_space">Use free space</option><option value="manual">Manual partitioning</option>',
                        '<option value="wipe">Erase full disk</option>')
    text = replace_once(text, '<option value="none">None</option><option value="tpm2">TPM2-bound</option>',
                        '<option value="none">None</option>')
    text = replace_once(text, 'Install the immutable gaming and development desktop. Coming from Windows? Keep it for anti-cheat titles and shrink it below — KythOS plays everything else.',
                        'Install the immutable gaming and development desktop. This installer erases the disk you select and installs Rowyn on it.')
    app.write_text('// Rowyn: Ubuntu-only kernel, wipe-only modes, Rowyn identity.\n' + text.replace('KythOS', 'Rowyn'))
    for page in [web / "index.html", *sorted((web / "src").rglob("*.ts*"))]:
        if page != app:
            page.write_text(page.read_text().replace('KythOS', 'Rowyn'))
    conf = crate / "tauri.conf.json"
    text = conf.read_text()
    text = replace_once(text, '"productName": "KythOS Installer"', '"productName": "Rowyn Installer"')
    text = replace_once(text, '"identifier": "com.kythos.installer"', '"identifier": "com.rowyn.installer"')
    text = replace_once(text, '"title": "KythOS Installer"', '"title": "Rowyn Installer"')
    conf.write_text(text)
    # The launcher stays on the upstream Tauri shell binary.
    launcher = root / "build_files/kyth-launch-installer"
    text = launcher.read_text()
    if '/usr/bin/kyth-installer-shell --socket-path' not in text:
        raise ValueError("upstream launcher no longer targets the Tauri shell")
    launcher.write_text(text.replace('KythOS', 'Rowyn'))
    service = root / "build_files/kyth-installerd.service"
    service.write_text(adapt_installer_service(service.read_text()))
    (root / 'ROWYN-PORT-NOTICE').write_text(
        f'Kyth installer source {PIN}, Apache-2.0 (LICENSE).\n'
        'Modified by Rowyn: Ubuntu accounts, tool paths, Tauri web UI, source trust, firmware policy.\n'
        'A legacy junk cut removed the never-built Slint native UI (bin target,\n'
        'native_main.rs, the slint/slint-build dependencies and its build.rs\n'
        'compile step), the shared crate\'s 92 helper-binary targets, and every\n'
        'shared-crate library module outside the shells\' reachability closure\n'
        '(incl. the runtime-updater/PGP heritage and its pgp dependency).\n'
        'Upstream OS/image/ISO assembly scripts are not included or invoked.\n')

    # --- Legacy junk cut -----------------------------------------------------
    # The Slint native UI is not built or installed (the ISO builds only
    # kyth-installer-shell / kyth-installerd / kyth-installer-exec), but
    # slint and slint-build are non-optional dependencies of this crate,
    # so the whole Slint stack compiled into every ISO build. The bin
    # target, its source, both dependencies, and the build.rs compile
    # step leave together.
    native = crate / "src/native_main.rs"
    if not native.is_file():
        raise ValueError("expected upstream native_main.rs to remove")
    native.unlink()
    installer_cargo = crate / "Cargo.toml"
    text = installer_cargo.read_text()
    text = replace_once(
        text,
        '[[bin]]\nname = "kyth-installer-native"\npath = "src/native_main.rs"\n\n',
        "")
    text = replace_once(text, 'slint-build = "1.16.0"\n', "")
    text = replace_once(text, 'slint = "1.16.0"\n', "")
    installer_cargo.write_text(text)
    build_rs = crate / "build.rs"
    text = build_rs.read_text()
    text = replace_once(
        text,
        '    slint_build::compile("ui/installer.slint").expect("failed to compile native installer UI");\n',
        "")
    build_rs.write_text(text)
    # The shared crate gets the same bin strip + closure prune as the Hub
    # port, then this crate's lock is re-synced for both dependency
    # removals (slint stack and pgp) in one cargo pass.
    strip_shared_crate_bins(root)
    prune_shared_crate(root)
    sync_cargo_lock(crate, ("pgp", "slint", "slint-build"))

    composefs.adapt(root, digest)
    composefs.record(root, digest)


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