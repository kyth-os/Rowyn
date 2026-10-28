#!/usr/bin/env python3
"""Apply the reviewed bootc 1.17.3 composefs signature-policy patch.

The patch is deliberately source-pinned and fail-closed: it only edits the
three upstream Rust files whose exact anchors are reviewed here, refuses any
other bootc version, and is idempotent.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


BOOTC_VERSION = "1.17.3"


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{path}: expected one patch anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _require_version(source: Path) -> None:
    cargo = source / "crates/lib/Cargo.toml"
    text = cargo.read_text(encoding="utf-8")
    package = re.search(r"(?ms)^\[package\](.*?)(?:^\[|\Z)", text)
    version = re.search(r'^version = "([^"]+)"$', package.group(1), re.MULTILINE) if package else None
    if not version or version.group(1) != BOOTC_VERSION:
        raise ValueError(f"bootc source is not pinned to {BOOTC_VERSION}")


def apply(source: Path) -> None:
    """Apply the patch to a checked-out bootc source tree."""
    source = source.resolve()
    _require_version(source)

    repo = source / "crates/lib/src/bootc_composefs/repo.rs"
    state = source / "crates/lib/src/bootc_composefs/state.rs"
    update = source / "crates/lib/src/bootc_composefs/update.rs"
    install = source / "crates/lib/src/install.rs"

    _replace_once(
        repo,
        "use std::sync::Arc;\n",
        "use std::os::fd::AsFd;\nuse std::sync::Arc;\n",
    )
    _replace_once(
        repo,
        "use ostree_ext::containers_image_proxy;\n",
        "use ostree_ext::container::{OstreeImageReference, SignatureSource};\n"
        "use ostree_ext::containers_image_proxy;\n"
        "use ostree_ext::ostree;\n",
    )
    _replace_once(
        repo,
        "pub(crate) fn final_repository_policy(\n"
        "    image_fsverity_policy: Option<bool>,\n"
        "    requested_relaxed: bool,\n"
        ") -> bool {\n"
        "    image_fsverity_policy == Some(true) || (image_fsverity_policy.is_none() && requested_relaxed)\n"
        "}\n\n",
        "pub(crate) fn final_repository_policy(\n"
        "    image_fsverity_policy: Option<bool>,\n"
        "    requested_relaxed: bool,\n"
        ") -> bool {\n"
        "    image_fsverity_policy == Some(true) || (image_fsverity_policy.is_none() && requested_relaxed)\n"
        "}\n\n"
        "/// Authenticate a composefs fetch through bootc's native containers-image\n"
        "/// proxy, then pin the exact manifest digest used by the composefs pull.\n"
        "/// The digest pin closes the tag TOCTOU window between verification and\n"
        "/// importing the image into composefs. Existing insecure references are\n"
        "/// intentionally returned unchanged; this function never upgrades their\n"
        "/// policy metadata without performing a policy-authenticated fetch.\n"
        "pub(crate) async fn authenticated_image_reference(\n"
        "    spec_imgref: &crate::spec::ImageReference,\n"
        ") -> Result<crate::spec::ImageReference> {\n"
        "    let ostree_imgref: OstreeImageReference = spec_imgref.clone().into();\n"
        "    if !matches!(ostree_imgref.sigverify, SignatureSource::ContainerPolicy) {\n"
        "        return Ok(spec_imgref.clone());\n"
        "    }\n\n"
        "    let tempdir = tempfile::TempDir::new().context(\"Creating signature preflight repository\")?;\n"
        "    let root = Dir::open_ambient_dir(tempdir.path(), ambient_authority())?;\n"
        "    let repo = ostree::Repo::create_at_dir(root.as_fd(), \".\", ostree::RepoMode::Bare, None)\n"
        "        .context(\"Creating signature preflight repository\")?;\n"
        "    let mut importer = ostree_ext::container::store::ImageImporter::new(\n"
        "        &repo,\n"
        "        &ostree_imgref,\n"
        "        crate::deploy::new_proxy_config(),\n"
        "    )\n"
        "    .await\n"
        "    .context(\"Creating signature preflight importer\")?;\n"
        "    use ostree_ext::container::store::PrepareResult;\n"
        "    let prepared = importer\n"
        "        .prepare()\n"
        "        .await\n"
        "        .context(\"Authenticating composefs image\")?;\n"
        "    let manifest_digest = match prepared {\n"
        "        PrepareResult::Ready(import) => import.manifest_digest.to_string(),\n"
        "        PrepareResult::AlreadyPresent(_) => {\n"
        "            anyhow::bail!(\"Signature preflight unexpectedly reused its empty repository\")\n"
        "        }\n"
        "    };\n\n"
        "    let mut pinned = spec_imgref.clone();\n"
        "    pinned.image = crate::utils::digested_pullspec(&pinned.image, &manifest_digest);\n"
        "    pinned.canonicalize()\n"
        "}\n\n",
    )
    _replace_once(
        repo,
        "    let original_imgref: containers_image_proxy::ImageReference = state\n"
        "        .source\n"
        "        .imageref\n"
        "        .to_string()\n"
        "        .as_str()\n"
        "        .try_into()\n"
        "        .context(\"Parsing source image reference\")?;\n"
        "    let imgref = fetch_imgref.unwrap_or(&original_imgref);\n",
        "    let original_imgref: containers_image_proxy::ImageReference = state\n"
        "        .source\n"
        "        .imageref\n"
        "        .to_string()\n"
        "        .as_str()\n"
        "        .try_into()\n"
        "        .context(\"Parsing source image reference\")?;\n"
        "    let imgref = fetch_imgref.unwrap_or(&original_imgref);\n"
        "    let pinned_imgref = imgref.clone();\n",
    )
    _replace_once(repo, "            &imgref,\n", "            &pinned_imgref,\n")
    _replace_once(
        repo,
        "        pull_composefs_direct(&repo, &imgref, false, ProgressWriter::default()).await?\n",
        "        pull_composefs_direct(&repo, &pinned_imgref, false, ProgressWriter::default()).await?\n",
    )
    _replace_once(
        repo,
        "    let imgref = match delta {\n"
        "        Some(delta) => {\n"
        "            delta.validate_image_reference(spec_imgref)?;\n"
        "            crate::delta::reject_unified_storage(delta, use_unified)?;\n"
        "            delta.pull_ref()?\n"
        "        }\n"
        "        None => spec_imgref.to_image_proxy_ref()?,\n"
        "    };\n",
        "    let pinned_spec_imgref = if delta.is_some() {\n"
        "        let delta_sigref: OstreeImageReference = spec_imgref.clone().into();\n"
        "        if matches!(delta_sigref.sigverify, SignatureSource::ContainerPolicy) {\n"
        "            anyhow::bail!(\n"
        "                \"Cannot apply a delta for a ContainerPolicy image without signature proof\"\n"
        "            );\n"
        "        }\n"
        "        spec_imgref.clone()\n"
        "    } else {\n"
        "        authenticated_image_reference(spec_imgref).await?\n"
        "    };\n\n"
        "    let imgref = match delta {\n"
        "        Some(delta) => {\n"
        "            delta.validate_image_reference(spec_imgref)?;\n"
        "            crate::delta::reject_unified_storage(delta, use_unified)?;\n"
        "            delta.pull_ref()?\n"
        "        }\n"
        "        None => pinned_spec_imgref.to_image_proxy_ref()?,\n"
        "    };\n",
    )
    _replace_once(
        state,
        "    let imgref = target_imgref.to_image_proxy_ref()?;\n",
        "    let _imgref = target_imgref.to_image_proxy_ref()?;\n",
    )
    _replace_once(
        state,
        "        // TODO (Johan-Liebert1): The image won't always be unverified\n"
        "        format!(\"ostree-unverified-image:{imgref}\"),\n",
        "        // Preserve the actual signature policy in the durable origin.\n"
        "        // This is intentionally derived from the verified target reference;\n"
        "        // old insecure origins are never rewritten by this code.\n"
        "        format!(\"{target_imgref}\"),\n",
    )
    _replace_once(
        update,
        "    let imgref_repr = imgref.to_image_proxy_ref()?;\n",
        "    let pinned_imgref = crate::bootc_composefs::repo::authenticated_image_reference(imgref).await?;\n"
        "    let imgref_repr = pinned_imgref.to_image_proxy_ref()?;\n",
    )
    _replace_once(
        install,
        "async fn install_to_filesystem_impl(\n",
        "fn composefs_source_spec(\n"
        "    target_imgref: &ostree_container::OstreeImageReference,\n"
        "    fetch_ref: &ostree_container::ImageReference,\n"
        ") -> ImageReference {\n"
        "    ImageReference::from(ostree_container::OstreeImageReference {\n"
        "        sigverify: target_imgref.sigverify.clone(),\n"
        "        imgref: fetch_ref.clone(),\n"
        "    })\n"
        "}\n\n"
        "#[cfg(test)]\n"
        "mod rowyn_source_reference_tests {\n"
        "    use super::*;\n"
        "    #[test]\n"
        "    fn registry_source_keeps_exact_digest_and_target_signature() {\n"
        "        let digest = \"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\";\n"
        "        let source = ostree_container::ImageReference {\n"
        "            transport: ostree_container::Transport::Registry,\n"
        "            name: format!(\"ghcr.io/kyth-os/rowyn@{digest}\"),\n"
        "        };\n"
        "        let target = ostree_container::OstreeImageReference {\n"
        "            sigverify: ostree_container::SignatureSource::ContainerPolicy,\n"
        "            imgref: ostree_container::ImageReference {\n"
        "                transport: ostree_container::Transport::Registry,\n"
        "                name: \"ghcr.io/kyth-os/rowyn:stable\".to_string(),\n"
        "            },\n"
        "        };\n"
        "        let actual = composefs_source_spec(&target, &source);\n"
        "        assert_eq!(actual.transport, \"registry\");\n"
        "        assert_eq!(actual.image, source.name);\n"
        "        assert!(matches!(actual.signature, Some(crate::spec::ImageSignature::ContainerPolicy)));\n"
        "        assert_eq!(actual.to_image_proxy_ref().unwrap().to_string(), format!(\"docker://ghcr.io/kyth-os/rowyn@{digest}\"));\n"
        "    }\n"
        "}\n\n"
        "async fn install_to_filesystem_impl(\n",
    )
    _replace_once(
        install,
        "        let fetch_ref = state.source.composefs_fetch_reference();\n",
        "        let fetch_ref = state.source.composefs_fetch_reference();\n"
        "        let fetch_imgref = if fetch_ref.transport\n"
        "            == ostree_container::Transport::ContainerStorage\n"
        "        {\n"
        "            fetch_ref.clone().into()\n"
        "        } else {\n"
        "            // Rebuild the spec through bootc's canonical conversion so\n"
        "            // the target's signature policy accompanies the actual source\n"
        "            // transport and name.  Transport::Display is a proxy URL\n"
        "            // (e.g. docker://), not a valid spec transport string.\n"
        "            let source_spec = composefs_source_spec(&state.target_imgref, &fetch_ref);\n"
        "            let source_spec =\n"
        "                crate::bootc_composefs::repo::authenticated_image_reference(&source_spec).await?;\n"
        "            source_spec.to_image_proxy_ref()?\n"
        "        };\n"
        "        let (manifest, _) = get_container_manifest_and_config(&fetch_imgref).await?;\n",
    )
    _replace_once(
        install,
        "        let (manifest, _) = get_container_manifest_and_config(&fetch_ref).await?;\n",
        "        // manifest was fetched through the verified source reference above.\n",
    )
    _replace_once(
        install,
        "        let fetch_imgref = Some(fetch_ref.clone().into());\n",
        "        // fetch_imgref was selected and authenticated above.\n",
    )
    _replace_once(
        install,
        "            fetch_imgref.as_ref(),\n",
        "            Some(&fetch_imgref),\n",
    )



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    apply(args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
