"""Check publication of Ubuntu rootfs downloads and guest evidence."""

import runpy
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

runner = runpy.run_path(str(Path(__file__).with_name("guest.py")))


class EvidenceExport(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.output = Path(work.name)
        self.header = b"KCFG-TRACE\t1\t" + b"a" * 64 + b"\n"
        self.trace = self.header + b"2\t1\t0\t1\n1\t0\t1\t0\nEND\t2\n"
        self.pending = self.output / "trace.pending"
        self.pending.write_bytes(self.trace)
        self.status = self.output / "guest.status"
        self.args = Namespace(mode="profile", late_module=[])
        self.info = {
            "distro": "ubuntu", "version": "24.04", "kernel": "test", "mode": "profile",
            "ready": "yes", "late_modules": "0", "status": "ok",
        }

    def report(self):
        self.status.write_text(
            "".join(f"{key}={value}\n" for key, value in self.info.items()), encoding="ascii")

    def assert_rejected(self):
        with self.assertRaises(ValueError):
            runner["validate"](self.output, self.args)
        self.assertFalse((self.output / "trace.tsv").exists())
        self.assertTrue(self.pending.exists())

    def test_complete_trace_is_published_unchanged(self):
        self.report()
        self.assertEqual(runner["validate"](self.output, self.args), self.info)
        self.assertEqual((self.output / "trace.tsv").read_bytes(), self.trace)
        self.assertFalse(self.pending.exists())

    def test_empty_or_malformed_trace_is_not_published(self):
        self.report()
        for data in (b"", self.trace[:-1], self.trace.removesuffix(b"END\t2\n"),
                     self.trace.replace(b"END\t2", b"END\t1"), self.trace + b"extra\n",
                     self.trace.replace(b"KCFG-TRACE\t1", b"KCFG-TRACE\t2"),
                     self.trace.replace(b"a" * 64, b"short-id"),
                     self.trace.replace(b"2\t1\t0\t1", b"1\t1\t0\t1"),
                     self.trace.replace(b"2\t1\t0\t1", b"3\t1\t0\t1"),
                     self.trace.replace(b"2\t1\t0\t1", b"2\t1\t0\t2"), self.header + b"END\t0\n"):
            with self.subTest(data=data):
                self.pending.write_bytes(data)
                self.assert_rejected()

    def test_profile_requires_trace(self):
        self.report()
        self.pending.unlink()
        with self.assertRaises(ValueError):
            runner["validate"](self.output, self.args)
        self.assertFalse((self.output / "trace.tsv").exists())

    def test_other_modes_do_not_require_trace(self):
        self.pending.unlink()
        for mode in ("baseline", "candidate"):
            with self.subTest(mode=mode):
                self.args.mode = self.info["mode"] = mode
                self.report()
                self.assertEqual(runner["validate"](self.output, self.args), self.info)
                self.assertFalse((self.output / "trace.tsv").exists())

    def test_wrong_guest_or_failed_workload(self):
        for key, value in (("distro", "other"), ("status", "failed"), ("kernel", ""), ("version", ""),
                           ("ready", "no"), ("mode", "candidate"), ("late_modules", "1")):
            with self.subTest(key=key):
                previous = self.info[key]
                self.info[key] = value
                self.report()
                self.assert_rejected()
                self.info[key] = previous

    def test_status_must_finish_unambiguously(self):
        self.report()
        complete = self.status.read_bytes()
        for data in (complete[:-1], complete.removesuffix(b"status=ok\n"),
                     complete + b"stage=workload\n", b"status=failed\n" + complete):
            with self.subTest(data=data):
                self.status.write_bytes(data)
                self.assert_rejected()


class Rootfs(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.cache = Path(work.name)
        self.archive = self.cache / f"ubuntu-base-{runner['VERSION']}" / runner["ARCHIVE"]

    def test_fetch_stages_https_download_and_reuses_cache(self):
        def download(command, *, check):
            self.assertTrue(check)
            self.assertEqual(command[:2], ["curl", "--disable"])
            for option in ("--proto", "--proto-redir"):
                self.assertEqual(command[command.index(option) + 1], "=https")
            self.assertTrue(command[-1].startswith("https://"))
            self.assertFalse(self.archive.parent.exists())
            staged = Path(command[command.index("--output") + 1])
            self.assertNotEqual(staged, self.archive)
            staged.write_bytes(b"archive data")

        with patch.object(subprocess, "run", side_effect=download) as curl:
            self.assertEqual(runner["fetch"](self.cache), self.archive)
            self.assertEqual(runner["fetch"](self.cache), self.archive)
        curl.assert_called_once()
        self.assertEqual(self.archive.read_bytes(), b"archive data")
        self.assertEqual(list(self.cache.iterdir()), [self.archive.parent])

    def test_fetch_does_not_publish_failed_or_empty_download(self):
        for fail, error in ((True, subprocess.CalledProcessError), (False, ValueError)):
            def download(command, *, check, fail=fail):
                staged = Path(command[command.index("--output") + 1])
                staged.write_bytes(b"partial data" if fail else b"")
                if fail:
                    raise subprocess.CalledProcessError(1, command)

            with self.subTest(fail=fail), patch.object(subprocess, "run", side_effect=download):
                with self.assertRaises(error):
                    runner["fetch"](self.cache)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_custom_rootfs_without_checksum_reaches_packer(self):
        rootfs = self.cache / "custom.tar.gz"
        rootfs.write_bytes(b"archive data")
        command = ["guest.py", "run", "--mode", "baseline", "--rootfs", str(rootfs),
                   "--kernel", str(rootfs), "--workload", str(rootfs), "--output", str(self.cache / "run")]
        pack = Mock(side_effect=RuntimeError("stop before boot"))
        with (
            patch.object(sys, "argv", command),
            patch.dict(runner["run"].__globals__, pack=pack),
            self.assertRaisesRegex(RuntimeError, "stop before boot"),
        ):
            runner["main"]()
        self.assertEqual(pack.call_args.args[1], rootfs)


if __name__ == "__main__":
    unittest.main()
