use super::*;
use std::fs;

fn temp_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("rowyn-settings-{label}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn target_allowlist_maps_exact_modules() {
    for (target, expected) in [
        ("backups", "kcm_kup"),
        ("bluetooth", "kcm_bluetooth"),
        ("wifi", "kcm_networkmanagement"),
        ("audio", "kcm_pulseaudio"),
        ("power", "kcm_powerdevil"),
        ("displays", "kcm_kscreen"),
        ("defaults", "kcm_componentchooser"),
        ("file-associations", "kcm_filetypes"),
        ("flatpak-permissions", "kcm_app-permissions"),
        ("printers", "kcm_printer_manager"),
    ] {
        assert_eq!(settings_module(target).unwrap(), expected);
    }
}

#[test]
fn unknown_targets_are_rejected() {
    for target in ["", "bluetooth; rm -rf /", "kcm_flatpak", "wifi "] {
        assert!(settings_module(target).is_err(), "accepted {target:?}");
    }
}

#[test]
fn launchers_are_absolute_and_try_plasma6_then_plasma5() {
    assert_eq!(LAUNCHERS[0], "/usr/bin/kcmshell6");
    assert_eq!(LAUNCHERS[1], "/usr/bin/kcmshell5");
    assert_eq!(LAUNCHERS[2], "/usr/bin/systemsettings");
    for launcher in LAUNCHERS {
        assert!(launcher.starts_with('/'), "{launcher} must be absolute");
    }
}

#[test]
fn launcher_resolution_prefers_the_first_installed_binary() {
    let root = temp_dir("launchers");
    let sixth = root.join("kcmshell6");
    let fifth = root.join("kcmshell5");
    let missing = root.join("kcmshell4");
    fs::write(&fifth, b"").unwrap();

    let both = [sixth.to_str().unwrap(), fifth.to_str().unwrap()];
    assert_eq!(first_existing(&both), Some(fifth.clone()));

    fs::write(&sixth, b"").unwrap();
    assert_eq!(first_existing(&both), Some(sixth.clone()));
    assert_eq!(first_existing(&[missing.to_str().unwrap()]), None);

    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn kcm_directories_cover_both_plasma_generations() {
    for needle in [
        "qt6/plugins/plasma/kcms/systemsettings",
        "qt5/plugins/plasma/kcms/systemsettings",
        "qt5/plugins/plasma/kcms/systemsettings_qwidgets",
    ] {
        assert!(
            KCM_DIRS.iter().any(|dir| dir.contains(needle)),
            "KCM_DIRS must search {needle}"
        );
    }
    for dir in KCM_DIRS {
        assert!(dir.starts_with("/usr/lib"), "{dir} must be an absolute plugin path");
    }
}

#[test]
fn module_presence_is_answered_only_where_kcm_directories_exist() {
    let root = temp_dir("modules");
    let empty = root.join("qt6");
    let full = root.join("qt5");
    fs::create_dir_all(&empty).unwrap();
    fs::create_dir_all(&full).unwrap();
    fs::write(full.join("kcm_filetypes.so"), b"").unwrap();
    let dirs = [empty.to_str().unwrap(), full.to_str().unwrap()];

    assert_eq!(module_installed_in("kcm_filetypes", &dirs), Some(true));
    assert_eq!(module_installed_in("kcm_kup", &dirs), Some(false));
    // No KCM directory at all: unknown, so a launch is still attempted.
    assert_eq!(module_installed_in("kcm_kup", &[]), None);

    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn flatpak_permissions_falls_back_to_the_plasma5_module() {
    let root = temp_dir("flatpak");
    let dir = root.join("kcms");
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("kcm_flatpak.so"), b"").unwrap();
    let dirs = [dir.to_str().unwrap()];

    let modules = modules_for("flatpak-permissions").unwrap();
    assert_eq!(modules, ["kcm_app-permissions", "kcm_flatpak"]);
    assert_eq!(resolve_module_in(&modules, &dirs).unwrap(), "kcm_flatpak");
    // A definite miss everywhere is reported by its most specific name.
    let empty = root.join("empty");
    fs::create_dir_all(&empty).unwrap();
    assert_eq!(module_installed_in("kcm_flatpak", &[empty.to_str().unwrap()]), Some(false));
    assert!(resolve_module_in(&modules, &[empty.to_str().unwrap()]).is_err());

    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn targets_without_alternates_resolve_to_their_only_module() {
    let modules = modules_for("power").unwrap();
    assert_eq!(modules, ["kcm_powerdevil"]);
    for (target, expected) in [("backups", "kcm_kup"), ("displays", "kcm_kscreen"),
                               ("printers", "kcm_printer_manager")] {
        assert_eq!(modules_for(target).unwrap(), [expected]);
    }
}

#[test]
fn launch_command_uses_the_resolved_launcher_and_exact_module() {
    let launcher = Path::new("/usr/bin/kcmshell5");
    let command = launch_command(launcher, "kcm_kup");
    assert_eq!(command.get_program(), launcher.as_os_str());
    assert_eq!(command.get_args().collect::<Vec<_>>(), ["kcm_kup"]);
}

#[cfg(unix)]
#[test]
fn launcher_listing_reads_the_launchers_own_module_list() {
    use std::os::unix::fs::PermissionsExt;
    let root = temp_dir("listing-script");
    let launcher = root.join("kcmshell5");
    fs::write(
        &launcher,
        b"#!/bin/sh\nprintf 'The following modules are available:\\n\\nkcm_filetypes - Configure file associations\\n'\n",
    )
    .unwrap();
    fs::set_permissions(&launcher, fs::Permissions::from_mode(0o755)).unwrap();
    let listing = launcher_listing(&launcher).unwrap();
    assert!(listing.iter().any(|entry| entry == "kcm_filetypes"), "{listing:?}");
    assert!(supports_listing(&launcher));
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn only_kcmshell_launchers_are_asked_to_list() {
    assert!(supports_listing(Path::new("/usr/bin/kcmshell6")));
    assert!(supports_listing(Path::new("/usr/bin/kcmshell5")));
    assert!(!supports_listing(Path::new("/usr/bin/systemsettings")));
}

#[test]
fn a_listed_module_is_used_when_the_plugin_directories_lack_it() {
    let root = temp_dir("listed");
    let dir = root.join("kcms");
    fs::create_dir_all(&dir).unwrap();
    let dirs = [dir.to_str().unwrap()];
    let modules = modules_for("file-associations").unwrap();
    let listing = vec!["kcm_filetypes".to_string()];
    assert_eq!(choose_module(&modules, Some(&listing), &dirs), Some("kcm_filetypes"));
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn a_listed_absence_falls_back_to_the_settings_app() {
    let root = temp_dir("unlisted");
    let dir = root.join("kcms");
    fs::create_dir_all(&dir).unwrap();
    let dirs = [dir.to_str().unwrap()];
    let modules = modules_for("file-associations").unwrap();
    let listing = vec!["kcm_kup".to_string()];
    assert_eq!(choose_module(&modules, Some(&listing), &dirs), None);
    fs::remove_dir_all(&root).unwrap();
}

#[test]
fn an_unlistable_launcher_still_tries_the_module() {
    let modules = modules_for("displays").unwrap();
    assert_eq!(choose_module(&modules, None, &[]), Some("kcm_kscreen"));
}

#[test]
fn settings_home_launch_uses_the_launcher_without_a_module() {
    let launcher = Path::new("/usr/bin/systemsettings");
    let command = settings_home_command(launcher);
    assert_eq!(command.get_program(), launcher.as_os_str());
    assert_eq!(command.get_args().count(), 0);
}
