"""Contract and execution tests for the verified stable update channel."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build.yml"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/kyth-os/rowyn"


class UpdateChannelWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)

    def test_stable_promotion_is_gated_by_key_signature_and_digest_verification(self):
        jobs = self.workflow["jobs"]
        self.assertIn("promote", jobs)
        promote = jobs["promote"]
        self.assertEqual(set(promote["needs"]), {"build", "sign"})
        self.assertEqual(
            promote["permissions"],
            {"contents": "read", "packages": "write"},
        )

        names = [step["name"] for step in promote["steps"]]
        self.assertLess(
            names.index("Sign exact image with update-channel key"),
            names.index("Verify update-channel signature"),
        )
        self.assertLess(
            names.index("Verify update-channel signature"),
            names.index("Reject stale main before stable promotion"),
        )
        self.assertEqual(
            names.index("Reject stale main before stable promotion") + 1,
            names.index("Promote exact digest to stable"),
        )
        self.assertLess(
            names.index("Reject stale main before stable promotion"),
            names.index("Promote exact digest to stable"),
        )
        self.assertLess(
            names.index("Promote exact digest to stable"),
            names.index("Verify stable tag digest"),
        )

        self.assertIn('test -s "keys/update.pub"', self.workflow_text)
        self.assertIn("ROWYN_UPDATE_SIGNING_KEY", self.workflow_text)
        self.assertIn("ROWYN_UPDATE_SIGNING_PASSWORD", self.workflow_text)
        self.assertIn("cosign sign --yes --key env://ROWYN_UPDATE_SIGNING_KEY", self.workflow_text)
        self.assertIn("cosign verify --key keys/update.pub", self.workflow_text)
        self.assertIn("skopeo copy --preserve-digests", self.workflow_text)
        self.assertIn("IMAGE_NAME}@${IMAGE_DIGEST}", self.workflow_text)
        self.assertIn('IMAGE_NAME}:stable', self.workflow_text)

    def test_stale_main_guard_rejects_without_copy_and_current_main_allows_copy(self):
        promote = self.workflow["jobs"]["promote"]
        guard_step = next(
            step for step in promote["steps"]
            if step["name"] == "Reject stale main before stable promotion"
        )
        copy_step = next(
            step for step in promote["steps"]
            if step["name"] == "Promote exact digest to stable"
        )
        guard_script = guard_step["run"]
        copy_script = copy_step["run"].replace(
            "${{ github.actor }}", "${GITHUB_ACTOR}"
        )
        self.assertIn("GH_TOKEN", guard_step["env"])
        self.assertIn("gh api", guard_script)
        self.assertIn("git/ref/heads/main", guard_script)
        self.assertIn("GITHUB_REPOSITORY", guard_script)

        with tempfile.TemporaryDirectory(prefix="rowyn-update-channel-guard-") as temp:
            temp = Path(temp)
            log = temp / "calls.log"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "[ \"${GH_TOKEN-}\" = authenticated-token ]\n"
                "case \"$*\" in\n"
                "  *\"repos/${GITHUB_REPOSITORY}/git/ref/heads/main\"*) ;;\n"
                "  *) exit 42 ;;\n"
                "esac\n"
                "printf 'gh api\\n' >> \"$TEST_LOG\"\n"
                "printf '%s\\n' \"${GH_API_HEAD_SHA}\"\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            skopeo = fake_bin / "skopeo"
            skopeo.write_text(
                "#!/bin/sh\n"
                "printf 'skopeo %s\\n' \"$*\" >> \"$TEST_LOG\"\n",
                encoding="utf-8",
            )
            skopeo.chmod(0o755)

            expected_sha = "b" * 40
            env = {
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "TEST_LOG": str(log),
                "EXPECTED_SHA": expected_sha,
                "GH_TOKEN": "authenticated-token",
                "GITHUB_REPOSITORY": "kyth-os/Rowyn",
                "IMAGE_REF": f"{IMAGE}:build",
                "IMAGE_DIGEST": DIGEST,
                "GITHUB_TOKEN": "token",
                "GITHUB_ACTOR": "actor",
            }
            combined_script = f"{guard_script}\n{copy_script}"

            for head_sha, should_copy in (
                ("a" * 40, False),
                ("not-a-sha", False),
                (expected_sha, True),
            ):
                with self.subTest(head_sha=head_sha, should_copy=should_copy):
                    log.unlink(missing_ok=True)
                    case_env = {**env, "GH_API_HEAD_SHA": head_sha}
                    result = subprocess.run(
                        ["bash", "-euo", "pipefail", "-c", combined_script],
                        cwd=temp,
                        env=case_env,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode == 0, should_copy, result.stderr)
                    calls = log.read_text(encoding="utf-8") if log.exists() else ""
                    self.assertIn("gh api", calls)
                    self.assertEqual("skopeo copy" in calls, should_copy)

    def test_keyless_signing_remains_identity_bound_and_uses_oidc(self):
        sign = self.workflow["jobs"]["sign"]
        self.assertEqual(sign["permissions"]["id-token"], "write")
        sign_text = "\n".join(step.get("run", "") for step in sign["steps"])
        self.assertIn("cosign sign -y", sign_text)
        self.assertIn("cosign verify", sign_text)
        self.assertIn("--certificate-identity-regexp", sign_text)
        self.assertIn("--certificate-oidc-issuer", sign_text)
        self.assertNotIn("ROWYN_UPDATE_SIGNING_KEY", sign_text)

    def test_promotion_script_fails_closed_before_copy_when_public_key_is_missing(self):
        promote = self.workflow["jobs"]["promote"]
        sign_step = next(
            step for step in promote["steps"]
            if step["name"] == "Sign exact image with update-channel key"
        )
        copy_step = next(
            step for step in promote["steps"]
            if step["name"] == "Promote exact digest to stable"
        )
        verify_step = next(
            step for step in promote["steps"]
            if step["name"] == "Verify update-channel signature"
        )

        with tempfile.TemporaryDirectory(prefix="rowyn-update-channel-") as temp:
            temp = Path(temp)
            log = temp / "calls.log"
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            cosign = fake_bin / "cosign"
            cosign.write_text(
                "#!/bin/sh\n"
                "printf 'cosign %s\\n' \"$*\" >> \"$TEST_LOG\"\n",
                encoding="utf-8",
            )
            cosign.chmod(0o755)
            skopeo = fake_bin / "skopeo"
            skopeo.write_text(
                "#!/bin/sh\n"
                "printf 'skopeo %s\\n' \"$*\" >> \"$TEST_LOG\"\n",
                encoding="utf-8",
            )
            skopeo.chmod(0o755)
            env = {
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "TEST_LOG": str(log),
                "IMAGE_REF": f"{IMAGE}:build",
                "IMAGE_DIGEST": DIGEST,
                "ROWYN_UPDATE_SIGNING_KEY": "private-key-material",
                "COSIGN_PASSWORD": "password",
                "GITHUB_TOKEN": "token",
                "GITHUB_ACTOR": "actor",
            }
            # The key check belongs to the signature step, not the copy step.
            public_key_check = "test -s \"keys/update.pub\""
            self.assertIn(public_key_check, verify_step["run"])
            self.assertIn(public_key_check, sign_step["run"])
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", sign_step["run"]],
                cwd=temp,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(log.exists(), "signing must not run without the checked-in public key")
            self.assertIn("skopeo copy --preserve-digests", copy_step["run"])

    def test_stable_digest_verification_compares_registry_tag_to_source_digest(self):
        promote = self.workflow["jobs"]["promote"]
        verify_step = next(
            step for step in promote["steps"]
            if step["name"] == "Verify stable tag digest"
        )
        script = verify_step["run"]
        self.assertIn('stable_digest="$(skopeo inspect', script)
        self.assertIn('"${IMAGE_DIGEST}"', script)
        self.assertRegex(script, r"\[\[ .*stable_digest.*==.*IMAGE_DIGEST")


if __name__ == "__main__":
    unittest.main()
