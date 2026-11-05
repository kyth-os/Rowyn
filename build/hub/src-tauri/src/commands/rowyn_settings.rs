//! Curated entry points for KDE system settings modules.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use std::process::{Command, Stdio};
use std::time::Duration;

const KCMSHELL: &str = "/usr/bin/kcmshell6";
const MAX_LIST_OUTPUT: usize = 64 * 1024;

fn settings_module(target: &str) -> Result<&'static str, String> {
    match target {
        "backups" => Ok("kcm_kup"),
        "bluetooth" => Ok("kcm_bluetooth"),
        "wifi" => Ok("kcm_networkmanagement"),
        "audio" => Ok("kcm_pulseaudio"),
        // Power management itself, not the advanced profile editor: this is
        // the panel the Power care readouts describe.
        "power" => Ok("kcm_powerdevil"),
        "displays" => Ok("kcm_kscreen"),
        "defaults" => Ok("kcm_componentchooser"),
        "file-associations" => Ok("kcm_filetypes"),
        // Ubuntu 26.10 kde-config-flatpak filelist ships kcm_app-permissions.
        "flatpak-permissions" => Ok("kcm_app-permissions"),
        _ => Err("Unsupported settings target.".into()),
    }
}

fn parse_module_list(output: &[u8]) -> Result<Vec<String>, String> {
    if output.len() > MAX_LIST_OUTPUT {
        return Err("KDE settings module list exceeded the output limit.".into());
    }
    let text = std::str::from_utf8(output)
        .map_err(|_| "KDE settings module list was not valid UTF-8.".to_string())?;
    Ok(text.lines().filter_map(|line| line.split_whitespace().next().map(str::to_owned)).collect())
}

fn module_list_command() -> Command {
    let mut command = Command::new(KCMSHELL);
    command.arg("--list").stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    command
}

fn launch_command(module: &str) -> Command {
    let mut command = Command::new(KCMSHELL);
    command.arg(module).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    command
}

#[tauri::command]
pub(crate) async fn rowyn_open_settings(target: String) -> Result<String, String> {
    let module = settings_module(&target)?;
    tauri::async_runtime::spawn_blocking(move || {
        let output = run_bounded_command(module_list_command(), Duration::from_secs(5))
            .map_err(|_| "Could not check installed KDE settings modules.".to_string())?;
        if !output.status.success() {
            return Err("Could not check installed KDE settings modules.".into());
        }
        let modules = parse_module_list(&output.stdout)?;
        if !modules.iter().any(|listed| listed == module) {
            return Err(format!("KDE settings module {module} is not installed."));
        }
        let mut command = launch_command(module);
        spawn_detached(&mut command).map_err(|_| "Could not open KDE settings module.".to_string())?;
        Ok("Settings launch requested.".into())
    })
    .await
    .map_err(|_| "Settings worker failed.".to_string())?
}

#[cfg(test)]
#[path = "rowyn_settings_tests.rs"]
mod tests;
