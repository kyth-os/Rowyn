//! Read-only parser for Kup's fixed per-user KConfig file.
use serde::Serialize;
use std::os::unix::fs::OpenOptionsExt;
use std::{
    collections::BTreeMap, fs::OpenOptions, io::Read, os::unix::fs::MetadataExt, path::PathBuf,
};

const LIMIT: usize = 256 * 1024;
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BackupPlan {
    pub name: String,
    pub destination: String,
    pub included: Vec<String>,
    pub excluded: Vec<String>,
    pub last_completed: Option<String>,
    pub default_included: bool,
    pub kind: String,
    pub enabled: bool,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BackupSnapshot {
    pub state: String,
    pub detail: String,
    pub plans: Vec<BackupPlan>,
}

fn decode(raw: &str) -> Result<String, String> {
    let s = raw.trim();
    if s.starts_with('"') {
        if !s.ends_with('"') || s.len() < 2 {
            return Err("invalid quoted KConfig value".into());
        }
        let mut out = String::new();
        let mut it = s[1..s.len() - 1].chars();
        while let Some(c) = it.next() {
            if c == '\\' {
                match it.next() {
                    Some('\\') => out.push('\\'),
                    Some('"') => out.push('"'),
                    Some('n') => out.push('\n'),
                    Some('t') => out.push('\t'),
                    Some(',') => out.push(','),
                    _ => return Err("invalid KConfig escape".into()),
                }
            } else {
                out.push(c);
            }
        }
        Ok(out)
    } else {
        let mut out = String::new();
        let mut it = s.chars();
        while let Some(c) = it.next() {
            if c == '\\' {
                match it.next() {
                    Some('s') => out.push(' '),
                    Some('t') => out.push('\t'),
                    Some('n') => out.push('\n'),
                    Some('r') => out.push('\r'),
                    Some('\\') => out.push('\\'),
                    Some(',') => out.push(','),
                    _ => return Err("invalid KConfig escape".into()),
                }
            } else {
                out.push(c);
            }
        }
        if out.chars().any(|c| c.is_control()) {
            return Err("control character in KConfig value".into());
        }
        Ok(out)
    }
}
fn parse_list(raw: &str) -> Result<Vec<String>, String> {
    // The INI backend decodes once before KConfigGroup::deserializeList.
    // Quotes are literal characters, not a second grouping syntax.
    let body = decode(raw)?;
    if body.is_empty() {
        return Ok(vec![]);
    }
    if body == "\\0" {
        return Ok(vec![String::new()]);
    }
    let mut out = Vec::new();
    let mut item = String::new();
    let mut escape = false;
    for c in body.chars() {
        if escape {
            item.push(c);
            escape = false;
        } else if c == '\\' {
            escape = true;
        } else if c == ',' {
            out.push(std::mem::take(&mut item));
        } else {
            item.push(c);
        }
    }
    if escape {
        return Err("malformed KConfig list".into());
    }
    out.push(item);
    Ok(out)
}
fn parse(raw: &str) -> Result<BackupSnapshot, String> {
    if raw.len() > LIMIT {
        return Err("Kup configuration exceeds size limit".into());
    }
    let mut sections: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    let mut section = String::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if line.starts_with('[') {
            if !line.ends_with(']') {
                return Err("malformed KConfig section".into());
            }
            section = line[1..line.len() - 1].trim().to_string();
            if section.is_empty() {
                return Err("empty KConfig section".into());
            }
            sections.entry(section.clone()).or_default();
            continue;
        }
        let (key, value) = line.split_once('=').ok_or("malformed KConfig entry")?;
        let key = key.trim();
        if key.is_empty() {
            return Err("empty KConfig key".into());
        }
        if sections
            .entry(section.clone())
            .or_default()
            .insert(key.to_string(), value.trim().to_string())
            .is_some()
        {
            return Err("duplicate KConfig key".into());
        }
    }
    let global = sections.get("Kup settings");
    let enabled = match global.and_then(|g| g.get("Backups enabled")) {
        None => false,
        Some(v) if v.eq_ignore_ascii_case("true") => true,
        Some(v) if v.eq_ignore_ascii_case("false") => false,
        Some(_) => return Err("invalid Kup enabled setting".into()),
    };
    let plan_count = match global.and_then(|g| g.get("Number of backups")) {
        Some(v) => v.parse::<usize>().map_err(|_| "invalid Kup plan count")?,
        None => 0,
    };
    if plan_count > 128 {
        return Err("Kup plan count exceeds limit".into());
    }
    if plan_count == 0 {
        if sections.keys().any(|name| name.starts_with("Plan/")) {
            return Err("Kup plan sections exist without a declared plan count".into());
        }
        return Ok(BackupSnapshot { state:"none".into(), detail:"Kup is installed, but no backup plans are configured. Kup defaults may apply; open settings to review included files.".into(), plans:vec![] });
    }
    let declared_sections: BTreeMap<_, _> = sections
        .keys()
        .filter_map(|name| {
            name.strip_prefix("Plan/")
                .and_then(|n| n.parse::<usize>().ok())
                .map(|n| (n, ()))
        })
        .collect();
    if declared_sections.len()
        != sections
            .keys()
            .filter(|name| name.starts_with("Plan/"))
            .count()
        || declared_sections.len() != plan_count
        || (1..=plan_count).any(|i| !declared_sections.contains_key(&i))
    {
        return Err("Kup plan sections do not match declared plan count".into());
    }
    let mut plans = Vec::new();
    for i in 1..=plan_count {
        let group = sections
            .get(&format!("Plan/{i}"))
            .ok_or("Kup plan section is missing")?;
        let name = group
            .get("Description")
            .map(|v| decode(v))
            .transpose()?
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("Backup plan {i}"));
        let excluded = group
            .get("Paths excluded")
            .map(|v| parse_list(v))
            .transpose()?
            .unwrap_or_default();
        let included = group
            .get("Paths included")
            .map(|v| parse_list(v))
            .transpose()?
            .unwrap_or_default();
        let dest_type = group
            .get("Destination type")
            .map(|v| v.parse::<i32>())
            .transpose()
            .map_err(|_| "invalid Kup destination type")?
            .unwrap_or(1);
        let dest_key = if dest_type == 0 {
            "Filesystem destination path"
        } else if dest_type == 1 {
            "External drive destination path"
        } else {
            return Err("unsupported Kup destination type".into());
        };
        let expanded_key = format!("{dest_key}[$e]");
        let destination = if let Some(value) = group.get(&expanded_key) {
            format!("{} (KDE-expanded path; review in settings)", decode(value)?)
        } else {
            group
                .get(dest_key)
                .map(|v| decode(v))
                .transpose()?
                .unwrap_or_else(|| "Kup default destination; review in settings".into())
        };
        let last = group
            .get("Last complete backup")
            .map(|v| parse_date(v))
            .transpose()?
            .flatten();
        let kind = match group
            .get("Backup type")
            .map(|v| v.parse::<i32>())
            .transpose()
            .map_err(|_| "invalid Kup backup type")?
            .unwrap_or(0)
        {
            0 => "versioned",
            1 => "mirror",
            _ => return Err("unsupported Kup backup type".into()),
        };
        plans.push(BackupPlan {
            name,
            destination,
            included,
            excluded,
            last_completed: last,
            default_included: group.get("Paths included").is_none(),
            kind: kind.into(),
            enabled,
        });
    }
    Ok(BackupSnapshot {
        state: "plans".into(),
        detail: format!("{} Kup backup plan(s) configured.", plans.len()),
        plans,
    })
}
fn parse_date(v: &str) -> Result<Option<String>, String> {
    let s = decode(v)?;
    if s.is_empty() || s == "0" || s == "()" {
        return Ok(None);
    }
    if s.is_empty() || s == "0" || s == "()" {
        return Ok(None);
    }
    let tuple = s
        .strip_prefix('(')
        .and_then(|v| v.strip_suffix(')'))
        .unwrap_or(&s);
    if tuple.contains(',') {
        let nums: Result<Vec<i32>, _> = tuple.split(',').map(|x| x.trim().parse()).collect();
        let n = nums.map_err(|_| "invalid Kup date")?;
        if n.len() != 6 {
            return Err("invalid Kup date".into());
        }
        let (y, m, d) = (n[0], n[1], n[2]);
        if y < 1
            || !(1..=12).contains(&m)
            || !(0..=23).contains(&n[3])
            || n[4..].iter().any(|x| !(0..=59).contains(x))
        {
            return Err("invalid Kup date".into());
        }
        let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
        let days = [
            31,
            if leap { 29 } else { 28 },
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ];
        if d < 1 || d > days[(m - 1) as usize] {
            return Err("invalid Kup date".into());
        }
        return Ok(Some(format!(
            "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}",
            n[3], n[4], n[5]
        )));
    }
    let b = s.as_bytes();
    if b.len() < 10
        || b[4] != b'-'
        || b[7] != b'-'
        || !b[..10].iter().enumerate().all(|(i, c)| {
            if i == 4 || i == 7 {
                *c == b'-'
            } else {
                c.is_ascii_digit()
            }
        })
    {
        return Err("invalid Kup date".into());
    }
    let year = i32::from(s.as_bytes()[0] - b'0') * 1000
        + i32::from(s.as_bytes()[1] - b'0') * 100
        + i32::from(s.as_bytes()[2] - b'0') * 10
        + i32::from(s.as_bytes()[3] - b'0');
    let month = u32::from(s.as_bytes()[5] - b'0') * 10 + u32::from(s.as_bytes()[6] - b'0');
    let day = u32::from(s.as_bytes()[8] - b'0') * 10 + u32::from(s.as_bytes()[9] - b'0');
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if !(1..=12).contains(&month) || day < 1 || day > days[(month - 1) as usize] {
        return Err("invalid Kup date".into());
    }
    if b.len() == 10 {
        return Ok(Some(s));
    }
    if b.len() < 19
        || (b[10] != b'T' && b[10] != b' ')
        || b[13] != b':'
        || b[16] != b':'
        || !b[11..19].iter().enumerate().all(|(i, c)| {
            if i == 2 || i == 5 {
                *c == b':'
            } else {
                c.is_ascii_digit()
            }
        })
    {
        return Err("invalid Kup date".into());
    }
    if b.len() > 19 {
        let fraction = &b[20..];
        if b[19] != b'.' || fraction.is_empty() || fraction.len() > 9 || !fraction.iter().all(u8::is_ascii_digit) {
            return Err("invalid Kup date".into());
        }
    }
    let h = (b[11] - b'0') * 10 + (b[12] - b'0');
    let m = (b[14] - b'0') * 10 + (b[15] - b'0');
    let sec = (b[17] - b'0') * 10 + (b[18] - b'0');
    if h > 23 || m > 59 || sec > 59 {
        return Err("invalid Kup date".into());
    }
    Ok(Some(format!("{}T{}", &s[..10], &s[11..])))
}
fn config_path() -> Result<PathBuf, String> {
    let base = match std::env::var_os("XDG_CONFIG_HOME") {
        Some(v) => {
            let p = PathBuf::from(v);
            if !p.is_absolute() {
                return Err("XDG_CONFIG_HOME must be absolute".into());
            }
            p
        }
        None => std::env::var_os("HOME")
            .map(|h| PathBuf::from(h).join(".config"))
            .ok_or("user config directory unavailable")?,
    };
    Ok(base.join("kuprc"))
}
pub(crate) fn collect() -> Result<BackupSnapshot, String> {
    let path = config_path()?;
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(&path)
    {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(BackupSnapshot {
                state: "none".into(),
                detail: "Kup is not configured.".into(),
                plans: vec![],
            })
        }
        Err(_) => return Err("Kup configuration is unavailable.".into()),
    };
    let meta = file
        .metadata()
        .map_err(|_| "Kup configuration is unavailable.")?;
    if !meta.is_file() || meta.len() as usize > LIMIT || meta.uid() != unsafe { libc::getuid() } {
        return Err("Kup configuration is unavailable or unsafe.".into());
    }
    let mut text = String::new();
    file.take((LIMIT + 1) as u64)
        .read_to_string(&mut text)
        .map_err(|_| "Kup configuration is unreadable.")?;
    parse(&text)
}
#[tauri::command]
pub(crate) async fn rowyn_backups_snapshot() -> Result<BackupSnapshot, String> {
    tauri::async_runtime::spawn_blocking(collect)
        .await
        .map_err(|_| "Kup status worker failed".to_string())?
}
#[cfg(test)]
#[path = "rowyn_backups_tests.rs"]
mod tests;
