"""TDD contracts for the pinned bootc composefs signature-policy patch."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "shared/bootc-sigpolicy-patch.py"
PINNED = ROOT / "build/tests/fixtures/bootc-1.17.3"


def load_patch_module():
    spec = importlib.util.spec_from_file_location("bootc_sigpolicy_patch", PATCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_fixture(destination: Path) -> Path:
    source = destination / "bootc"
    for relative in (
        "crates/lib/Cargo.toml",
        "crates/lib/src/bootc_composefs/repo.rs",
        "crates/lib/src/bootc_composefs/state.rs",
        "crates/lib/src/bootc_composefs/update.rs",
        "crates/lib/src/install.rs",
    ):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PINNED / relative).read_bytes())
    return source


class BootcSigpolicyPatchTests(unittest.TestCase):
    def test_patch_requires_bootc_1_17_3(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-policy-version-") as temp:
            source = copy_fixture(Path(temp))
            cargo = source / "crates/lib/Cargo.toml"
            cargo.write_text(cargo.read_text(encoding="utf-8").replace('name = "bootc-lib"\nrepository = "https://github.com/bootc-dev/bootc"\n# The intention is we\'ll follow semver here, even though this\n# project isn\'t actually published as a crate.\nversion = "1.17.3"', 'name = "bootc-lib"\nrepository = "https://github.com/bootc-dev/bootc"\n# The intention is we\'ll follow semver here, even though this\n# project isn\'t actually published as a crate.\nversion = "9.9.9"', 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "1.17.3"):
                module.apply(source)

    def test_patch_persists_signature_policy_in_composefs_origin(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-policy-origin-") as temp:
            source = copy_fixture(Path(temp))
            module.apply(source)
            state = (source / "crates/lib/src/bootc_composefs/state.rs").read_text(encoding="utf-8")
            self.assertIn('format!("{target_imgref}")', state)
            self.assertNotIn('format!("ostree-unverified-image:{imgref}")', state)

    def test_patch_preflights_policy_and_pins_digest_before_composefs_pull(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-policy-pull-") as temp:
            source = copy_fixture(Path(temp))
            module.apply(source)
            repo = (source / "crates/lib/src/bootc_composefs/repo.rs").read_text(encoding="utf-8")
            self.assertIn("ImageImporter::new", repo)
            self.assertIn(".prepare()", repo)
            self.assertIn("SignatureSource::ContainerPolicy", repo)
            self.assertIn("digested_pullspec", repo)
            self.assertIn("authenticated_image_reference", repo)
            self.assertIn("let pinned_imgref", repo)
            self.assertIn("\n        pull_composefs_direct(&repo, &pinned_imgref", repo)

    def test_patch_covers_check_auto_switch_and_install_callers(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-policy-callers-") as temp:
            source = copy_fixture(Path(temp))
            module.apply(source)
            update = (source / "crates/lib/src/bootc_composefs/update.rs").read_text(encoding="utf-8")
            repo = (source / "crates/lib/src/bootc_composefs/repo.rs").read_text(encoding="utf-8")
            install = (source / "crates/lib/src/install.rs").read_text(encoding="utf-8")
            self.assertIn("authenticated_image_reference(imgref).await?", update)
            self.assertIn("authenticated_image_reference(spec_imgref).await?", repo)
            self.assertIn("authenticated_image_reference(&source_spec).await?", install)
            self.assertIn("SignatureSource::ContainerPolicy", repo)

    def test_install_source_fetch_is_not_replaced_by_stable_target(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-policy-source-target-") as temp:
            source = copy_fixture(Path(temp))
            module.apply(source)
            install = (source / "crates/lib/src/install.rs").read_text(encoding="utf-8")
            composefs_install = install.split(
                "    if state.composefs_options.composefs_backend {", 1
            )[1].split("\n    }", 1)[0]

            # The source image reference is the object fetched and verified for
            # this installation.  target_imgref is durable update metadata only.
            self.assertIn(
                "let fetch_ref = state.source.composefs_fetch_reference();",
                composefs_install,
            )
            self.assertIn(
                "let fetch_imgref = if fetch_ref.transport",
                composefs_install,
            )
            self.assertIn(
                "== ostree_container::Transport::ContainerStorage",
                composefs_install,
            )
            self.assertIn(
                "let mut source_spec = ImageReference::from(state.target_imgref.clone());",
                composefs_install,
            )
            self.assertIn(
                "source_spec.image = fetch_ref.name.clone();",
                composefs_install,
            )
            self.assertIn(
                "source_spec.transport = fetch_ref.transport.to_string();",
                composefs_install,
            )
            self.assertIn(
                "authenticated_image_reference(&source_spec).await?",
                composefs_install,
            )
            self.assertIn(
                "let (manifest, _) = get_container_manifest_and_config(&fetch_imgref).await?;",
                composefs_install,
            )
            self.assertIn(
                "Some(&fetch_imgref)",
                composefs_install,
            )
            source_pos = composefs_install.index(
                "let fetch_ref = state.source.composefs_fetch_reference();"
            )
            auth_pos = composefs_install.index(
                "authenticated_image_reference(&source_spec).await?"
            )
            manifest_pos = composefs_install.index(
                "get_container_manifest_and_config(&fetch_imgref).await?"
            )
            pull_pos = composefs_install.index("pull_install_composefs_repository(")
            self.assertLess(source_pos, auth_pos)
            self.assertLess(auth_pos, manifest_pos)
            self.assertLess(manifest_pos, pull_pos)
            self.assertNotIn("let mut fetch_ref", composefs_install)
            self.assertNotIn("fetch_ref = install_spec", composefs_install)

    def test_preflight_preserves_signature_field_of_actual_bootc_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = copy_fixture(Path(temp))
            load_patch_module().apply(root)
            repo = (root / "crates/lib/src/bootc_composefs/repo.rs").read_text()
            self.assertIn("let ostree_imgref: OstreeImageReference = spec_imgref.clone().into();", repo)
            self.assertNotIn('format!("{}:{}", spec_imgref.transport, spec_imgref.image)', repo)

    def test_install_repository_does_not_replace_verified_source_with_target(self):
        with tempfile.TemporaryDirectory() as temp:
            source = copy_fixture(Path(temp))
            load_patch_module().apply(source)
            repo = (source / "crates/lib/src/bootc_composefs/repo.rs").read_text()
            self.assertIn("let pinned_imgref = imgref.clone();", repo)
            self.assertNotIn("install_spec.to_image_proxy_ref()?", repo)

    def test_verified_pull_reference_canonicalizes_tag_and_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            source = copy_fixture(Path(temp))
            load_patch_module().apply(source)
            repo = (source / "crates/lib/src/bootc_composefs/repo.rs").read_text()
            self.assertIn("pinned.canonicalize()", repo)
            self.assertNotIn("    Ok(pinned)\n", repo)

    def test_patch_is_idempotent(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-policy-idempotent-") as temp:
            source = copy_fixture(Path(temp))
            module.apply(source)
            first = {p.relative_to(source): p.read_bytes() for p in source.rglob("*.rs")}
            module.apply(source)
            second = {p.relative_to(source): p.read_bytes() for p in source.rglob("*.rs")}
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
