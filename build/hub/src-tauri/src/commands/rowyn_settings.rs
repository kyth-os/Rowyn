//! Curated entry points for KDE system settings modules.
use rowyn_shared::system::process::spawn_detached;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

// Plasma 6 ships kcmshell6, Plasma 5 still ships kcmshell5, and
// `systemsettings` opens a named module on both. Resolving at runtime keeps
// the buttons working on the host the Hub is actually running on instead of
// only on the image that ships Plasma 6.
const LAUNCHERS: &[&str] = &[
    "/usr/bin/kcmshell6",
    "/usr/bin/kcmshell5",
    "/usr/bin/systemsettings",
];

// KCM plugins, per Qt major version. No directory at all means this check
// cannot judge the host (it is not a KDE install), and that must not block a
// launch the user asked for.
const KCM_DIRS: &[&str] = &[
    // Plasma 6.
    "/usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings",
    "/usr/lib64/qt6/plugins/plasma/kcms/systemsettings",
    "/usr/lib/qt6/plugins/plasma/kcms/systemsettings",
    // Plasma 5 keeps the same tree under qt5, split into QML modules and
    // qwidgets ones (kcm_filetypes, for example, only ships in the latter).
    "/usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/kcms/systemsettings",
    "/usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/kcms/systemsettings_qwidgets",
    "/usr/lib64/qt5/plugins/plasma/kcms/systemsettings",
    "/usr/lib64/qt5/plugins/plasma/kcms/systemsettings_qwidgets",
    "/usr/lib/qt5/plugins/plasma/kcms/systemsettings",
    "/usr/lib/qt5/plugins/plasma/kcms/systemsettings_qwidgets",
];

// Plasma 5's kde-config-flatpak ships the permissions panel under this name.
const FLATPAK_ALTERNATES: &[&str] = &["kcm_flatpak"];

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

/// Modules this target may open, most specific first.
fn modules_for(target: &str) -> Result<Vec<&'static str>, String> {
    let primary = settings_module(target)?;
    let mut modules = vec![primary];
    if primary == "kcm_app-permissions" {
        modules.extend_from_slice(FLATPAK_ALTERNATES);
    }
    Ok(modules)
}

fn first_existing(paths: &[&str]) -> Option<PathBuf> {
    paths
        .iter()
        .map(Path::new)
        .find(|path| path.is_file())
        .map(Path::to_path_buf)
}

fn launcher_binary() -> Result<PathBuf, String> {
    first_existing(LAUNCHERS).ok_or_else(|| "No KDE settings launcher is installed.".to_string())
}

/// `Some(true)`/`Some(false)` once a KCM directory was found, `None` when the
/// host has no KCM plugin directory at all.
fn module_installed_in(module: &str, dirs: &[&str]) -> Option<bool> {
    let file = format!("{module}.so");
    let mut searched = false;
    for dir in dirs {
        let path = Path::new(dir);
        if !path.is_dir() {
            continue;
        }
        searched = true;
        if path.join(&file).is_file() {
            return Some(true);
        }
    }
    if searched {
        Some(false)
    } else {
        None
    }
}

fn resolve_module_in(modules: &[&'static str], dirs: &[&str]) -> Result<&'static str, String> {
    for module in modules {
        match module_installed_in(module, dirs) {
            Some(true) => return Ok(module),
            Some(false) => continue,
            // Nothing to judge by: try the most specific module anyway.
            None => return Ok(modules[0]),
        }
    }
    Err(format!("KDE settings module {} is not installed.", modules[0]))
}

fn resolve_module(modules: &[&'static str]) -> Result<&'static str, String> {
    resolve_module_in(modules, KCM_DIRS)
}

fn launch_command(launcher: &Path, module: &str) -> Command {
    let mut command = Command::new(launcher);
    command.arg(module).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    command
}

#[tauri::command]
pub(crate) async fn rowyn_open_settings(target: String) -> Result<String, String> {
    let modules = modules_for(&target)?;
    tauri::async_runtime::spawn_blocking(move || {
        let module = resolve_module(&modules)?;
        let launcher = launcher_binary()?;
        let mut command = launch_command(&launcher, module);
        spawn_detached(&mut command).map_err(|_| "Could not open KDE settings module.".to_string())?;
        Ok("Settings launch requested.".into())
    })
    .await
    .map_err(|_| "Settings worker failed.".to_string())?
}

#[cfg(test)]
#[path = "rowyn_settings_tests.rs"]
mod tests;
