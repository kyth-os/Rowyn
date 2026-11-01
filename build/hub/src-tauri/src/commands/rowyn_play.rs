//! Small curated launcher opener; installs remain in the existing Flatpak jobs.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use std::process::{Command, Stdio};
use std::time::Duration;

fn launcher_id(id: &str) -> Result<&str, String> {
    match id {
        "com.valvesoftware.Steam"
        | "com.heroicgameslauncher.hgl"
        | "net.lutris.Lutris"
        | "com.usebottles.bottles" => Ok(id),
        _ => Err("Unsupported launcher.".into()),
    }
}
fn installed_scope<'a>(
    id: &str,
    user: &str,
    system: &str,
    failed: &[&str],
) -> Result<&'a str, String> {
    if user.lines().any(|line| line.trim() == id) {
        return Ok("--user");
    }
    if system.lines().any(|line| line.trim() == id) {
        return Ok("--system");
    }
    if !failed.is_empty() {
        Err("Launcher state unavailable: a Flatpak scope could not be read.".into())
    } else {
        Err("Launcher is not installed. Install it first.".into())
    }
}
fn scope_apps(scope: &str) -> Result<String, String> {
    let mut command = Command::new("/usr/bin/flatpak");
    command
        .args(["list", scope, "--app", "--columns=application"])
        .env("LC_ALL", "C");
    let output = run_bounded_command(command, Duration::from_secs(8))
        .map_err(|_| "Flatpak installed-app scan unavailable".to_string())?;
    if !output.status.success() || output.stdout.len() > 256_000 {
        return Err("Flatpak installed-app scan failed".into());
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}
#[tauri::command]
pub(crate) async fn rowyn_launcher_open(id: String) -> Result<String, String> {
    launcher_id(&id)?;
    tauri::async_runtime::spawn_blocking(move || {
        let user = scope_apps("--user");
        let system = scope_apps("--system");
        let failed: Vec<&str> = [("user", &user), ("system", &system)].iter()
            .filter_map(|(scope, result)| result.is_err().then_some(*scope)).collect();
        let scope = installed_scope(&id, user.as_deref().unwrap_or(""), system.as_deref().unwrap_or(""), &failed)?;
        let mut command = Command::new("/usr/bin/flatpak");
        command.args(["run", scope, "--", &id]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        spawn_detached(&mut command).map_err(|_| "Could not start launcher.".to_string())?;
        // Detached application: do not claim its window or login succeeded.
        Ok("Launch requested. If no window appears, inspect the launcher itself; window readiness is not verified.".into())
    }).await.map_err(|_| "Launcher worker failed".to_string())?
}
#[cfg(test)]
#[path = "rowyn_play_tests.rs"]
mod tests;
