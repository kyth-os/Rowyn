//! Work page bridge: CUPS queue state and one explicitly confirmed test page.
//!
//! Both commands are fixed-argument and bounded. The test page is the file the
//! image installs, so the Hub never builds print data and never has to trust a
//! path from the webview: the queue name is validated, and the file is a
//! constant.
use rowyn_shared::system::process::run_bounded_command;
use serde::Serialize;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

const LPSTAT: &str = "/usr/bin/lpstat";
const LP: &str = "/usr/bin/lp";
/// Installed by the Containerfile. A missing file is reported, never faked.
const TEST_PAGE: &str = "/usr/share/rowyn/print-test-page.txt";
const QUEUE_TIMEOUT: Duration = Duration::from_secs(8);
const PRINT_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_OUTPUT: usize = 64 * 1024;
const MAX_QUEUES: usize = 64;
const MAX_QUEUE_NAME: usize = 64;
const MAX_URI: usize = 512;
/// Adding a queue writes CUPS configuration and can wait on authorization.
const ADMIN_TIMEOUT: Duration = Duration::from_secs(20);
const LPADMIN: &str = "/usr/sbin/lpadmin";

#[derive(Serialize, PartialEq, Debug, Clone)]
pub(crate) struct PrintQueue {
    name: String,
    state: String,
    is_default: bool,
}

#[derive(Serialize, PartialEq, Debug)]
pub(crate) struct PrintQueues {
    queues: Vec<PrintQueue>,
    default_queue: Option<String>,
    detail: String,
}

/// `lpstat -p -d` in the C locale prints one line per queue
/// (`printer NAME is idle.`, `printer NAME disabled since ...`,
/// `printer NAME now printing NAME-42.`) plus the default destination.
/// Anything else is ignored: an unparsed line must not become a queue.
fn parse_lpstat(text: &str) -> (Vec<PrintQueue>, Option<String>) {
    let mut queues: Vec<PrintQueue> = Vec::new();
    let mut default_queue: Option<String> = None;
    for line in text.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("system default destination:") {
            let name = rest.trim();
            if !name.is_empty() && name != "none" {
                default_queue = Some(name.to_string());
            }
            continue;
        }
        let Some(rest) = line.strip_prefix("printer ") else {
            continue;
        };
        let Some(name) = rest.split_whitespace().next() else {
            continue;
        };
        if queues.iter().any(|queue| queue.name == name) {
            continue;
        }
        if queues.len() >= MAX_QUEUES {
            break;
        }
        let lowered = rest.to_ascii_lowercase();
        // "disabled" first: a disabled queue can still carry "now printing"
        // text from the job that was held when it went down.
        let state = if lowered.contains("disabled") {
            "disabled"
        } else if lowered.contains("now printing") || lowered.contains(" is printing") {
            "printing"
        } else if lowered.contains("is idle") {
            "idle"
        } else {
            "unknown"
        };
        queues.push(PrintQueue { name: name.to_string(), state: state.to_string(), is_default: false });
    }
    if let Some(default) = default_queue.as_deref() {
        for queue in queues.iter_mut() {
            if queue.name == default {
                queue.is_default = true;
            }
        }
    }
    (queues, default_queue)
}

/// A queue name is passed as one argv entry, so only a name that cannot be
/// read as a flag or a path is accepted.
fn queue_argument(name: &str) -> Result<&str, String> {
    if name.is_empty() || name.len() > MAX_QUEUE_NAME || name.starts_with('-') {
        return Err("Invalid print queue name.".into());
    }
    if !name
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '@'))
    {
        return Err("Invalid print queue name.".into());
    }
    Ok(name)
}

fn first_line(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    text.lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("")
        .chars()
        .take(160)
        .collect()
}

/// Fixed argument shape for a driverless queue: `-m everywhere` asks CUPS for
/// the IPP Everywhere model instead of a vendor driver.
fn add_queue_args<'a>(name: &'a str, uri: &'a str) -> Vec<&'a str> {
    vec!["-p", name, "-E", "-v", uri, "-m", "everywhere"]
}

/// Only IPP service addresses are accepted: they are what discovery reports,
/// and they cannot be read as a flag or carry whitespace into argv.
fn printer_uri(uri: &str) -> Result<&str, String> {
    if uri.is_empty() || uri.len() > MAX_URI {
        return Err("Invalid printer address.".into());
    }
    let lowered = uri.to_ascii_lowercase();
    if !(lowered.starts_with("ipp://") || lowered.starts_with("ipps://")) {
        return Err("Only IPP printer addresses can become a queue.".into());
    }
    if uri.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return Err("Invalid printer address.".into());
    }
    Ok(uri)
}

