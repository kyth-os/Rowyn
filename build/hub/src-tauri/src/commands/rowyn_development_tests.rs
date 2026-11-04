use super::*;

fn owned() -> ContainerIdentity {
    ContainerIdentity {
        id: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".into(),
        name: NAME.into(),
        image: IMAGE.into(),
        labels: [
            (LABEL.into(), LABEL_VALUE.into()),
            ("manager".into(), "distrobox".into()),
        ]
        .into_iter()
        .collect(),
        status: "running".into(),
    }
}

#[test]
fn only_fixed_development_name_is_accepted() {
    assert!(validate_name(NAME).is_ok());
    assert!(validate_name("other").is_err());
    assert!(validate_name("../ubuntu-dev").is_err());
}

#[test]
fn ownership_requires_exact_name_label_manager_and_image() {
    let valid = owned();
    assert!(ownership(&valid));
    let mut bad = valid.clone();
    bad.image = "ubuntu:latest".into();
    assert!(!ownership(&bad));
    let mut bad = valid.clone();
    bad.labels.remove(LABEL);
    assert!(!ownership(&bad));
    let mut bad = valid.clone();
    bad.labels.insert("manager".into(), "other".into());
    assert!(!ownership(&bad));
    let mut bad = valid;
    bad.name = "unrelated".into();
    assert!(!ownership(&bad));
}

#[test]
fn parser_accepts_valid_rows_and_rejects_oversize_or_malformed_data() {
    let json = format!(
        r#"[{{"Id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","Names":["/{}"],"Image":"{}","State":"running","Labels":{{"{}":"{}","manager":"distrobox"}}}}]"#,
        NAME, IMAGE, LABEL, LABEL_VALUE
    );
    let rows = parse_list(json.as_bytes()).unwrap();
    assert_eq!(rows.len(), 1);
    assert!(ownership(&rows[0]));
    assert!(parse_list(&vec![b'x'; MAX_OUTPUT + 1]).is_err());
    assert!(parse_list(b"not json").is_err());
    assert!(parse_list(br#"[{"Id":"bad;rm","Names":["rowyn-ubuntu-dev"]}]"#).is_err());
    assert!(parse_list(br#"[{"Id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","Names":[]}]"#).is_err());
    assert!(parse_list(br#"[{"Id":"0123456789abcdef","Names":["x"]}]"#).is_err());
    assert!(parse_list(br#"[{"Id":"0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF","Names":["x"]}]"#).is_err());
}

#[test]
fn create_arguments_use_fixed_identity_and_noninteractive_mode() {
    let args = create_args();
    assert!(args.windows(2).any(|w| w == ["--name", NAME]));
    assert!(args.windows(2).any(|w| w == ["--image", IMAGE]));
    assert!(args.windows(2).any(|w| w
        == [
            "--additional-flags",
            "--label io.rowyn.development=ubuntu-v1"
        ]));
    assert!(args.iter().any(|a| *a == "--yes"));
}

#[test]
fn controlled_child_lifecycle_reports_success_failure_cancellation_and_timeout() {
    use std::sync::atomic::AtomicBool;
    let flag = || Arc::new(AtomicBool::new(false));
    let mut success = Command::new("/bin/sh");
    success.args(["-c", "exit 0"]);
    assert!(child(success, flag(), Duration::from_secs(2)).is_ok());
    let mut failure = Command::new("/bin/sh");
    failure.args(["-c", "exit 9"]);
    assert!(child(failure, flag(), Duration::from_secs(2)).is_err());
    let cancelled = flag();
    let mut sleeper = Command::new("/bin/sh");
    sleeper.args(["-c", "sleep 5"]);
    let worker_flag = cancelled.clone();
    let join = thread::spawn(move || child(sleeper, worker_flag, Duration::from_secs(5)));
    thread::sleep(Duration::from_millis(100));
    cancelled.store(true, std::sync::atomic::Ordering::SeqCst);
    assert_eq!(join.join().unwrap().unwrap_err(), "Cancelled.");
    let mut sleeper = Command::new("/bin/sh");
    sleeper.args(["-c", "sleep 5"]);
    assert_eq!(
        child(sleeper, flag(), Duration::from_millis(150)).unwrap_err(),
        "Development command timed out."
    );
}

#[test]
fn job_terminal_transitions_and_unknown_job_errors() {
    let key = format!("test-{}", id());
    let flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
    jobs().lock().unwrap().0.insert(
        key.clone(),
        Entry {
            job: DevelopmentJob {
                id: key.clone(),
                status: "running".into(),
                detail: "started".into(),
            },
            cancel: Some(flag),
        },
    );
    finish(&key, Ok("verified".into()));
    let job = tauri::async_runtime::block_on(rowyn_development_job(key.clone())).unwrap();
    assert_eq!(job.status, "succeeded");
    assert_eq!(job.detail, "verified");
    assert!(tauri::async_runtime::block_on(rowyn_development_job("missing".into())).is_err());
    assert!(tauri::async_runtime::block_on(rowyn_development_cancel("missing".into())).is_err());
    jobs().lock().unwrap().0.remove(&key);
}

#[test]
fn create_manager_label_matches_distrobox_ownership_contract() {
    assert_eq!(
        owned().labels.get("manager").map(String::as_str),
        Some("distrobox")
    );
}
