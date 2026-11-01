//! Read-only Steam appmanifest scanner. It reads only Steam's own library
//! metadata and never reports install paths, launches games, or rates titles.
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};

const MAX_VDF_BYTES: u64 = 256 * 1024;
const MAX_MANIFEST_BYTES: u64 = 32 * 1024;
const MAX_LIBRARIES: usize = 32;
const MAX_MANIFESTS: usize = 2048;
const MAX_DIRECTORY_ENTRIES: usize = 8192;
const MAX_TOKENS: usize = 50_000;

#[derive(Debug, Serialize)]
pub(crate) struct SteamGame {
    app_id: String,
    name: String,
}

#[derive(Debug, Serialize)]
pub(crate) struct SteamScan {
    available: bool,
    warnings: usize,
    games: Vec<SteamGame>,
}

#[derive(Debug)]
enum Value {
    Text(String),
    Object(Vec<(String, Value)>),
}

#[derive(Debug)]
enum Token {
    Text(String),
    Open,
    Close,
}

fn tokenize_vdf(input: &str) -> Result<Vec<Token>, String> {
    let bytes = input.as_bytes();
    let mut tokens = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            byte if byte.is_ascii_whitespace() => index += 1,
            b'{' => {
                tokens.push(Token::Open);
                index += 1;
            }
            b'}' => {
                tokens.push(Token::Close);
                index += 1;
            }
            b'/' if bytes.get(index + 1) == Some(&b'/') => {
                while index < bytes.len() && bytes[index] != b'\n' {
                    index += 1;
                }
            }
            b'"' => {
                index += 1;
                let mut value = Vec::new();
                let mut closed = false;
                while index < bytes.len() {
                    match bytes[index] {
                        b'"' => {
                            index += 1;
                            closed = true;
                            break;
                        }
                        b'\\' if index + 1 < bytes.len() => {
                            index += 1;
                            value.push(bytes[index]);
                            index += 1;
                        }
                        byte => {
                            value.push(byte);
                            index += 1;
                        }
                    }
                }
                if !closed {
                    return Err("unterminated quoted value".into());
                }
                tokens.push(Token::Text(String::from_utf8_lossy(&value).into_owned()));
            }
            _ => {
                let start = index;
                while index < bytes.len()
                    && !bytes[index].is_ascii_whitespace()
                    && !matches!(bytes[index], b'{' | b'}')
                {
                    index += 1;
                }
                if start == index {
                    return Err("invalid token".into());
                }
                tokens.push(Token::Text(
                    String::from_utf8_lossy(&bytes[start..index]).into_owned(),
                ));
            }
        }
        if tokens.len() > MAX_TOKENS {
            return Err("VDF has too many entries".into());
        }
    }
    Ok(tokens)
}

fn parse_pairs(
    tokens: &[Token],
    index: &mut usize,
    depth: usize,
    nested: bool,
) -> Result<Vec<(String, Value)>, String> {
    if depth > 16 {
        return Err("VDF nesting is too deep".into());
    }
    let mut pairs = Vec::new();
    loop {
        match tokens.get(*index) {
            None if !nested => return Ok(pairs),
            None => return Err("unclosed VDF object".into()),
            Some(Token::Close) if nested => {
                *index += 1;
                return Ok(pairs);
            }
            Some(Token::Close) => return Err("unexpected closing brace".into()),
            Some(Token::Open) => return Err("expected a VDF key".into()),
            Some(Token::Text(key)) => {
                let key = key.clone();
                *index += 1;
                let value = match tokens.get(*index) {
                    Some(Token::Text(text)) => {
                        *index += 1;
                        Value::Text(text.clone())
                    }
                    Some(Token::Open) => {
                        *index += 1;
                        Value::Object(parse_pairs(tokens, index, depth + 1, true)?)
                    }
                    _ => return Err("missing VDF value".into()),
                };
                pairs.push((key, value));
            }
        }
    }
}

