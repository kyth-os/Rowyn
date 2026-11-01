use super::*;
use std::fs;
use std::path::Path;

fn manifest(app_id: u32, title: &str, flags: u32) -> String {
    format!(
        "\"AppState\" {{\n\t\"appid\" \"{app_id}\"\n\t\"name\" \"{title}\"\n\t\"StateFlags\" \"{flags}\"\n\t\"installdir\" \"ignored\"\n}}\n"
    )
}

fn write_game(library: &Path, app_id: u32, title: &str, flags: u32) {
    let steamapps = library.join("steamapps");
    fs::create_dir_all(&steamapps).unwrap();
    fs::write(
        steamapps.join(format!("appmanifest_{app_id}.acf")),
        manifest(app_id, title, flags),
    )
    .unwrap();
}

#[test]
fn scans_native_and_flatpak_libraries_without_exposing_paths() {
    let temporary = tempfile::tempdir().unwrap();
    let home = temporary.path();
    let native = home.join(".local/share/Steam");
    let flatpak = home.join(".var/app/com.valvesoftware.Steam/.local/share/Steam");
    let external = home.join("mounted-games");
    fs::create_dir_all(native.join("steamapps")).unwrap();
    fs::create_dir_all(flatpak.join("steamapps")).unwrap();
    fs::create_dir_all(external.join("steamapps")).unwrap();
    fs::write(
        native.join("steamapps/libraryfolders.vdf"),
        format!(
            "\"libraryfolders\" {{\n\t\"0\" {{ \"path\" \"{}\" }}\n\t\"1\" {{ \"path\" \"{}\" }}\n}}",
            native.display(), external.display()
        ),
    )
    .unwrap();
    write_game(&native, 620, "Portal 2", 4);
    write_game(&external, 570, "Dota 2", 4);
    write_game(&flatpak, 440, "Team Fortress 2", 4);
    write_game(&native, 730, "Counter-Strike 2 (not installed)", 2);
    write_game(&native, 8230, "Game files are corrupt", 4 | 128);

    let result = scan_steam_libraries(home);
    assert!(result.available);
    assert_eq!(
        result
            .games
            .iter()
            .map(|game| game.app_id.as_str())
            .collect::<Vec<_>>(),
        vec!["440", "570", "620"]
    );
    assert_eq!(result.games[2].name, "Portal 2");
    let json = serde_json::to_string(&result).unwrap();
    assert!(!json.contains(&home.to_string_lossy().to_string()));
    assert!(!json.contains("mounted-games"));
}

#[test]
fn missing_steam_install_is_unavailable_not_an_empty_library() {
    let temporary = tempfile::tempdir().unwrap();
    let result = scan_steam_libraries(temporary.path());
    assert!(!result.available);
    assert!(result.games.is_empty());
}

#[test]
fn malformed_library_file_is_visible_as_partial_not_a_clean_empty_scan() {
    let temporary = tempfile::tempdir().unwrap();
    let steam = temporary.path().join(".local/share/Steam");
    fs::create_dir_all(steam.join("steamapps")).unwrap();
    fs::write(
        steam.join("steamapps/libraryfolders.vdf"),
        "not valid { \"path\"",
    )
    .unwrap();
    let result = scan_steam_libraries(temporary.path());
    assert!(result.available);
    assert!(result.warnings > 0);
}

#[test]
fn parses_vdf_bom_and_comments() {
    let parsed = parse_vdf(concat!(
        "\u{feff}",
        r#"// Steam library list
"libraryfolders" { "0" { "path" "/games/library" } }"#
    ))
    .unwrap();
    let libraries = object(&parsed, "libraryfolders").unwrap();
    let first = match &libraries[0].1 {
        Value::Object(fields) => fields,
        _ => panic!("expected library object"),
    };
    assert_eq!(text(first, "path"), Some("/games/library"));
}
