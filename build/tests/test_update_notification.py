"""A staged image gets one passive notification, never an automatic restart."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load_notifier():
    path = ROOT / "shared/rowyn-update-notify"
    loader = importlib.machinery.SourceFileLoader("rowyn_update_notify", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class UpdateNotificationTests(unittest.TestCase):
    def test_staged_digest_is_notified_once_and_new_digest_notifies_again(self):
        module = load_notifier()
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            calls = []
            first = "sha256:" + "a" * 64
            second = "sha256:" + "b" * 64
            self.assertTrue(module.notify_once(first, state, lambda: calls.append("notify")))
            self.assertFalse(module.notify_once(first, state, lambda: calls.append("notify")))
            self.assertTrue(module.notify_once(second, state, lambda: calls.append("notify")))
            self.assertEqual(calls, ["notify", "notify"])
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_no_stage_malformed_status_and_failed_notify_do_not_record_success(self):
        module = load_notifier()
        self.assertIsNone(module.staged_digest({"status": {"staged": None}}))
        self.assertIsNone(module.staged_digest({"status": {"staged": {"image": {"imageDigest": "not-a-digest"}}}}))
        digest = "sha256:" + "a" * 64
        self.assertEqual(module.staged_digest({"status": {"staged": {"image": {"imageDigest": digest}}}}), digest)
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            def fail():
                raise RuntimeError("notification unavailable")
            self.assertFalse(module.notify_once(digest, state, fail))
            self.assertFalse(state.exists())

    def test_invalid_digest_or_symlink_state_cannot_notify(self):
        module = load_notifier()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            target = path / "original"
            target.write_text("preserve")
            state = path / "state.json"
            state.symlink_to(target)
            calls = []
            self.assertFalse(module.notify_once("sha256:" + "a" * 64, state, lambda: calls.append(1)))
            self.assertFalse(module.notify_once("invalid", path / "other", lambda: calls.append(1)))
            self.assertEqual(calls, [])
            self.assertEqual(target.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
