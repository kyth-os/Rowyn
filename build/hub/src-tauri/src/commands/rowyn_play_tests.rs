use super::*;
#[test]
fn only_four_curated_launcher_ids_are_accepted() {
    for id in [
        "com.valvesoftware.Steam",
        "com.heroicgameslauncher.hgl",
        "net.lutris.Lutris",
        "com.usebottles.bottles",
    ] {
        assert_eq!(launcher_id(id).unwrap(), id);
    }
    for id in [
        "",
        "org.example.App",
        "--system",
        "com.valvesoftware.Steam;id",
        "../Steam",
    ] {
        assert!(launcher_id(id).is_err(), "{id}");
    }
}
#[test]
fn installed_scope_is_explicit_and_missing_scope_is_not_false_success() {
    assert_eq!(
        installed_scope(
            "com.valvesoftware.Steam",
            "com.valvesoftware.Steam\n",
            "",
            &[]
        )
        .unwrap(),
        "--user"
    );
    assert_eq!(
        installed_scope(
            "com.usebottles.bottles",
            "",
            "com.usebottles.bottles\n",
            &[]
        )
        .unwrap(),
        "--system"
    );
    assert!(
        installed_scope("com.usebottles.bottles", "", "", &["system"])
            .unwrap_err()
            .contains("unavailable")
    );
    assert!(installed_scope("com.usebottles.bottles", "", "", &[])
        .unwrap_err()
        .contains("not installed"));
    assert!(installed_scope(
        "com.valvesoftware.Steam",
        "com.valvesoftware.SteamBeta\n",
        "",
        &[]
    )
    .is_err());
}
