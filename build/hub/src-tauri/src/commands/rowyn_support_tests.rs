use super::*;

#[test]
fn trusted_metadata_and_status_are_narrowly_parsed() {
    let rev = "0123456789abcdef0123456789abcdef01234567";
    assert_eq!(
        parse_build_info(&format!(
            r#"{{"source_revision":"{rev}","image_version":"26.10"}}"#
        )),
        (Some(rev.into()), Some("26.10".into()))
    );
    assert_eq!(
        parse_build_info(r#"{"source_revision":"not-a-revision","image_version":"../private"}"#),
        (None, None)
    );
    let status = r#"{"status":{"booted":{"image":{"image":{"image":"ghcr.io/mrtrickdotorg/rowyn:stable"},"imageDigest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}}"#;
    let parsed = parse_status(status);
    assert!(parsed.contains(&(
        "bootref".into(),
        "ghcr.io/mrtrickdotorg/rowyn:stable".into()
    )));
    assert!(parsed.contains(&("bootdigest".into(), format!("sha256:{}", "a".repeat(64)))));
    assert!(parse_status(r#"{"image":"private.example/a:b"}"#).is_empty());
    assert!(parse_status(&" ".repeat(65_537)).is_empty());
    assert_eq!(parse_build_info(&" ".repeat(16_385)), (None, None));
}
#[test]
fn sanitizer_preserves_only_values_from_trusted_refresh_allowlist() {
    let allowed = vec![
        "0123456789abcdef0123456789abcdef01234567".into(),
        "ghcr.io/mrtrickdotorg/rowyn:stable".into(),
    ];
    let text = "rev 0123456789abcdef0123456789abcdef01234567 image ghcr.io/mrtrickdotorg/rowyn:stable random aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa password=hunter2 /home/alice/x";
    let out = sanitize_with_public(text, &allowed);
    assert!(out.contains("0123456789abcdef0123456789abcdef01234567"));
    assert!(out.contains("ghcr.io/mrtrickdotorg/rowyn:stable"));
    assert!(!out.contains("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"));
    assert!(!out.contains("hunter2"));
    assert!(!out.contains("/home/alice"));
}
#[test]
fn sanitizer_enforces_a_utf8_byte_limit_after_restoring_public_tokens() {
    let output = sanitize_report(&"é".repeat(128_001));
    assert!(output.len() <= 128_000);
    assert!(!output.contains('\u{fffd}'));
}

#[test]
fn provenance_exemptions_never_apply_to_partial_tokens_or_secret_fields() {
    let rev = "0123456789abcdef0123456789abcdef01234567";
    let hash = format!("sha256:{}", "a".repeat(64));
    let reference = format!("ghcr.io/mrtrickdotorg/rowyn@{hash}");
    let allowed = vec![rev.to_owned(), hash.clone(), reference.clone()];
    let public = sanitize_with_public(&format!("é ({rev}) {hash} {reference}"), &allowed);
    for token in &allowed {
        assert!(public.contains(token));
    }
    for input in [
        format!("prefix{rev}"),
        format!("{rev}suffix"),
        format!("/{reference}/private"),
    ] {
        assert!(!sanitize_with_public(&input, &allowed).contains(rev));
        assert!(!sanitize_with_public(&input, &allowed).contains(&"a".repeat(64)));
    }
    for token in &allowed {
        assert!(!sanitize_with_public(&format!("password={token}"), &allowed).contains(token));
    }
}

#[test]
fn sanitizer_strips_controls_and_bounds_after_scrubbing() {
    let out = sanitize_report(&format!(
        "password=\u{1b}[31msecret\u{1b}[0m\r{}",
        "z".repeat(140_000)
    ));
    assert!(!out.contains('\u{1b}'));
    assert!(!out.contains('\r'));
    assert!(out.len() <= 128_000);
    assert!(!out.contains("secret"));
    assert!(sanitize_report("ordinary secret text").contains("ordinary secret text"));
}
