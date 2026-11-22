"""Check that incomplete or misidentified guest runs never publish evidence."""

import hashlib
import runpy
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

runner = runpy.run_path(str(Path(__file__).with_name("guest.py")))


class EvidenceExport(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.output = Path(work.name)
        self.trace = b"header\tprofile\n1\t0\t1\n"
        self.pending = self.output / "trace.pending"
        self.pending.write_bytes(self.trace)
        self.args = Namespace(mode="profile", late_module=[])
        self.info = {
            "distro": "ubuntu", "version": "24.04", "kernel": "test", "mode": "profile",
            "ready": "yes", "late_modules": "0", "status": "ok",
            "sha256": hashlib.sha256(self.trace).hexdigest(),
        }

    def report(self):
        (self.output / "guest.status").write_text(
            "".join(f"{key}={value}\n" for key, value in self.info.items()), encoding="ascii")

    def assert_rejected(self):
        self.report()
        with self.assertRaises(ValueError):
            runner["validate"](self.output, self.args)
        self.assertFalse((self.output / "trace.tsv").exists())

    def test_exact_transfer(self):
        self.report()
        runner["validate"](self.output, self.args)
        self.assertEqual((self.output / "trace.tsv").read_bytes(), self.trace)
        self.assertFalse(self.pending.exists())

    def test_truncated_trace(self):
        self.pending.write_bytes(self.trace[:-1])
        self.assert_rejected()

    def test_empty_trace_with_matching_checksum(self):
        self.pending.write_bytes(b"")
        self.info["sha256"] = hashlib.sha256(b"").hexdigest()
        self.assert_rejected()

    def test_wrong_guest_or_failed_workload(self):
        for key, value in (("distro", "other"), ("status", "failed"), ("kernel", ""),
                           ("ready", "no"), ("mode", "candidate"), ("late_modules", "1")):
            with self.subTest(key=key):
                previous = self.info[key]
                self.info[key] = value
                self.assert_rejected()
                self.info[key] = previous


if __name__ == "__main__":
    unittest.main()
