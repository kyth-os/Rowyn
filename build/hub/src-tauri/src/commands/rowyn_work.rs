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

#[cfg(test)]
#[path = "rowyn_work_tests.rs"]
mod tests;
