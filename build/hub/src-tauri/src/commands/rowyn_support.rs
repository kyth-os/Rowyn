//! Trusted, narrowly parsed public provenance for diagnostics reports.
use rowyn_shared::{
    diagnostics_scrub::scrub_logs,
    system::process::{run_bounded_command, strip_ansi},
};
use serde_json::Value;
use std::{
    fs::File,
    io::Read,
    sync::{Mutex, OnceLock},
    time::Duration,
};

static PUBLIC_VALUES: OnceLock<Mutex<Vec<String>>> = OnceLock::new();
const IMAGE_PREFIX: &str = "ghcr.io/mrtrickdotorg/rowyn:";
const IMAGE_REPO: &str = "ghcr.io/mrtrickdotorg/rowyn";
fn values() -> &'static Mutex<Vec<String>> {
    PUBLIC_VALUES.get_or_init(|| Mutex::new(Vec::new()))
}
fn revision(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
fn digest(s: &str) -> bool {
    s.strip_prefix("sha256:").is_some_and(|h| {
        h.len() == 64
            && h.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    })
}
fn image_ref(s: &str) -> bool {
    if let Some(tag) = s.strip_prefix(IMAGE_PREFIX) {
        !tag.is_empty()
            && tag.len() <= 128
            && tag
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    } else if let Some(d) = s.strip_prefix(&(IMAGE_REPO.to_owned() + "@")) {
        digest(d)
    } else {
        false
    }
}
fn put_unique(out: &mut Vec<String>, s: &str) {
    if !out.iter().any(|v| v == s) {
        out.push(s.to_owned());
    }
}
fn token_boundary_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '@' | ':' | '-')
}
fn parse_build_info(raw: &str) -> (Option<String>, Option<String>) {
    if raw.len() > 16_384 {
        return (None, None);
    }
    let Ok(v) = serde_json::from_str::<Value>(raw) else {
        return (None, None);
    };
    let rev = v
        .get("source_revision")
        .and_then(Value::as_str)
        .filter(|s| revision(s))
        .map(str::to_owned);
    let ver = v
        .get("image_version")
        .and_then(Value::as_str)
        .filter(|s| {
            !s.is_empty()
                && s.len() <= 80
                && s.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._+-".contains(&b))
        })
        .map(str::to_owned);
    (rev, ver)
}
fn parse_status(raw: &str) -> Vec<(String, String)> {
    if raw.len() > 65_536 {
        return Vec::new();
    }
    let Ok(v) = serde_json::from_str::<Value>(raw) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (state, label) in [("booted", "boot"), ("staged", "stage")] {
        let base = format!("/status/{state}/image");
        let reference = v
            .pointer(&format!("{base}/image/image"))
            .and_then(Value::as_str)
            .filter(|s| image_ref(s));
        let hash = v
            .pointer(&format!("{base}/imageDigest"))
            .and_then(Value::as_str)
            .filter(|s| digest(s));
        if let Some(s) = reference {
            out.push((format!("{label}ref"), s.to_owned()));
        }
        if let Some(s) = hash {
            out.push((format!("{label}digest"), s.to_owned()));
        }
    }
    out
}
/// Refresh the allowlist only from fixed trusted metadata and status sources.
pub(crate) fn refresh_public_provenance() -> Vec<(String, String, String)> {
    let mut allow = Vec::new();
    let mut rows = Vec::new();
    let build = File::open("/usr/share/rowyn/build-info.json")
        .ok()
        .and_then(|file| {
            let mut bytes = Vec::new();
            file.take(16_385).read_to_end(&mut bytes).ok()?;
            (bytes.len() <= 16_384)
                .then(|| String::from_utf8(bytes).ok())
                .flatten()
        });
    let (rev, version) = build
        .as_deref()
        .map(parse_build_info)
        .unwrap_or((None, None));
    if let Some(r) = &rev {
        put_unique(&mut allow, r);
    }
    if let Some(v) = &version {
        rows.push(("Build version".into(), "ok".into(), v.clone()));
    } else {
        rows.push((
            "Build version".into(),
            "unavailable".into(),
            "Build metadata unavailable.".into(),
        ));
    }
    if let Some(r) = rev {
        rows.push(("Build identity".into(), "ok".into(), r));
    } else {
        rows.push((
            "Build identity".into(),
            "unavailable".into(),
            "Build revision unavailable.".into(),
        ));
    }
    let mut command = std::process::Command::new("/usr/bin/sudo");
    command.args(["-n", "/usr/bin/rowyn-bootc-guard", "status-json"]);
    let status = run_bounded_command(command, Duration::from_secs(35))
        .ok()
        .filter(|o| o.stdout.len() <= 65_536)
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout).ok()
            } else {
                None
            }
        });
    let parsed = status.as_deref().map(parse_status).unwrap_or_default();
    for (key, value) in &parsed {
        put_unique(&mut allow, value);
        rows.push((key.clone(), "ok".into(), value.clone()));
    }
    if !parsed.iter().any(|(k, _)| k == "bootref") {
        rows.push((
            "Booted image".into(),
            "unavailable".into(),
            "Booted image unavailable.".into(),
        ));
    }
    *values().lock().unwrap_or_else(|e| e.into_inner()) = allow;
    rows
}
/// Redact opaque hex runs after shared scrub, while retaining only validated trusted tokens.
fn scrub_long_hex(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut run = String::new();
    let flush = |out: &mut String, run: &mut String| {
        if run.len() >= 32 {
            out.push_str("[redacted]");
        } else {
            out.push_str(run);
        }
        run.clear();
    };
    for ch in text.chars() {
        if ch.is_ascii_hexdigit() {
            run.push(ch);
        } else {
            flush(&mut out, &mut run);
            out.push(ch);
        }
    }
    flush(&mut out, &mut run);
    out
}
/// Scrub arbitrary report text, exempting only exact complete trusted tokens.
pub(crate) fn sanitize_report(text: &str) -> String {
    let allowed = values().lock().unwrap_or_else(|e| e.into_inner()).clone();
    sanitize_with_public(text, &allowed)
}

