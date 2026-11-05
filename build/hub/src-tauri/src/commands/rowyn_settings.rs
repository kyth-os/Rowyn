//! Curated entry points for KDE system settings modules.
use rowyn_shared::system::process::{run_bounded_command, spawn_detached};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

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

// `kcmshell --list` answers in milliseconds; the bound is there so a wedged
// launcher cannot hold the command open.
const LIST_TIMEOUT: Duration = Duration::from_secs(5);
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

/// Only kcmshell understands `--list`; asking `systemsettings` would open a
/// window instead of answering.
fn supports_listing(launcher: &Path) -> bool {
    launcher
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with("kcmshell"))
}

/// Module ids the launcher advertises, when it can be asked. This is the
/// authoritative list: it covers plugin directories this module does not
/// enumerate, including user-local ones.
fn launcher_listing(launcher: &Path) -> Option<Vec<String>> {
    let mut command = Command::new(launcher);
    command
        .arg("--list")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let output = run_bounded_command(command, LIST_TIMEOUT).ok()?;
    if !output.status.success() {
        return None;
    }
    parse_module_listing(&output.stdout)
}

fn parse_module_listing(stdout: &[u8]) -> Option<Vec<String>> {
    if stdout.len() > MAX_LIST_OUTPUT {
        return None;
    }
    let text = std::str::from_utf8(stdout).ok()?;
    Some(
        text.lines()
            .filter_map(|line| line.split_whitespace().next())
            .map(str::to_owned)
            .collect(),
    )
}

/// The module to open, or `None` when every source agrees this host does not
/// ship it — the caller then opens System Settings itself instead of failing.
fn choose_module(
    modules: &[&'static str],
    listing: Option<&[String]>,
    dirs: &[&str],
) -> Option<&'static str> {
    if let Some(listed) = listing {
        if let Some(module) = modules
            .iter()
            .copied()
            .find(|module| listed.iter().any(|entry| entry == module))
        {
            return Some(module);
        }
    }
    match resolve_module_in(modules, dirs) {
        Ok(module) => Some(module),
        // Plugin directories answered and did not have it. Without a listing
        // that answer is only a guess about this host's layout, so still try.
        Err(_) if listing.is_none() => Some(modules[0]),
        Err(_) => None,
    }
}

fn launch_command(launcher: &Path, module: &str) -> Command {
    let mut command = Command::new(launcher);
    command.arg(module).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    command
}

fn settings_home_command(launcher: &Path) -> Command {
    let mut command = Command::new(launcher);
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    command
}

#[tauri::command]
pub(crate) async fn rowyn_open_settings(target: String) -> Result<String, String> {
    let modules = modules_for(&target)?;
    tauri::async_runtime::spawn_blocking(move || {
        let launcher = launcher_binary()?;
        let listing = if supports_listing(&launcher) {
            launcher_listing(&launcher)
        } else {
            None
        };
        let module = choose_module(&modules, listing.as_deref(), KCM_DIRS);
        let mut command = match module {
            Some(module) => launch_command(&launcher, module),
            // A missing panel is not a dead end: the settings app itself is
            // still the place the user can reach the same setting.
            None => settings_home_command(&launcher),
        };
        spawn_detached(&mut command).map_err(|_| "Could not open KDE settings module.".to_string())?;
        Ok(match module {
            Some(_) => "Settings launch requested.".to_string(),
            None => format!(
                "KDE settings module {} is not installed, so System Settings was opened instead.",
                modules[0]
            ),
        })
    })
    .await
    .map_err(|_| "Settings worker failed.".to_string())?
}

#[cfg(test)]
#[path = "rowyn_settings_tests.rs"]
mod tests;
