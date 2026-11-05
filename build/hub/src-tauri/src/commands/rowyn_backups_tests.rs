use super::*;
const FIXTURE: &str = r#"[Kup settings]
Backups enabled=false
Number of backups=2

[Plan/1]
Description=Documents
Paths included=/home/me/Documents,/home/me/Work
Paths excluded=/home/me/.cache,/home/me/a\\,b
Destination type=0
Filesystem destination path=file:///mnt/archive
Backup type=0
Last complete backup=2024,2,29,12,30,0

[Plan/2]
Paths included=/srv/data
Destination type=1
External drive destination path=/run/media/disk
Backup type=1
Last complete backup=
"#;
#[test]
fn parses_multiple_kup_plans_and_ui_contract() {
    let got = parse(FIXTURE).unwrap();
    assert_eq!(got.state, "plans");
    assert_eq!(got.plans.len(), 2);
    assert_eq!(
        got.plans[0].included,
        vec!["/home/me/Documents", "/home/me/Work"]
    );
    assert_eq!(
        got.plans[0].excluded,
        vec!["/home/me/.cache", "/home/me/a,b"]
    );
    assert_eq!(got.plans[0].kind, "versioned");
    assert!(!got.plans[0].enabled);
    assert_eq!(
        got.plans[0].last_completed.as_deref(),
        Some("2024-02-29T12:30:00")
    );
    assert_eq!(got.plans[1].kind, "mirror");
    assert_eq!(got.plans[1].destination, "/run/media/disk");
    assert_eq!(got.plans[1].name, "Backup plan 2");
    assert_eq!(got.plans[1].last_completed, None);
}
#[test]
fn dates_reject_invalid_calendar_and_accept_empty() {
    assert_eq!(parse_date("").unwrap(), None);
    assert_eq!(parse_date("2023,2,29,0,0,0").is_err(), true);
    assert_eq!(
        parse_date("2024,2,29,0,0,0").unwrap().as_deref(),
        Some("2024-02-29T00:00:00")
    );
    assert_eq!(
        parse_date("2024,2,29,23,59,59").unwrap().as_deref(),
        Some("2024-02-29T23:59:59")
    );
    assert!(parse_date("2024,2,29,24,0,0").is_err());
    assert!(parse_date("2024-02-29T12:00:00é").is_err());
    assert!(parse_date("2024-02-29T12:00:00.").is_err());
    assert!(parse_date("2024-02-29T12:00:00.1.2").is_err());
    assert_eq!(parse_date("2024-02-29T12:00:00.123456789").unwrap().as_deref(), Some("2024-02-29T12:00:00.123456789"));
    assert!(parse_date("not-a-date").is_err());
}
#[test]
fn missing_plan_number_never_reuses_prior_section() {
    assert!(parse("[Kup settings]\nNumber of backups=2\n[Plan/1]\nBackup type=0").is_err());
}
#[test]
fn absent_enabled_setting_defaults_off_and_bad_bool_fails() {
    assert!(
        !parse("[Kup settings]\nNumber of backups=1\n[Plan/1]\nBackup type=0")
            .unwrap()
            .plans[0]
            .enabled
    );
    assert!(parse("[Kup settings]\nBackups enabled=maybe").is_err());
}
#[test]
fn escaped_comma_decodes_actual_unquoted_kconfig_path() {
    assert_eq!(
        parse_list(r#"/home/a\\,b,/home/c\\\\d"#).unwrap(),
        vec!["/home/a,b", "/home/c\\d"]
    );
}
#[test]
fn default_no_plan_message_does_not_claim_empty_coverage() {
    assert!(parse("").unwrap().detail.contains("defaults may apply"));
}
#[test]
fn malformed_and_large_configs_fail_closed() {
    assert!(parse("[Plan/1]\nNumber of backups=1").is_err());
    assert!(parse(&"x".repeat(LIMIT + 1)).is_err());
    assert!(parse("[broken\nx=y").is_err());
}
#[test]
fn serialized_ini_paths_decode_both_layers_without_treating_quotes_as_syntax() {
    let config = r#"[Kup settings]
Number of backups=1
[Plan/1]
Backup type=0
Paths included=/home/a\\,b,/home/c\\\\d,/home/a"b,/home/with space
"#;
    let snapshot = parse(config).unwrap();
    assert_eq!(
        snapshot.plans[0].included,
        vec!["/home/a,b", "/home/c\\d", "/home/a\"b", "/home/with space"]
    );
}

#[test]
fn kup_omitted_defaults_and_environment_path_keys_are_not_false_absence() {
    let snapshot = parse("[Kup settings]\nNumber of backups=1\n[Plan/1]\nExternal drive destination path[$e]=$HOME/Backups").unwrap();
    assert_eq!(snapshot.plans[0].kind, "versioned");
    assert!(snapshot.plans[0].destination.contains("$HOME/Backups"));
    assert!(snapshot.plans[0].destination.contains("KDE"));
    assert!(snapshot.plans[0].default_included);
}

#[test]
fn empty_config_is_no_plans() {
    assert_eq!(parse("").unwrap().state, "none");
}

#[test]
fn plan_sections_without_global_count_are_rejected() {
    assert!(parse("[Plan/1]\nBackup type=0").is_err());
}
