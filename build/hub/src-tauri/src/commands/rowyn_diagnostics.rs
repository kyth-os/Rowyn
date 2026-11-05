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
    let clean = super::rowyn_support::sanitize_report(text);
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

#[derive(Clone, Copy)]
enum Probe {
    Bootc,
    Gpu,
    FailedServices,
    Controller,
    Disk,
    Bluetooth, Wifi, WifiRadio, Audio, Displays,
}
fn rfkill_state<'a>(device: &'a serde_json::Value, key: &str) -> Option<&'a str> { device[key].as_str() }
fn parse_rfkill(raw: &str) -> Result<String, String> {
    let data: serde_json::Value = serde_json::from_str(raw).map_err(|_| "error: invalid rfkill JSON")?;
    let devices = data["rfkill"].as_array().ok_or("error: invalid rfkill device list")?;
    let bt: Vec<_> = devices.iter().filter(|d| d["type"].as_str() == Some("bluetooth")).collect();
    if bt.is_empty() { return Ok("Bluetooth radio not present.".into()); }
    if bt.iter().any(|d| rfkill_state(d, "hard") == Some("blocked")) { return Ok("Bluetooth radio hard-blocked.".into()); }
    if bt.iter().any(|d| rfkill_state(d, "hard") != Some("unblocked") || rfkill_state(d, "soft").is_none()) { return Err("unavailable: Bluetooth block state not reported".into()); }
    if bt.iter().all(|d| rfkill_state(d, "soft") == Some("blocked")) { return Ok("Bluetooth radio soft-blocked.".into()); }
    if bt.iter().all(|d| rfkill_state(d, "soft") == Some("unblocked")) { return Ok("Bluetooth radio unblocked; connection not checked.".into()); }
    Err("unavailable: Bluetooth block state not reported".into())
}
fn parse_wifi(raw: &str, radio: &str) -> Result<String, String> {
    let rows: Vec<_> = raw.lines().filter_map(|line| { let f: Vec<_> = line.split(':').collect(); (f.len() >= 3 && f[1] == "wifi").then_some(f[2]) }).collect();
    if rows.is_empty() { return Ok("No Wi-Fi device reported.".into()); }
    match radio.trim() { "enabled" if rows.iter().any(|s| *s == "connected") => Ok("Wi-Fi radio enabled; device connected. Network names omitted.".into()), "enabled" => Ok("Wi-Fi radio enabled; no connected device reported.".into()), "disabled" => Ok("Wi-Fi radio disabled.".into()), _ => Err("unavailable: Wi-Fi radio state not reported".into()) }
}
fn parse_audio(raw: &str) -> Result<String, String> {
    let states: Vec<_> = raw.lines().map(str::trim).filter(|s| !s.is_empty()).collect();
    if states.len() != 2 || states.iter().any(|s| !["active", "inactive", "failed", "activating", "deactivating"].contains(s)) {
        return Err("unavailable: audio service state not fully reported".into());
    }
    Ok(format!("PipeWire: {}; WirePlumber: {}. Service readiness only; audio output is not tested.", states[0], states[1]))
}
fn parse_displays(raw: &str) -> Result<String, String> {
    let outputs: Vec<_> = raw.lines().filter(|l| l.trim_start().starts_with("Output:")).collect();
    if outputs.is_empty() { return Err("unavailable: no display outputs reported".into()); }
    let mut connected = 0;
    let mut enabled = 0;
    for output in &outputs {
        let tokens: Vec<_> = output.split_whitespace().collect();
        let connection: Vec<_> = tokens.iter().filter(|t| ["connected", "disconnected"].contains(&t.trim_matches(['(', ')', ',']))).collect();
        let power: Vec<_> = tokens.iter().filter(|t| ["enabled", "disabled"].contains(&t.trim_matches(['(', ')', ',']))).collect();
        if connection.len() != 1 || power.len() != 1 { return Err("unavailable: display state output malformed".into()); }
        connected += usize::from(connection[0].trim_matches(['(', ')', ',']) == "connected");
        enabled += usize::from(power[0].trim_matches(['(', ')', ',']) == "enabled");
    }
    Ok(format!("{} connected display output(s) reported; {} enabled. Identifiers and configuration omitted.", connected, enabled))
}
fn focused_reading(name: &str, result: Result<String, String>) -> Reading {
    let reading = section(name, result);
    if reading.state != "ok" { return reading; }
    let lower = reading.detail.to_lowercase();
    let attention = match name {
        "Bluetooth" => lower.contains("blocked") || lower.contains("not present"),
        "Wi-Fi" => lower.contains("disabled") || lower.contains("no connected device") || lower.contains("not present"),
        "Audio" => lower.contains("inactive") || lower.contains("failed"),
        "Displays" => lower.contains("0 enabled") || lower.contains("0 connected"),
        _ => false,
    };
    Reading { state: if attention { "attention" } else { "ok" }.into(), ..reading }
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
        Probe::Disk => { let mut c = Command::new("/usr/bin/df"); c.args(["-h", "--output=size,used,avail,pcent,target", "/", "/var"]); c }            Probe::Bluetooth => { let mut c = Command::new("/usr/sbin/rfkill"); c.args(["--json"]); c }
        Probe::Wifi => { let mut c=Command::new("/usr/bin/nmcli"); c.args(["-t","-f","DEVICE,TYPE,STATE","device","status"]); c }
        Probe::WifiRadio => { let mut c=Command::new("/usr/bin/nmcli"); c.args(["radio","wifi"]); c }
        Probe::Audio => { let mut c=Command::new("/usr/bin/systemctl"); c.args(["--user","is-active","pipewire.service","wireplumber.service"]); c }
        Probe::Displays => { let mut c=Command::new("/usr/bin/kscreen-doctor"); c.arg("-o"); c }
    };
    command
        .env("LC_ALL", "C")
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin");
    let output = run_bounded_command(command, Duration::from_secs(8))
        .map_err(|error| format!("unavailable: check could not complete: {error}"))?;
    if !output.status.success() && !matches!(kind, Probe::Audio) {
        return Err(probe_failure(&String::from_utf8_lossy(&output.stderr)));
    }
    if matches!(kind, Probe::Audio) && output.stdout.is_empty() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(if stderr.trim().is_empty() { "unavailable: audio service state not reported".into() } else { probe_failure(&stderr) });
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
    let provenance = super::rowyn_support::refresh_public_provenance();
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
        section("CPU model", read_fixed("/proc/cpuinfo").and_then(|raw| {
            raw.lines().find_map(|line| line.strip_prefix("model name").and_then(|value| value.split_once(':')).map(|(_, value)| value.trim().to_owned()))
                .ok_or_else(|| "unavailable: CPU model is not reported".into())
        })),
        section("NVIDIA module version", read_fixed("/sys/module/nvidia/version")),
        section("Update health record", read_fixed("/var/lib/rowyn/boot-health.json").and_then(|raw| {
            let value: serde_json::Value = serde_json::from_str(&raw).map_err(|_| "error: invalid boot-health JSON".to_string())?;
            if !value.is_object() { return Err("error: invalid boot-health record".into()); }
            let selected: serde_json::Map<String, serde_json::Value> = ["schema_version", "failures", "quarantined", "rollback", "last_boot"]
                .into_iter().filter_map(|key| value.get(key).map(|item| (key.to_owned(), item.clone()))).collect();
            serde_json::to_string_pretty(&selected).map_err(|_| "error: could not format update health".into())
        })),
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
    readings.extend(provenance.into_iter().map(|(name, state, detail)| Reading { name, state, detail }));
    readings.push(controller_section(controllers()));
    readings.push(focused_reading("Bluetooth", probe(Probe::Bluetooth).and_then(|raw| parse_rfkill(&raw))));
    readings.push(focused_reading("Wi-Fi", probe(Probe::Wifi).and_then(|raw| parse_wifi(&raw, &probe(Probe::WifiRadio)?))));
    readings.push(focused_reading("Audio", probe(Probe::Audio).and_then(|raw| parse_audio(&raw))));
    readings.push(focused_reading("Displays", probe(Probe::Displays).and_then(|raw| parse_displays(&raw))));
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
    let clean = super::rowyn_support::sanitize_report(report);
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
