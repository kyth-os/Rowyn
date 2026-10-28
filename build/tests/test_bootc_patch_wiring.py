"""Regression tests for bootc source patch wiring in the image builder."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTAINERFILE = ROOT / "Containerfile"
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


class BootcPatchWiringTests(unittest.TestCase):
    def test_builder_installs_python3_and_applies_patch_before_build_script(self):
        containerfile = CONTAINERFILE.read_text(encoding="utf-8")
        builder = containerfile.split("FROM base AS builder", 1)[1].split(
            "# --- system: assemble the bootable image", 1
        )[0]

        self.assertIn("apt install -y python3", builder)
        self.assertRegex(builder, r"\bpython3\b")
        patch_command = "/usr/bin/python3 /ctx/shared/bootc-sigpolicy-patch.py /home/build/bootc"
        build_command = "PATH=/opt/rust/bin:$PATH /ctx/shared/build.sh /home/build/bootc"
        self.assertIn(patch_command, builder)
        self.assertIn(build_command, builder)
        self.assertLess(builder.index("cp -a /ctx/assets/bootc /home/build/bootc"), builder.index(patch_command))
        self.assertLess(builder.index(patch_command), builder.index(build_command))

    def test_builder_patch_step_changes_pinned_source_before_build_runs(self):
        module = load_patch_module()
        with tempfile.TemporaryDirectory(prefix="rowyn-bootc-patch-build-") as temp:
            root = Path(temp)
            assets = root / "ctx/assets"
            shared = root / "ctx/shared"
            build_root = root / "home/build"
            assets.mkdir(parents=True)
            shared.mkdir(parents=True)
            build_root.mkdir(parents=True)
            source_fixture = copy_fixture(root / "fixture")
            shutil.copy2(PATCH, shared / PATCH.name)

            build_script = shared / "build.sh"
            build_script.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                "source = Path(sys.argv[1])\n"
                "state = (source / 'crates/lib/src/bootc_composefs/state.rs').read_text()\n"
                "if 'format!(\"{target_imgref}\")' not in state:\n"
                "    raise SystemExit('bootc signature-policy patch was not applied before build')\n",
                encoding="utf-8",
            )
            build_script.chmod(0o755)

            copied_source = assets / "bootc"
            shutil.copytree(source_fixture, copied_source)
            result = subprocess.run(
                [
                    "bash",
                    "-euc",
                    'cp -a "$ASSETS/bootc" "$SOURCE" && '
                    '/usr/bin/python3 "$PATCH_SCRIPT" "$SOURCE" && '
                    '"$BUILD_SCRIPT" "$SOURCE"',
                ],
                env={
                    "ASSETS": str(assets),
                    "SOURCE": str(build_root / "bootc"),
                    "PATCH_SCRIPT": str(shared / PATCH.name),
                    "BUILD_SCRIPT": str(build_script),
                },
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            patched_source = build_root / "bootc"
            self.assertIn(
                'format!("{target_imgref}")',
                (patched_source / "crates/lib/src/bootc_composefs/state.rs").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual(module.BOOTC_VERSION, "1.17.3")


if __name__ == "__main__":
    unittest.main()
