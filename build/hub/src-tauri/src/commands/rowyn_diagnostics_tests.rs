use super::*;

/// Opt-in smoke on an installed system/container; never installs or changes it.
#[test]
#[ignore = "explicit installed-system read-only smoke"]
fn live_read_only_snapshot_has_all_sections_and_controller_helper_result() {
    let snapshot = collect().unwrap();
    assert_eq!(snapshot.readings.len(), 9);
    assert!(snapshot.collected_at > 0);
    for reading in &snapshot.readings {
        assert!(!reading.detail.is_empty());
        assert!(["ok", "attention", "unavailable", "error"].contains(&reading.state.as_str()));
        println!(
            "{}: {} ({} characters)",
            reading.name,
            reading.state,
            reading.detail.chars().count()
        );
    }
    let findings = controllers().expect("shipped controller helper must return parseable JSON");
    assert!(!findings.is_empty());
    println!(
        "Controller helper returned {} scrubbed findings",
        findings.len()
    );
}

#[test]
fn controller_parser_projects_only_bounded_read_only_findings() {
    let raw = r#"{"findings":[{"name":"Input devices","status":"warn","detail":"none","hint":"privileged advice"}],"devices":[{"name":"private device"}]}"#;
    let parsed = parse_controller(raw).unwrap();
    assert_eq!(parsed.len(), 1);
    assert_eq!(parsed[0].state, "warn");
    let text = serde_json::to_string(&parsed).unwrap();
    assert!(!text.contains("privileged advice"));
    assert!(!text.contains("private device"));
    for bad in [
        "",
        "{}",
        r#"{"findings":null}"#,
        r#"{"findings":[{"name":"x","status":"clean","detail":"x"}]}"#,
    ] {
        assert!(parse_controller(bad).is_err(), "{bad}");
    }
}

#[test]
fn output_is_scrubbed_before_it_reaches_any_view_or_report() {
    let output = safe_text("password=hunter2 192.0.2.8 /home/alice/file\u{1b}[31m");
    for sensitive in ["hunter2", "192.0.2.8", "/home/alice", "\u{1b}"] {
        assert!(!output.contains(sensitive), "{sensitive}");
    }
}

#[test]
fn controller_warnings_are_not_reported_as_an_ok_summary() {
    let result = controller_section(Ok(vec![Reading {
        name: "uinput".into(),
        state: "warn".into(),
        detail: "missing".into(),
    }]));
    assert_eq!(result.state, "attention");
    let missing = controller_section(Err("unavailable: helper missing".into()));
    assert_eq!(missing.state, "unavailable");
}

#[test]
fn failed_services_and_permission_denials_are_classified_truthfully() {
    assert_eq!(
        failed_services_section(Ok("broken.service loaded failed failed Broken".into())).state,
        "attention"
    );
    assert_eq!(failed_services_section(Ok(String::new())).state, "ok");
    for denied in [
        "Permission denied",
        "This command requires root privileges",
        "Operation not permitted",
    ] {
        assert_eq!(
            section("Image", Err(probe_failure(denied))).state,
            "unavailable"
        );
    }
    assert_eq!(
        section("Image", Err(probe_failure("bad output"))).state,
        "error"
    );
}

#[test]
fn empty_success_is_not_an_unavailable_or_failed_service_read() {
    let check = section("Failed services", Ok(String::new()));
    assert_eq!(check.state, "ok");
    assert_eq!(check.detail, "None reported.");
    let check = section("Image", Err("unavailable: permission denied".into()));
    assert_eq!(check.state, "unavailable");
}

#[test]
fn bootc_projection_rejects_malformed_data_and_never_copies_arbitrary_fields() {
    assert!(parse_bootc("{}").is_err());
    assert!(parse_bootc("not json").is_err());
    let text = parse_bootc(r#"{"status":{"booted":{"image":{"image":{"image":"ghcr.io/kyth-os/rowyn:stable"},"imageDigest":"sha256:abcd"},"secret":"do-not-copy"}}}"#).unwrap();
    assert!(text.contains("rowyn:stable"));
    assert!(!text.contains("do-not-copy"));
}

#[test]
fn refresh_guard_rejects_overlap_and_releases_on_drop() {
    let flag = AtomicBool::new(false);
    let first = ReadGuard::take(&flag).unwrap();
    assert!(ReadGuard::take(&flag).is_err());
    drop(first);
    assert!(ReadGuard::take(&flag).is_ok());
}

#[test]
fn report_export_writes_only_an_explicit_private_file_and_never_creates_missing_directories() {
    use std::os::unix::fs::PermissionsExt;
    let temp = tempfile::tempdir().unwrap();
    assert!(save_report(&temp.path().join("missing"), "anything").is_err());
    assert!(!temp.path().join("missing").exists());
    assert!(save_report(temp.path(), &"x".repeat(REPORT_LIMIT + 1)).is_err());
    let message = save_report(temp.path(), "password=hunter2 /home/alice/report").unwrap();
    assert!(message.contains("Saved rowyn-diagnostics-"));
    let entries: Vec<_> = std::fs::read_dir(temp.path())
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    assert_eq!(entries.len(), 1);
    let text = std::fs::read_to_string(&entries[0]).unwrap();
    assert!(!text.contains("hunter2") && !text.contains("/home/alice"));
    assert_eq!(
        std::fs::metadata(&entries[0]).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert!(save_report(temp.path(), "second report").is_ok());
    assert_eq!(std::fs::read_to_string(&entries[0]).unwrap(), text);
}

#[test]
fn bounded_view_scrubs_before_truncating_a_secret() {
    let input = format!(
        "{}password={}\n",
        " ".repeat(TEXT_LIMIT - 4),
        "secret".repeat(2000)
    );
    let text = safe_text(&input);
    assert!(!text.contains("secret"));
    assert!(text.chars().count() <= TEXT_LIMIT + 20);
}
