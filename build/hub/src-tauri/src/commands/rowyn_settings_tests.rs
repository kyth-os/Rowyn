use super::*;

#[test]
fn target_allowlist_maps_exact_modules() {
    for (target, expected) in [
        ("backups", "kcm_kup"),
        ("bluetooth", "kcm_bluetooth"),
        ("wifi", "kcm_networkmanagement"),
        ("audio", "kcm_pulseaudio"),
        ("power", "kcm_powerdevilprofilesconfig"),
        ("displays", "kcm_kscreen"),
        ("defaults", "kcm_componentchooser"),
        ("file-associations", "kcm_filetypes"),
        ("flatpak-permissions", "kcm_app-permissions"),
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
fn list_parser_uses_only_first_whitespace_word() {
    let parsed = parse_module_list(b"kcm_kup description\n  kcm_app-permissions\tApp permissions\n\n").unwrap();
    assert_eq!(parsed, ["kcm_kup", "kcm_app-permissions"]);
}

#[test]
fn list_parser_rejects_invalid_utf8_and_oversized_output() {
    assert!(parse_module_list(&[0xff]).is_err());
    assert!(parse_module_list(&vec![b'x'; MAX_LIST_OUTPUT + 1]).is_err());
}

#[test]
fn command_fixtures_use_fixed_executable_and_exact_arguments() {
    let list = module_list_command();
    assert_eq!(list.get_program(), KCMSHELL);
    assert_eq!(list.get_args().collect::<Vec<_>>(), ["--list"]);

    for target in ["backups", "flatpak-permissions"] {
        let module = settings_module(target).unwrap();
        let launch = launch_command(module);
        assert_eq!(launch.get_program(), KCMSHELL);
        assert_eq!(launch.get_args().collect::<Vec<_>>(), [module]);
    }
}
