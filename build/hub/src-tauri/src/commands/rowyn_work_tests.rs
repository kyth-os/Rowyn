use super::*;

#[test]
fn idle_printing_and_disabled_queues_keep_their_reported_state() {
    let text = "printer Office_Printer is idle.  enabled since Thu 05 Nov 2026\n\
                printer Label_Writer now printing Label_Writer-12.  enabled since Thu 05 Nov 2026\n\
                printer Old_Office disabled since Thu 05 Nov 2026 -\n\
                \tReason: paused\n";
    let (queues, _) = parse_lpstat(text);
    assert_eq!(queues.len(), 3);
    assert_eq!(queues[0].state, "idle");
    assert_eq!(queues[1].state, "printing");
    assert_eq!(queues[2].state, "disabled");
}

#[test]
fn a_disabled_queue_reports_disabled_even_with_a_held_job() {
    let (queues, _) = parse_lpstat(
        "printer Office is disabled since today - \n\tnow printing Office-7.  enabled since yesterday\n",
    );
    assert_eq!(queues[0].state, "disabled");
}

#[test]
fn the_default_destination_marks_exactly_one_queue() {
    let text = "printer A is idle.\nprinter B is idle.\nsystem default destination: B\n";
    let (queues, default) = parse_lpstat(text);
    assert_eq!(default.as_deref(), Some("B"));
    assert!(!queues[0].is_default);
    assert!(queues[1].is_default);
}

#[test]
fn no_default_destination_stays_none() {
    let (queues, default) = parse_lpstat("printer A is idle.\nno system default destination\n");
    assert_eq!(default, None);
    assert!(queues.iter().all(|queue| !queue.is_default));
}

#[test]
fn unrelated_output_never_becomes_a_queue() {
    let (queues, default) = parse_lpstat(
        "scheduler is running\nprinter \nprinter A is idle.\nprinter A is idle.\n",
    );
    assert_eq!(default, None);
    assert_eq!(queues.len(), 1);
    assert_eq!(queues[0].name, "A");
}

#[test]
fn an_unknown_state_is_reported_as_unknown_not_ready() {
    let (queues, _) = parse_lpstat("printer A is warming up.\n");
    assert_eq!(queues[0].state, "unknown");
}

#[test]
fn queue_names_cannot_be_read_as_flags_or_paths() {
    for name in ["", "-d", "--help", "a b", "../x", "a;rm -rf", "name/with/slash", &"a".repeat(65)] {
        assert!(queue_argument(name).is_err(), "{name:?} must be rejected");
    }
    for name in ["Office_Printer", "HP.LaserJet-200", "queue@server", "A"] {
        assert_eq!(queue_argument(name).unwrap(), name);
    }
}

#[test]
fn printer_addresses_must_be_ipp_and_free_of_whitespace() {
    assert_eq!(
        printer_uri("ipp://Office%20Printer._ipp._tcp.local/").unwrap(),
        "ipp://Office%20Printer._ipp._tcp.local/"
    );
    assert_eq!(
        printer_uri("ipps://192.168.1.10:631/ipp/print").unwrap(),
        "ipps://192.168.1.10:631/ipp/print"
    );
    let long = format!("ipp://{}", "a".repeat(MAX_URI));
    for bad in [
        "",
        "http://printer/ipp/print",
        "ipp://bad host/ipp/print",
        "ipp://host/ipp/print\n",
        "--domain",
        long.as_str(),
    ] {
        assert!(printer_uri(bad).is_err(), "{bad:?} must be rejected");
    }
}

#[test]
fn lpadmin_uses_the_installed_ubuntu_executable_path() {
    assert_eq!(LPADMIN, "/usr/sbin/lpadmin");
}

#[test]
fn add_queue_arguments_are_fixed_and_driverless() {
    assert_eq!(
        add_queue_args("Office_Printer", "ipp://p.local/ipp/print"),
        vec!["-p", "Office_Printer", "-E", "-v", "ipp://p.local/ipp/print", "-m", "everywhere"]
    );
}

#[test]
fn a_refused_addition_names_the_authorization_problem() {
    let message = add_failure("Office", b"lpadmin: Unauthorized\n");
    assert!(message.contains("Unauthorized"), "{message}");
    assert!(message.contains("Print settings"), "{message}");
    let bare = add_failure("Office", b"");
    assert!(bare.contains("did not create the queue Office"), "{bare}");
    assert!(!bare.contains("administrator"), "no authorization is invented: {bare}");
}

#[test]
fn the_test_page_is_the_image_file_and_the_bridge_elevates_nothing() {
    assert_eq!(TEST_PAGE, "/usr/share/rowyn/print-test-page.txt");
    let source = include_str!("rowyn_work.rs");
    for forbidden in ["sudo", "pkexec", "sh -c", "Command::new(program)", "print-test-page\")"] {
        assert!(!source.contains(forbidden), "bridge must not contain {forbidden:?}");
    }
}
