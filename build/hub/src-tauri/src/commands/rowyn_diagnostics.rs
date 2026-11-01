//! Rowyn's read-only diagnostics. No caller-selected program, argv or path.
use rowyn_shared::{
    diagnostics_scrub::scrub_logs,
    system::process::{run_bounded_command, strip_ansi},
};
use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::Manager;

const TEXT_LIMIT: usize = 12_000;
const REPORT_LIMIT: usize = 128_000;
static SNAPSHOT_RUNNING: AtomicBool = AtomicBool::new(false);
static CONTROLLER_RUNNING: AtomicBool = AtomicBool::new(false);

struct ReadGuard<'a>(&'a AtomicBool);
impl<'a> ReadGuard<'a> {
    fn take(flag: &'a AtomicBool) -> Result<Self, String> {
        flag.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| Self(flag))
            .map_err(|_| "A check is already running.".into())
    }
}
impl Drop for ReadGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

#[derive(Clone, Serialize)]
pub(crate) struct Reading {
    pub name: String,
    pub state: String,
    pub detail: String,
}
#[derive(Serialize)]
pub(crate) struct Snapshot {
    pub collected_at: u64,
    pub readings: Vec<Reading>,
}

fn safe_text(text: &str) -> String {
    // Scrub BEFORE truncation so a cutoff cannot split a secret pattern.
    let clean = scrub_logs(&strip_ansi(text));
    let mut bounded: String = clean
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .take(TEXT_LIMIT)
        .collect();
    if clean.chars().count() > TEXT_LIMIT {
        bounded.push_str("\n[output truncated]");
    }
    bounded
}
fn section(name: &str, result: Result<String, String>) -> Reading {
    let (state, detail) = match result {
        Ok(value) => (
            "ok",
            if value.trim().is_empty() {
                "None reported.".into()
            } else {
                value
            },
        ),
        Err(value) => (
            if value.starts_with("error:") {
                "error"
            } else {
                "unavailable"
            },
            value,
        ),
    };
    Reading {
        name: name.into(),
        state: state.into(),
        detail: safe_text(&detail),
    }
}
fn probe_failure(stderr: &str) -> String {
    let lower = stderr.to_lowercase();
    let prefix = if [
        "permission denied",
        "access denied",
        "not authorized",
        "authentication required",
        "must be root",
        "requires root",
        "operation not permitted",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
    {
        "unavailable"
    } else {
        "error"
    };
    format!("{prefix}: check failed: {}", safe_text(stderr))
}
fn failed_services_section(result: Result<String, String>) -> Reading {
    let has_failures = result.as_ref().is_ok_and(|text| !text.trim().is_empty());
    let mut reading = section("Failed services", result);
    if has_failures {
        reading.state = "attention".into();
    }
    reading
}
fn read_fixed(path: &str) -> Result<String, String> {
    let mut text = String::new();
    fs::File::open(path)
        .and_then(|file| file.take(512_001).read_to_string(&mut text))
        .map_err(|error| format!("unavailable: {error}"))?;
    if text.len() > 512_000 {
        return Err("error: reading exceeded size limit".into());
    }
    Ok(text)
}

enum Probe {
    Bootc,
    Gpu,
    FailedServices,
    Controller,
    Disk,
}
fn probe(kind: Probe) -> Result<String, String> {
    let mut command = match kind {
        Probe::Bootc => {
            let mut c = Command::new("/usr/bin/bootc");
            c.args(["status", "--json"]);
            c
        }
        Probe::Gpu => {
            let mut c = Command::new("/usr/bin/lspci");
            c.args(["-nnk"]);
            c
        }
        Probe::FailedServices => {
            let mut c = Command::new("/usr/bin/systemctl");
            c.args(["--failed", "--no-legend", "--no-pager", "--plain"]);
            c
        }
        Probe::Controller => {
            let mut c = Command::new("/usr/bin/rowyn-controller-check");
            c.arg("--json");
            c
        }
        Probe::Disk => {
            let mut c = Command::new("/usr/bin/df");
            c.args(["-h", "--output=size,used,avail,pcent,target", "/", "/var"]);
            c
        }
    };
    command
        .env("LC_ALL", "C")
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin");
    let output = run_bounded_command(command, Duration::from_secs(8))
        .map_err(|error| format!("unavailable: check could not complete: {error}"))?;
    if !output.status.success() {
        return Err(probe_failure(&String::from_utf8_lossy(&output.stderr)));
    }
    if output.stdout.len() > 256_000 {
        return Err("error: check exceeded output limit".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}
fn parse_bootc(raw: &str) -> Result<String, String> {
    let data: serde_json::Value =
        serde_json::from_str(raw).map_err(|_| "error: invalid bootc JSON")?;
    let booted = &data["status"]["booted"]["image"];
    let image = booted["image"]["image"]
        .as_str()
        .ok_or("unavailable: booted image not reported")?;
    let digest = booted["imageDigest"].as_str().unwrap_or("unavailable");
    Ok(format!("Booted image: {image}\nDigest: {digest}"))
}
fn parse_controller(raw: &str) -> Result<Vec<Reading>, String> {
    let data: serde_json::Value =
        serde_json::from_str(raw).map_err(|_| "error: invalid controller JSON")?;
    let findings = data["findings"]
        .as_array()
        .filter(|f| !f.is_empty() && f.len() <= 64)
        .ok_or("error: invalid controller findings")?;
    findings
        .iter()
        .map(|finding| {
            let name = finding["name"]
                .as_str()
                .ok_or("error: missing controller name")?;
            let state = finding["status"]
                .as_str()
                .filter(|s| ["ok", "warn", "fail", "info"].contains(s))
                .ok_or("error: invalid controller status")?;
            let detail = finding["detail"]
                .as_str()
                .ok_or("error: missing controller detail")?;
            // Do not project helper hints, raw devices, identifiers or suggested mutations.
            Ok(Reading {
                name: safe_text(name),
                state: state.into(),
                detail: safe_text(detail),
            })
        })
        .collect()
}
fn controllers() -> Result<Vec<Reading>, String> {
    let _guard = ReadGuard::take(&CONTROLLER_RUNNING)?;
    parse_controller(&probe(Probe::Controller)?)
}
fn controller_section(result: Result<Vec<Reading>, String>) -> Reading {
    match result {
        Err(error) => section("Controller readiness", Err(error)),
        Ok(items) => Reading {
            name: "Controller readiness".into(),
            state: if items
                .iter()
                .any(|item| ["warn", "fail"].contains(&item.state.as_str()))
            {
                "attention"
            } else {
                "ok"
            }
            .into(),
            detail: safe_text(
                &items
                    .iter()
                    .map(|item| format!("{} [{}]: {}", item.name, item.state, item.detail))
                    .collect::<Vec<_>>()
                    .join("\n"),
            ),
        },
    }
}
fn collect() -> Result<Snapshot, String> {
    let _guard = ReadGuard::take(&SNAPSHOT_RUNNING)?;
    let mut readings = vec![
        section(
            "OS",
            read_fixed("/usr/lib/os-release").map(|text| {
                text.lines()
                    .filter(|line| {
                        ["PRETTY_NAME=", "VERSION_ID=", "ID="]
                            .iter()
                            .any(|prefix| line.starts_with(prefix))
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            }),
        ),
        section("Kernel", read_fixed("/proc/sys/kernel/osrelease")),
        section(
            "Memory",
            read_fixed("/proc/meminfo").map(|text| {
                text.lines()
                    .filter(|line| {
                        ["MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"]
                            .iter()
                            .any(|prefix| line.starts_with(prefix))
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            }),
        ),
        section(
            "GPU drivers",
            read_fixed("/proc/modules").map(|text| {
                let modules: Vec<_> = text
                    .lines()
                    .filter_map(|line| line.split_whitespace().next())
                    .filter(|module| ["nvidia", "nouveau", "amdgpu", "i915", "xe"].contains(module))
                    .collect();
                if modules.is_empty() {
                    "No listed GPU module detected; built-in drivers are not listed here.".into()
                } else {
                    modules.join(", ")
                }
            }),
        ),
        section(
            "Bootc image",
            probe(Probe::Bootc).and_then(|raw| parse_bootc(&raw)),
        ),
        section(
            "GPU and bound drivers",
            probe(Probe::Gpu)
                .map(|text| {
                    let mut include = false;
                    text.lines()
                        .filter(|line| {
                            if !line.starts_with(char::is_whitespace) {
                                include = [
                                    "VGA compatible controller",
                                    "3D controller",
                                    "Display controller",
                                ]
                                .iter()
                                .any(|key| line.contains(key));
                            }
                            include
                        })
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .and_then(|text| {
                    if text.is_empty() {
                        Err("unavailable: no graphics adapter reported".into())
                    } else {
                        Ok(text)
                    }
                }),
        ),
        failed_services_section(probe(Probe::FailedServices)),
        section("Disk space", probe(Probe::Disk)),
    ];
    readings.push(controller_section(controllers()));
    Ok(Snapshot {
        collected_at: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
        readings,
    })
}
#[tauri::command]
pub(crate) async fn rowyn_diagnostics_snapshot() -> Result<Snapshot, String> {
    tauri::async_runtime::spawn_blocking(collect)
        .await
        .map_err(|_| "Diagnostics worker failed".to_string())?
}
#[tauri::command]
pub(crate) async fn rowyn_controller_scan() -> Result<Vec<Reading>, String> {
    tauri::async_runtime::spawn_blocking(controllers)
        .await
        .map_err(|_| "Controller worker failed".to_string())?
}
#[tauri::command]
pub(crate) async fn rowyn_export_report(
    app: tauri::AppHandle,
    report: String,
) -> Result<String, String> {
    if report.len() > REPORT_LIMIT {
        return Err("Report exceeds size limit.".into());
    }
    let directory = app
        .path()
        .download_dir()
        .map_err(|_| "Downloads directory unavailable")?;
    tauri::async_runtime::spawn_blocking(move || save_report(&directory, &report))
        .await
        .map_err(|_| "Export worker failed".to_string())?
}
fn save_report(directory: &std::path::Path, report: &str) -> Result<String, String> {
    if report.len() > REPORT_LIMIT {
        return Err("Report exceeds size limit.".into());
    }
    if !directory.is_dir() {
        return Err("Downloads directory does not exist; nothing saved.".into());
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let name = format!("rowyn-diagnostics-{timestamp}.txt");
    let path = directory.join(&name);
    let clean = scrub_logs(&strip_ansi(report));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&path)
        .map_err(|_| "Could not create report; nothing overwritten.")?;
    if file
        .write_all(clean.as_bytes())
        .and_then(|_| file.sync_all())
        .is_err()
    {
        let _ = fs::remove_file(&path);
        return Err("Could not finish saving report.".into());
    }
    Ok(format!("Saved {name} in Downloads. Review before sharing."))
}

#[cfg(test)]
#[path = "rowyn_diagnostics_tests.rs"]
mod tests;
