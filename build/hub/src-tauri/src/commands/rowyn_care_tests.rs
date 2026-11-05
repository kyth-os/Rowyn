use super::*;
use std::{fs, path::Path};
#[test]
fn flatpak_fixture_parses_rows_preserving_ref_scope_and_raw_size() {
    let rows =
        parse_flatpak("org.example.App\tExample\tsystem\t12.3 MB\norg.other.App\tOther\tuser\t\n")
            .unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].reference, "org.example.App");
    assert_eq!(rows[0].scope, "system");
    assert_eq!(rows[0].installed_size.as_deref(), Some("12.3 MB"));
    assert_eq!(rows[1].installed_size, None);
}
#[test]
fn flatpak_fixture_rejects_bad_and_oversized_output() {
    for raw in ["garbage\n", "a\tb\tc\td\te\n", &"x".repeat(LIMIT + 1)] {
        assert!(parse_flatpak(raw).is_err())
    }
}
#[test]
fn podman_fixture_requires_supported_shape_and_parses_size() {
    assert_eq!(
        parse_podman(r#"{"Images":[{"size":12},{"size":30}]}"#).unwrap(),
        (Some(2), None)
    );
    for raw in ["{}", "[]", "not json", r#"{"Images":[{}]}"#] {
        assert!(parse_podman(raw).is_err(), "{raw}")
    }
}
#[test]
fn power_sysfs_fixture_reports_readonly_battery_and_charge_limit() {
    let tmp = tempfile::tempdir().unwrap();
    let b = tmp.path().join("BAT0");
    fs::create_dir(&b).unwrap();
    for (k, v) in [
        ("type", "Battery"),
        ("present", "1"),
        ("capacity", "78"),
        ("status", "Discharging"),
        ("energy_full", "400"),
        ("energy_full_design", "500"),
        ("charge_control_end_threshold", "80"),
    ] {
        fs::write(b.join(k), v).unwrap()
    }
    let p = power_collect_at(tmp.path(), Ok("balanced".into()));
    assert_eq!(p.state, "partial");
    assert_eq!(p.batteries.len(), 1);
    assert_eq!(p.batteries[0].capacity, Some(78.0));
    assert_eq!(p.batteries[0].health_percent, Some(80.0));
    assert_eq!(p.charge_limit.state, "available");
    assert_eq!(p.charge_limit.value, Some(Value::from(80)));
    assert_eq!(p.profile.state, "available");
}
#[test]
fn power_sysfs_fixture_distinguishes_absent_battery_from_failed_directory() {
    let tmp = tempfile::tempdir().unwrap();
    let p = power_collect_at(tmp.path(), Err("missing".into()));
    assert_eq!(p.state, "partial");
    assert!(p.batteries.is_empty());
    assert_eq!(p.profile.state, "unavailable");
    let p = power_collect_at(
        Path::new("/definitely/not/a/real/sysfs/path"),
        Err("missing".into()),
    );
    assert_eq!(p.state, "unavailable");
}
#[test]
fn charge_only_health_uses_a_complete_pair_and_missing_sleep_is_unknown() {
    let tmp = tempfile::tempdir().unwrap();
    let b = tmp.path().join("BAT0");
    fs::create_dir(&b).unwrap();
    for (key, value) in [
        ("type", "Battery"),
        ("capacity", "55"),
        ("status", "Discharging"),
        ("energy_full", "1000000"),
        ("charge_full", "400"),
        ("charge_full_design", "500"),
    ] {
        fs::write(b.join(key), value).unwrap();
    }
    let p = power_collect_at(tmp.path(), Ok("balanced".into()));
    assert_eq!(p.batteries[0].health_percent, Some(80.0));
    assert_eq!(p.capabilities["state"], "unavailable");
    fs::create_dir(b.join("charge_control_end_threshold")).unwrap();
    let p = power_collect_at(tmp.path(), Ok("balanced".into()));
    assert_eq!(p.charge_limit.state, "unavailable");
}

#[test]
fn kernel_capabilities_are_observed_not_invented_and_unknown_tokens_fail_closed() {
    assert_eq!(
        sleep_capabilities(Ok("freeze mem".into()))["value"],
        serde_json::json!(["freeze", "mem"])
    );
    assert_eq!(
        sleep_capabilities(Ok("future-mode".into()))["state"],
        "unavailable"
    );
    assert_eq!(
        sleep_capabilities(Err("denied".into()))["state"],
        "unavailable"
    );
    assert_eq!(
        sleep_capabilities(Ok(String::new()))["state"],
        "unsupported"
    );
}

#[test]
fn invalid_battery_measurements_are_partial_not_trusted_values() {
    let tmp = tempfile::tempdir().unwrap();
    let b = tmp.path().join("BAT0");
    fs::create_dir(&b).unwrap();
    for (key, value) in [
        ("type", "Battery"),
        ("capacity", "150"),
        ("energy_full", "10000"),
        ("energy_full_design", "1"),
        ("charge_control_end_threshold", "110"),
    ] {
        fs::write(b.join(key), value).unwrap();
    }
    let p = power_collect_with(tmp.path(), Ok("balanced".into()), Ok("mem".into()));
    assert_eq!(p.state, "partial");
    assert_eq!(p.batteries[0].capacity, None);
    assert_eq!(p.batteries[0].health_percent, None);
    assert_eq!(p.charge_limit.state, "unavailable");
}

#[test]
fn public_contract_serializes_camel_case_and_never_exposes_device_identifiers() {
    let tmp = tempfile::tempdir().unwrap();
    let p = power_collect_at(tmp.path(), Ok("balanced".into()));
    let mut p = p;
    p.batteries.push(Battery {
        capacity: Some(1.0),
        status: None,
        health_percent: None,
    });
    let s = serde_json::to_string(&p).unwrap();
    assert!(s.contains("chargeLimit") && s.contains("healthPercent") && s.contains("capabilities"));
    assert!(!s.contains("BAT0"));
    let snap = StorageSnapshot {
        state: "partial".into(),
        volumes: vec![Volume {
            name: "Home".into(),
            free_bytes: Some(0),
            total_bytes: Some(3),
        }],
        inventory: vec![Inventory {
            category: "flatpakApps".into(),
            count: Some(0),
            estimated_bytes: None,
            status: "available".into(),
            detail: "ok".into(),
            entries: Some(vec![]),
        }],
    };
    let j = serde_json::to_string(&snap).unwrap();
    assert!(j.contains("freeBytes") && j.contains("estimatedBytes") && j.contains("flatpakApps"));
}
