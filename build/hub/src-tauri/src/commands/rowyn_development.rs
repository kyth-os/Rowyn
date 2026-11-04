//! Dedicated, fail-closed backend for the fixed Rowyn Ubuntu development box.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, VecDeque},
    process::{Command, Stdio},
    sync::{Arc, Mutex, OnceLock},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
const NAME: &str = "rowyn-ubuntu-dev";
const IMAGE: &str = "docker.io/library/ubuntu:26.10";
const LABEL: &str = "io.rowyn.development";
const LABEL_VALUE: &str = "ubuntu-v1";
const MAX_OUTPUT: usize = 512 * 1024;
const PODMAN: &str = "/usr/bin/podman";
const DISTROBOX: &str = "/usr/bin/distrobox";
const MAX_JOBS: usize = 64;
static LIFECYCLE: OnceLock<Mutex<()>> = OnceLock::new();
#[derive(Clone, Debug)]
struct ContainerIdentity {
    id: String,
    name: String,
    image: String,
    labels: BTreeMap<String, String>,
    status: String,
}
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DevelopmentState {
    state: String,
    name: String,
    image: String,
    status: String,
    detail: String,
}
#[derive(Serialize, Clone, Debug)]
pub(crate) struct DevelopmentJob {
    id: String,
    status: String,
    detail: String,
}
#[derive(Deserialize)]
struct PodmanRow {
    #[serde(rename = "Id", alias = "ID", default)]
    id: String,
    #[serde(rename = "Names", default)]
    names: Vec<String>,
    #[serde(rename = "Image", default)]
    image: String,
    #[serde(rename = "State", default)]
    state: String,
    #[serde(rename = "Labels", default)]
    labels: BTreeMap<String, String>,
}
struct Entry {
    job: DevelopmentJob,
    cancel: Option<Arc<std::sync::atomic::AtomicBool>>,
}
static JOBS: OnceLock<Mutex<(HashMap<String, Entry>, VecDeque<String>)>> = OnceLock::new();
fn jobs() -> &'static Mutex<(HashMap<String, Entry>, VecDeque<String>)> {
    JOBS.get_or_init(|| Mutex::new((HashMap::new(), VecDeque::new())))
}
fn validate_name(n: &str) -> Result<(), String> {
    if n == NAME {
        Ok(())
    } else {
        Err("Unsupported development environment name.".into())
    }
}
fn ownership(c: &ContainerIdentity) -> bool {
    c.name == NAME
        && c.image == IMAGE
        && c.labels.get(LABEL).map(String::as_str) == Some(LABEL_VALUE)
        && c.labels.get("manager").map(String::as_str) == Some("distrobox")
}
fn parse_list(b: &[u8]) -> Result<Vec<ContainerIdentity>, String> {
    if b.len() > MAX_OUTPUT {
        return Err("Podman output exceeded limit.".into());
    }
    let rows: Vec<PodmanRow> = serde_json::from_slice(b)
        .map_err(|_| "Podman returned invalid container data.".to_string())?;
    if rows.len() > 128 {
        return Err("Podman returned too many containers.".into());
    }
    let mut parsed = Vec::with_capacity(rows.len());
    for r in rows {
        let Some(n) = r.names.first() else {
            return Err("Podman returned a container without a name.".into());
        };
        if r.id.len() != 64
            || !r
                .id
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err("Podman returned an invalid container ID.".into());
        }
        parsed.push(ContainerIdentity {
            id: r.id,
            name: n.trim_start_matches('/').into(),
            image: r.image,
            labels: r.labels,
            status: r.state,
        });
    }
    Ok(parsed)
}
fn create_args() -> Vec<&'static str> {
    vec![
        "create",
        "--name",
        NAME,
        "--image",
        IMAGE,
        "--additional-flags",
        "--label io.rowyn.development=ubuntu-v1",
        "--yes",
    ]
}
fn run(exe: &str, args: &[&str], timeout: Duration) -> Result<Vec<u8>, String> {
    let mut c = Command::new(exe);
    c.args(args).env("LC_ALL", "C");
    if exe == DISTROBOX {
        c.env("DBX_CONTAINER_MANAGER", "podman");
    }
    let o = run_bounded_command(c, timeout)
        .map_err(|_| format!("{} unavailable or timed out.", exe))?;
    if !o.status.success() {
        return Err(format!("{} failed.", exe));
    }
    if o.stdout.len() > MAX_OUTPUT || o.stderr.len() > MAX_OUTPUT {
        return Err("Command output exceeded limit.".into());
    }
    Ok(o.stdout)
}
fn inspect_all() -> Result<Vec<ContainerIdentity>, String> {
    parse_list(&run(
        PODMAN,
        &[
            "ps",
            "-a",
            "--filter",
            "name=^rowyn-ubuntu-dev$",
            "--format",
            "json",
        ],
        Duration::from_secs(10),
    )?)
}
fn state_inner() -> Result<DevelopmentState, String> {
    let v: Vec<_> = inspect_all()?
        .into_iter()
        .filter(|r| r.name == NAME)
        .collect();
    match v.as_slice() {
        [] => Ok(DevelopmentState {
            state: "absent".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: "absent".into(),
            detail: "Environment not created.".into(),
        }),
        [c] if ownership(c) => Ok(DevelopmentState {
            state: "managed".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: c.status.clone(),
            detail: "Rowyn-managed environment.".into(),
        }),
        _ => Ok(DevelopmentState {
            state: "collision".into(),
            name: NAME.into(),
            image: IMAGE.into(),
            status: "unknown".into(),
            detail: "Reserved name is not owned by Rowyn; no changes made.".into(),
        }),
    }
}
#[tauri::command]
pub(crate) async fn rowyn_development_list() -> Result<DevelopmentState, String> {
    tauri::async_runtime::spawn_blocking(state_inner)
        .await
        .map_err(|_| "Development worker failed".to_string())?
}
fn child(
    mut c: Command,
    flag: Arc<std::sync::atomic::AtomicBool>,
    timeout: Duration,
) -> Result<(), String> {
    use std::os::unix::process::CommandExt;
    use std::sync::atomic::Ordering;
    c.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);
    let mut p = c
        .spawn()
        .map_err(|_| "Could not start development command.".to_string())?;
    let group = p.id() as i32;
    let t = std::time::Instant::now();
    loop {
        if flag.load(Ordering::SeqCst) || t.elapsed() > timeout {
            unsafe {
                libc::kill(-group, libc::SIGTERM);
            }
            thread::sleep(Duration::from_millis(250));
            unsafe {
                libc::kill(-group, libc::SIGKILL);
            }
            let _ = p.wait();
            return Err(if flag.load(Ordering::SeqCst) {
                "Cancelled.".into()
            } else {
                "Development command timed out.".into()
            });
        }
        match p.try_wait() {
            Ok(Some(s)) => {
                return if s.success() {
                    Ok(())
                } else {
                    Err("Development command failed.".into())
                }
            }
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(_) => {
                unsafe {
                    libc::kill(-group, libc::SIGKILL);
                }
                let _ = p.wait();
                return Err("Could not monitor development command.".into());
            }
        }
    }
}
fn perform(
    op: String,
    confirmation: Option<String>,
    cancel: Arc<std::sync::atomic::AtomicBool>,
) -> Result<String, String> {
    use std::sync::atomic::Ordering;
    let _guard = LIFECYCLE
        .get_or_init(|| Mutex::new(()))
        .try_lock()
        .map_err(|_| "Development lifecycle is busy.".to_string())?;
    if cancel.load(Ordering::SeqCst) {
        return Err("Cancelled.".into());
    }
    match op.as_str() {
        "create" => {
            if confirmation.is_some() {
                return Err("Create does not accept confirmation.".into());
            }
            if state_inner()?.state != "absent" {
                return Err("Reserved name is occupied.".into());
            }
            let mut c = Command::new(DISTROBOX);
            c.args(create_args())
                .env("DBX_CONTAINER_MANAGER", "podman")
                .env("LC_ALL", "C");
            child(c, cancel, Duration::from_secs(300))?;
            if state_inner()?.state != "managed" {
                return Err(
                    "Create command finished, but managed environment could not be verified."
                        .into(),
                );
            }
            Ok("Environment created.".into())
        }
        "delete" => {
            if confirmation.as_deref() != Some(NAME) {
                return Err("Delete requires exact environment-name confirmation.".into());
            }
            let c = inspect_all()?
                .into_iter()
                .find(|r| r.name == NAME)
                .ok_or("Environment is absent.")?;
            if !ownership(&c) {
                return Err("Environment ownership could not be verified.".into());
            }
            if c.status.to_lowercase().contains("running") {
                run(
                    PODMAN,
                    &["stop", "--time", "10", &c.id],
                    Duration::from_secs(20),
                )?;
            }
            if cancel.load(Ordering::SeqCst) {
                return Err("Cancelled.".into());
            }
            let c = inspect_all()?
                .into_iter()
                .find(|r| r.id == c.id)
                .ok_or("Container identity changed.".to_string())?;
            if !ownership(&c) || c.name != NAME || c.status.to_lowercase().contains("running") {
                return Err("Container changed; removal refused.".into());
            }
            run(PODMAN, &["rm", &c.id], Duration::from_secs(30))?;
            if inspect_all()?.iter().any(|r| r.id == c.id) {
                return Err("Removal command finished, but container still exists.".into());
            }
            Ok("Environment deleted; container processes stopped.".into())
        }
        _ => Err("Unsupported operation.".into()),
    }
}
fn id() -> String {
    format!(
        "development-{}-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
        std::process::id()
    )
}
fn finish(id: &str, result: Result<String, String>) {
    if let Ok(mut x) = jobs().lock() {
        if let Some(e) = x.0.get_mut(id) {
            match result {
                Ok(s) => {
                    e.job.status = "succeeded".into();
                    e.job.detail = s
                }
                Err(s) => {
                    e.job.status = if s == "Cancelled." {
                        "cancelled"
                    } else {
                        "failed"
                    }
                    .into();
                    e.job.detail = s
                }
            }
            e.cancel = None
        }
    }
}
#[tauri::command]
pub(crate) async fn rowyn_development_start(
    operation: String,
    confirmation: Option<String>,
) -> Result<DevelopmentJob, String> {
    if !matches!(operation.as_str(), "create" | "delete") {
        return Err("Unsupported development operation.".into());
    }
    if operation == "delete" && confirmation.as_deref() != Some(NAME) {
        return Err("Delete requires exact environment-name confirmation.".into());
    }
    if operation == "create" && confirmation.is_some() {
        return Err("Create does not accept confirmation.".into());
    }
    let ident = id();
    let flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    {
        let mut x = jobs()
            .lock()
            .map_err(|_| "Job registry unavailable".to_string())?;
        while x.1.len() >= MAX_JOBS {
            if let Some(old) = x.1.pop_front() {
                if x.0
                    .get(&old)
                    .map(|e| e.job.status != "running")
                    .unwrap_or(true)
                {
                    x.0.remove(&old);
                } else {
                    x.1.push_front(old);
                    break;
                }
            }
        }
        if x.0.len() >= MAX_JOBS {
            return Err("Job registry is full.".into());
        }
        if x.0.values().any(|e| e.job.status == "running") {
            return Err("A development lifecycle job is already running.".into());
        }
        x.0.insert(
            ident.clone(),
            Entry {
                job: DevelopmentJob {
                    id: ident.clone(),
                    status: "running".into(),
                    detail: "Operation started.".into(),
                },
                cancel: Some(flag.clone()),
            },
        );
        x.1.push_back(ident.clone())
    }
    let worker_id = ident.clone();
    thread::spawn(move || finish(&worker_id, perform(operation, confirmation, flag)));
    Ok(DevelopmentJob {
        id: ident,
        status: "running".into(),
        detail: "Operation started.".into(),
    })
}
#[tauri::command]
pub(crate) async fn rowyn_development_job(id: String) -> Result<DevelopmentJob, String> {
    jobs()
        .lock()
        .map_err(|_| "Job registry unavailable".to_string())?
        .0
        .get(&id)
        .map(|e| e.job.clone())
        .ok_or_else(|| "Unknown development job.".into())
}
#[tauri::command]
pub(crate) async fn rowyn_development_cancel(id: String) -> Result<DevelopmentJob, String> {
    let mut x = jobs()
        .lock()
        .map_err(|_| "Job registry unavailable".to_string())?;
    let e = x.0.get_mut(&id).ok_or("Unknown development job.")?;
    if let Some(c) = &e.cancel {
        c.store(true, std::sync::atomic::Ordering::SeqCst);
        e.job.detail = "Cancellation requested.".into()
    }
    Ok(e.job.clone())
}
#[tauri::command]
pub(crate) async fn rowyn_development_open() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let _g = LIFECYCLE
            .get_or_init(|| Mutex::new(()))
            .try_lock()
            .map_err(|_| "Development lifecycle is busy.".to_string())?;
        if state_inner()?.state != "managed" {
            return Err("Managed environment unavailable.".into());
        }
        let mut c = Command::new("/usr/bin/konsole");
        c.args(["--separate", "-e", DISTROBOX, "enter", "--name", NAME]);
        c.env("DBX_CONTAINER_MANAGER", "podman");
        spawn_detached(&mut c).map_err(|_| "Could not request terminal window.".to_string())?;
        Ok("Terminal launch requested; readiness unverified.".into())
    })
    .await
    .map_err(|_| "Development worker failed".to_string())?
}
#[cfg(test)]
#[path = "rowyn_development_tests.rs"]
mod tests;