/// Queue names CUPS already has. `lpadmin -p` edits an existing queue instead
/// of failing, so this is the guard that keeps an add from rewriting one.
fn queue_names() -> Result<Vec<String>, String> {
    let mut command = Command::new(LPSTAT);
    command.args(["-e"]).env("LC_ALL", "C").stdin(Stdio::null());
    let output = run_bounded_command(command, QUEUE_TIMEOUT).map_err(|_| {
        "Printing is not available: the CUPS client (lpstat) did not answer.".to_string()
    })?;
    if !output.status.success() {
        return Err("CUPS did not answer, so the existing queue names could not be checked.".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect())
}

fn add_failure(queue: &str, stderr: &[u8]) -> String {
    let reason = first_line(stderr);
    let lowered = reason.to_ascii_lowercase();
    let hint = if lowered.contains("unauthorized")
        || lowered.contains("forbidden")
        || lowered.contains("not allowed")
        || lowered.contains("permission")
    {
        " This account may not administer printers; Print settings can add it with the system authorization prompt."
    } else {
        ""
    };
    if reason.is_empty() {
        format!("CUPS did not create the queue {queue}.{hint}")
    } else {
        format!("CUPS did not create the queue {queue}: {reason}{hint}")
    }
}

#[tauri::command]
pub(crate) async fn rowyn_print_queues() -> Result<PrintQueues, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut command = Command::new(LPSTAT);
        command.args(["-p", "-d"]).env("LC_ALL", "C").stdin(Stdio::null());
        let output = run_bounded_command(command, QUEUE_TIMEOUT).map_err(|_| {
            "Printing is not available: the CUPS client (lpstat) did not answer.".to_string()
        })?;
        if !output.status.success() {
            return Err("CUPS did not answer, so print queues could not be read. Check that cups.service is running.".into());
        }
        if output.stdout.len() > MAX_OUTPUT {
            return Err("The print queue list was too large to read safely.".into());
        }
        let (queues, default_queue) = parse_lpstat(&String::from_utf8_lossy(&output.stdout));
        let detail = if queues.is_empty() {
            "No print queues are configured yet. Add a printer in Print settings; USB printers appear through ipp-usb.".to_string()
        } else if default_queue.is_none() {
            "Queues are configured but none is the default, so each job has to name its queue.".to_string()
        } else {
            "Read from CUPS on this machine; nothing was changed.".to_string()
        };
        Ok(PrintQueues { queues, default_queue, detail })
    })
    .await
    .map_err(|_| "Print queue worker failed.".to_string())?
}

#[tauri::command]
pub(crate) async fn rowyn_print_test_page(queue: String) -> Result<String, String> {
    let queue = queue_argument(&queue)?.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        if !Path::new(TEST_PAGE).is_file() {
            return Err("The Rowyn test page is missing from this system, so no page was sent.".to_string());
        }
        let mut command = Command::new(LP);
        command
            .args(["-d", &queue, "-t", "Rowyn Hub test page", TEST_PAGE])
            .env("LC_ALL", "C")
            .stdin(Stdio::null());
        let output = run_bounded_command(command, PRINT_TIMEOUT).map_err(|_| {
            "CUPS did not accept the test page: the client (lp) did not answer.".to_string()
        })?;
        if !output.status.success() {
            let reason = first_line(&output.stderr);
            return Err(if reason.is_empty() {
                format!("CUPS rejected the test page on {queue}.")
            } else {
                format!("CUPS rejected the test page on {queue}: {reason}")
            });
        }
        let job = first_line(&output.stdout);
        if job.is_empty() {
            return Err(format!(
                "CUPS accepted the job on {queue} but named no job id; check the queue."
            ));
        }
        Ok(format!(
            "Test page queued on {queue} ({job}). The printer confirms whether it prints; the Hub cannot."
        ))
    })
    .await
    .map_err(|_| "Print worker failed.".to_string())?
}

#[tauri::command]
pub(crate) async fn rowyn_print_add_queue(queue: String, uri: String) -> Result<String, String> {
    let name = queue_argument(&queue)?.to_string();
    let uri = printer_uri(&uri)?.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        // Fail closed: if the queue list cannot be read, do not run a command
        // that would silently modify a queue whose name collides.
        if queue_names()?.iter().any(|existing| existing == &name) {
            return Err(format!(
                "A print queue named {name} already exists, so nothing was changed. Edit it in Print settings, or add the printer under another name."
            ));
        }
        let mut command = Command::new(LPADMIN);
        command
            .args(add_queue_args(&name, &uri))
            .env("LC_ALL", "C")
            .stdin(Stdio::null());
        let output = run_bounded_command(command, ADMIN_TIMEOUT).map_err(|_| {
            "CUPS did not accept the queue: the administration client (lpadmin) did not answer."
                .to_string()
        })?;
        if !output.status.success() {
            return Err(add_failure(&name, &output.stderr));
        }
        Ok(format!(
            "Queue {name} created for {uri}. CUPS owns its options now; send a test page to check it."
        ))
    })
    .await
    .map_err(|_| "Print queue worker failed.".to_string())?
}

#[cfg(test)]
#[path = "rowyn_work_tests.rs"]
mod tests;