fn parse_vdf(input: &str) -> Result<Vec<(String, Value)>, String> {
    let input = input.strip_prefix('\u{feff}').unwrap_or(input);
    let tokens = tokenize_vdf(input)?;
    let mut index = 0;
    parse_pairs(&tokens, &mut index, 0, false)
}

fn text<'a>(object: &'a [(String, Value)], key: &str) -> Option<&'a str> {
    object.iter().find_map(|(name, value)| {
        if name.eq_ignore_ascii_case(key) {
            if let Value::Text(value) = value {
                Some(value.as_str())
            } else {
                None
            }
        } else {
            None
        }
    })
}

fn object<'a>(object: &'a [(String, Value)], key: &str) -> Option<&'a [(String, Value)]> {
    object.iter().find_map(|(name, value)| {
        if name.eq_ignore_ascii_case(key) {
            if let Value::Object(value) = value {
                Some(value.as_slice())
            } else {
                None
            }
        } else {
            None
        }
    })
}

fn valid_library_path(path: &str) -> bool {
    !path.is_empty()
        && path.len() <= 4096
        && !path.chars().any(char::is_control)
        && Path::new(path).is_absolute()
}

fn library_paths(root: &Path, warnings: &mut usize) -> Vec<PathBuf> {
    let mut paths = vec![root.to_path_buf()];
    let file = root.join("steamapps/libraryfolders.vdf");
    let metadata = match fs::symlink_metadata(&file) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        Ok(_) => {
            *warnings += 1;
            return paths;
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return paths,
        Err(_) => {
            *warnings += 1;
            return paths;
        }
    };
    if metadata.len() > MAX_VDF_BYTES {
        *warnings += 1;
        return paths;
    }
    let mut source = String::new();
    if File::open(&file)
        .and_then(|file| file.take(MAX_VDF_BYTES + 1).read_to_string(&mut source))
        .is_err()
        || source.len() as u64 > MAX_VDF_BYTES
    {
        *warnings += 1;
        return paths;
    }
    let parsed = match parse_vdf(&source) {
        Ok(parsed) => parsed,
        Err(_) => {
            *warnings += 1;
            return paths;
        }
    };
    let Some(libraries) = object(&parsed, "libraryfolders") else {
        *warnings += 1;
        return paths;
    };
    for (_, entry) in libraries {
        let Value::Object(fields) = entry else {
            continue;
        };
        let Some(path) = text(fields, "path") else {
            continue;
        };
        if !valid_library_path(path) {
            *warnings += 1;
            continue;
        }
        if paths.len() >= MAX_LIBRARIES {
            *warnings += 1;
            break;
        }
        let path = PathBuf::from(path);
        if path.is_dir() {
            paths.push(path);
        } else {
            *warnings += 1;
        }
    }
    paths
}

fn parse_manifest(path: &Path, expected_id: &str, warnings: &mut usize) -> Option<SteamGame> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => metadata,
        _ => {
            *warnings += 1;
            return None;
        }
    };
    if metadata.len() > MAX_MANIFEST_BYTES {
        *warnings += 1;
        return None;
    }
    let mut source = String::new();
    if File::open(path)
        .and_then(|file| {
            file.take(MAX_MANIFEST_BYTES + 1)
                .read_to_string(&mut source)
        })
        .is_err()
        || source.len() as u64 > MAX_MANIFEST_BYTES
    {
        *warnings += 1;
        return None;
    }
    let parsed = match parse_vdf(&source) {
        Ok(parsed) => parsed,
        Err(_) => {
            *warnings += 1;
            return None;
        }
    };
    let Some(state) = object(&parsed, "AppState") else {
        *warnings += 1;
        return None;
    };
    let (Some(app_id), Some(name), Some(flags)) = (
        text(state, "appid"),
        text(state, "name"),
        text(state, "StateFlags"),
    ) else {
        *warnings += 1;
        return None;
    };
    if app_id != expected_id || app_id.parse::<u32>().ok().filter(|id| *id > 0).is_none() {
        *warnings += 1;
        return None;
    }
    // Steam's FullyInstalled bit is 4. Omit queued, partial and other
    // manifests rather than presenting them as installed games.
    let flags = match flags.parse::<u32>() {
        Ok(flags) => flags,
        Err(_) => {
            *warnings += 1;
            return None;
        }
    };
    if flags & 4 == 0 || flags & (32 | 128) != 0 {
        return None;
    }
    let name = name.trim();
    if name.is_empty() || name.len() > 160 || name.chars().any(char::is_control) {
        *warnings += 1;
        return None;
    }
    Some(SteamGame {
        app_id: app_id.to_string(),
        name: name.to_string(),
    })
}