fn sanitize_with_public(text: &str, allowed: &[String]) -> String {
    let mut ordered = allowed.to_vec();
    ordered.sort_by_key(|s| std::cmp::Reverse(s.len()));
    let mut marked = text.to_owned();
    let mut restore = Vec::new();
    for (i, v) in ordered.iter().enumerate() {
        let marker = format!("ROWYNPUBLICPROVENANCE{:04}TOKEN", i);
        let mut replaced = String::with_capacity(marked.len());
        let mut cursor = 0;
        let mut found = false;
        for (start, _) in marked.match_indices(v) {
            let end = start + v.len();
            let left_ok = start == 0
                || !marked[..start]
                    .chars()
                    .next_back()
                    .is_some_and(token_boundary_char);
            let right_ok = end == marked.len()
                || !marked[end..]
                    .chars()
                    .next()
                    .is_some_and(token_boundary_char);
            if left_ok && right_ok {
                replaced.push_str(&marked[cursor..start]);
                replaced.push_str(&marker);
                cursor = end;
                found = true;
            }
        }
        if found {
            replaced.push_str(&marked[cursor..]);
            marked = replaced;
            restore.push((marker, v.clone()));
        }
    }
    let clean = scrub_long_hex(&scrub_logs(&strip_ansi(&marked)));
    let mut out: String = clean
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .take(128_000)
        .collect();
    for (marker, v) in restore {
        out = out.replace(&marker, &v);
    }
    if out.len() > 128_000 {
        let mut end = 128_000;
        while !out.is_char_boundary(end) {
            end -= 1;
        }
        out.truncate(end);
    }
    out
}
#[cfg(test)]
#[path = "rowyn_support_tests.rs"]
mod tests;
