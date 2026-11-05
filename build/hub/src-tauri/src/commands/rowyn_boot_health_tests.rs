use super::*;

#[test]
fn accepts_a_minimal_schema_v1_record() {
    let record = r#"{"schema_version":1,"known_good":{},"failures":{},"quarantined":{},"rollback":{},"last_boot":{}}"#;
    assert_eq!(validate(record).unwrap(), record);
}

#[test]
fn rejects_invalid_json_arrays_and_missing_schema() {
    assert!(validate("{").is_err());
    assert!(validate("[]").is_err());
    assert!(validate(r#"{"known_good":{}}"#).is_err());
    assert!(validate(r#"{"schema_version":2}"#).is_err());
    assert!(validate(r#"{"schema_version":"1"}"#).is_err());
}

#[test]
fn rejects_oversized_records() {
    let mut record = String::from(r#"{"schema_version":1,"padding":""#);
    record.push_str(&"x".repeat(LIMIT));
    record.push_str(r#""}"#);
    assert!(validate(&record).is_err());
}

#[test]
fn command_takes_no_arguments_and_reads_a_fixed_path() {
    assert_eq!(PATH, "/var/lib/rowyn/boot-health.json");
    assert!(LIMIT >= 64 * 1024, "the record must allow every state the helper writes");
}