fn scan_steam_libraries(home: &Path) -> SteamScan {
    let mut candidates = vec![
        home.join(".local/share/Steam"),
        home.join(".steam/steam"),
        home.join(".steam/root"),
        home.join(".var/app/com.valvesoftware.Steam/.local/share/Steam"),
        home.join(".var/app/com.valvesoftware.Steam/data/Steam"),
    ];
    if let Some(data_home) = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from) {
        if data_home.is_absolute() {
            candidates.push(data_home.join("Steam"));
        }
    }
    let mut roots = Vec::new();
    for candidate in candidates {
        if !candidate.is_dir() {
            continue;
        }
        let canonical = candidate.canonicalize().unwrap_or(candidate);
        if !roots.contains(&canonical) {
            roots.push(canonical);
        }
    }
    if roots.is_empty() {
        return SteamScan {
            available: false,
            warnings: 0,
            games: Vec::new(),
        };
    }
    let mut warnings = 0;
    let mut libraries = Vec::new();
    for root in &roots {
        for library in library_paths(root, &mut warnings) {
            let canonical = library.canonicalize().unwrap_or(library);
            if libraries.len() < MAX_LIBRARIES && !libraries.contains(&canonical) {
                libraries.push(canonical);
            } else if libraries.len() >= MAX_LIBRARIES {
                warnings += 1;
            }
        }
    }
    let mut games = BTreeMap::new();
    let mut examined = 0;
    for library in libraries {
        let directory = library.join("steamapps");
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => {
                warnings += 1;
                continue;
            }
        };
        let mut directory_entries = 0;
        for entry in entries {
            directory_entries += 1;
            if directory_entries > MAX_DIRECTORY_ENTRIES {
                warnings += 1;
                break;
            }
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => {
                    warnings += 1;
                    continue;
                }
            };
            let filename = entry.file_name();
            let filename = filename.to_string_lossy();
            let Some(app_id) = filename
                .strip_prefix("appmanifest_")
                .and_then(|name| name.strip_suffix(".acf"))
            else {
                continue;
            };
            if app_id.is_empty() || !app_id.bytes().all(|byte| byte.is_ascii_digit()) {
                continue;
            }
            examined += 1;
            if examined > MAX_MANIFESTS {
                warnings += 1;
                break;
            }
            if let Some(game) = parse_manifest(&entry.path(), app_id, &mut warnings) {
                games.entry(game.app_id.clone()).or_insert(game);
            }
        }
        if examined >= MAX_MANIFESTS {
            break;
        }
    }
    SteamScan {
        available: true,
        warnings,
        games: games.into_values().collect(),
    }
}

#[tauri::command]
pub(crate) async fn rowyn_steam_games_scan() -> Result<SteamScan, String> {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "Could not locate the current user's home directory.".to_string())?;
    tauri::async_runtime::spawn_blocking(move || scan_steam_libraries(&home))
        .await
        .map_err(|error| format!("Steam library scan did not complete: {error}"))
}

#[cfg(test)]
#[path = "rowyn_steam_tests.rs"]
mod tests;
