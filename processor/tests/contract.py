"""Exercise the shipped executable, including failure-before-publication cases."""

import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROGRAM = Path(sys.argv.pop(1)).resolve()
INTERACTIVE = "--tmux" in sys.argv
if INTERACTIVE:
    sys.argv.remove("--tmux")

BASELINE = (
    '# Built by Kconfig\nCONFIG_MODULES=y\nCONFIG_UNMAPPED="quotes and spaces"\n'
    "CONFIG_USED=y\nCONFIG_IDLE=y\nCONFIG_ROOT=m\nCONFIG_LATE=y\nCONFIG_PARTIAL=y\n"
    "CONFIG_ALWAYS=y\nCONFIG_NUM=14\nCONFIG_ALREADY=m\n# CONFIG_OFF is not set\n"
    "CONFIG_NO_MODULE=y\nCONFIG_UNRELATED=y"
)
ROWS = [
    ("USED", "used", "drop", "fs/used/one.c"),
    ("USED", "used", "drop", "fs/used/two.c"),
    ("IDLE", "idle", "drop", "drivers/idle.c"),
    ("ROOT", "root", "defer", "fs/root.c"),
    ("LATE", "late-io", "defer", "drivers/late.c"),
    ("PARTIAL", "partial", "drop", "fs/p1.c"),
    ("PARTIAL", "partial", "drop", "fs/p2.c"),
    ("ALWAYS", "-", "keep", "drivers/always.c"),
    ("NUM", "-", "drop", "drivers/number.c"),
    ("ABSENT", "-", "drop", "drivers/missing.c"),
    ("ALREADY", "already", "defer", "drivers/already.c"),
    ("OFF", "-", "drop", "drivers/off.c"),
    ("NO_MODULE", "-", "defer", "drivers/builtin.c"),
    ("MODULES", "-", "drop", "kernel/module/example.c"),
]
HITS = {1: "0\t0\t1", 2: "0\t1\t0", 3: "0\t0\t1", 4: "1\t0\t0", 5: "0\t1\t0"}


def make_id(campaign):
    h = hashlib.sha256(b"kcfg-scope-v1\0")
    for name in ("campaign.seed", "scope.tsv"):
        h.update((campaign / name).read_bytes())
    h.update(b"\0")
    h.update((campaign / "baseline.config").read_bytes())
    digest = h.hexdigest()
    (campaign / "campaign.id").write_text(digest + "\n")
    return digest


def trace_for(digest, hits):
    records = [f"KCFG-TRACE\t1\t{digest}"]
    for i in range(len(ROWS), 0, -1):  # The wire format does not require row order.
        records.append(str(i) + "\t" + hits.get(i, "0\t0\t0"))
    return "\n".join([*records, f"END\t{len(ROWS)}", ""])


class Contract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kcfg-contract-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.campaign = self.root / "campaign"
        self.campaign.mkdir()
        self.output = self.root / "proposal"
        self.trace = self.root / "trace"
        self.scope = "# ID, symbol, module, policy, source\n\n" + "".join(
            f"{i}\tCONFIG_{symbol}\t{module}\t{policy}\t{source}\n"
            for i, (symbol, module, policy, source) in enumerate(ROWS, 1)
        )
        self.coverage = "".join(f"{i}\t2\n" for i in range(1, len(ROWS) + 1) if i != 7)
        for name, data in {
            "baseline.config": BASELINE,
            "scope.tsv": self.scope,
            "coverage.tsv": self.coverage,
            "campaign.seed": "a" * 64 + "\n",
        }.items():
            (self.campaign / name).write_text(data)
        self.bind()

    def bind(self):
        self.digest = make_id(self.campaign)
        self.trace.write_text(trace_for(self.digest, HITS))

    def plan(self, *extra, output=None):
        return subprocess.run(
            [PROGRAM, self.campaign, self.trace, output or self.output, *extra],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )

    def ok(self, *extra):
        proc = self.plan(*extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def refused(self, *extra):
        proc = self.plan(*extra)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(proc.stderr)
        self.assertFalse(os.path.lexists(self.output))
        self.assertEqual(list(self.root.glob(".kcfg-plan-*")), [])

    def result(self, name):
        return (self.output / name).read_text()

    def test_candidate(self):
        self.ok()
        expected = BASELINE.replace(
            "CONFIG_IDLE=y", "# CONFIG_IDLE is not set"
        ).replace("CONFIG_LATE=y", "CONFIG_LATE=m")
        self.assertEqual(self.result("requested.config"), expected)
        self.assertEqual(
            self.result("changes.tsv"), "CONFIG_IDLE\ty\tn\nCONFIG_LATE\ty\tm\n"
        )
        self.assertEqual(self.result("modules-load.late"), "already\nlate-io\n")
        self.assertEqual(self.result("campaign.id"), self.digest + "\n")
        self.assertIn("CONFIG_IDLE\ty\tn\t0\t0\t1\t1\t1\t", self.result("report.tsv"))
        self.assertIn(
            "CONFIG_PARTIAL\ty\ty\t0\t0\t0\t1\t2\tincomplete", self.result("report.tsv")
        )

    def test_multiple_runs_conservative_union(self):
        second = self.root / "second"
        second.write_text(trace_for(self.digest, {3: "0\t1\t0", 5: "1\t0\t0"}))
        self.ok("--also", second)
        self.assertEqual(self.result("requested.config"), BASELINE)
        self.assertEqual(self.result("changes.tsv"), "")
        self.assertEqual(self.result("modules-load.late"), "already\n")

    def test_bad_second_run(self):
        second = self.root / "second"
        second.write_text(trace_for("d" * 64, {}))
        self.refused("--also", second)

    def test_no_coverage_preserves_all(self):
        (self.campaign / "coverage.tsv").write_text("3\t0\n")
        self.ok()
        self.assertEqual(self.result("requested.config"), BASELINE)
        self.assertEqual(self.result("modules-load.late"), "")

    def test_no_module_support(self):
        text = BASELINE.replace("CONFIG_MODULES=y", "# CONFIG_MODULES is not set")
        (self.campaign / "baseline.config").write_text(text + "\n")
        self.bind()
        self.ok()
        self.assertEqual(
            self.result("requested.config"),
            text.replace("CONFIG_IDLE=y", "# CONFIG_IDLE is not set") + "\n",
        )
        self.assertEqual(self.result("modules-load.late"), "")

    def test_invalid_trace(self):
        valid = self.trace.read_text()
        cases = [
            valid[:-1],
            valid + "\n",
            valid.replace("END\t14\n", "END\t13\n"),
            valid.replace("2\t0\t1\t0\n", ""),
            valid.replace("1\t0\t0\t1\n", "2\t0\t0\t1\n"),
            valid.replace("4\t1\t0\t0\n", "65\t1\t0\t0\n"),
            valid.replace("4\t1\t0\t0\n", "4\t2\t0\t0\n"),
            valid.replace("KCFG-TRACE\t1", "KCFG-TRACE\t3"),
            valid.replace(self.digest, "0" * 64),
            valid.replace("5\t0\t1\t0", "5\t0\t1"),
            valid.replace("\n", "\r\n"),
        ]
        for idx, text in enumerate(cases):
            with self.subTest(case=idx):
                self.trace.write_bytes(text.encode())
                self.refused()

    def test_invalid_scope(self):
        variants = [
            "\t../used.c",
            "\t/used.c",
            "\tfs//used.c",
            "\tfs/used.s",
            "\tfs/used/one.c",
        ]
        scopes = [self.scope.replace("\tfs/used/two.c", part) for part in variants]
        scopes += [
            self.scope.replace("idle\tdrop", "late_io\tdrop"),
            self.scope.replace("2\tCONFIG", "1\tCONFIG", 1),
            self.scope.replace("14\tCONFIG", "15\tCONFIG"),
            self.scope.replace("used\tdrop", "used\tdefer", 1),
            self.scope.replace("ROOT\troot", "ROOT\t--flag"),
            self.scope.replace("CONFIG_IDLE", "IDLE"),
            self.scope.replace("drop\tfs/used/one", "remove\tfs/used/one"),
            "# no units\n",
        ]
        for idx, text in enumerate(scopes):
            with self.subTest(case=idx):
                (self.campaign / "scope.tsv").write_text(text)
                self.bind()
                self.refused()

    def test_invalid_coverage(self):
        for row in (
            "1\t4",
            "5\t-1",
            "0\t2",
            "15\t1",
            "1\t9999999999999999999999",
            "x\ty",
            "",
            "8\t1\textra",
        ):
            with self.subTest(row=row):
                (self.campaign / "coverage.tsv").write_text(self.coverage + row + "\n")
                self.refused()

    def test_mismatched_identity(self):
        for name in ("scope.tsv", "baseline.config", "campaign.seed", "campaign.id"):
            with self.subTest(file=name):
                path = self.campaign / name
                original = path.read_bytes()
                altered = (
                    b"c" * 64 + b"\n"
                    if name.startswith("campaign.")
                    else original + b"\n"
                )
                path.write_bytes(altered)
                self.refused()
                path.write_bytes(original)

    def test_malformed_baseline(self):
        for text in (
            BASELINE + "\n# CONFIG_USED is not set\n",
            BASELINE + "\nCONFIG X=y",
            "",
            "CONFIG_X=y\0",
        ):
            with self.subTest(text=text[-20:]):
                (self.campaign / "baseline.config").write_bytes(text.encode())
                self.bind()
                self.refused()

    def test_existing_destination_untouched(self):
        self.output.mkdir()
        self.assertNotEqual(self.plan().returncode, 0)
        self.assertEqual(list(self.output.iterdir()), [])
        marker = self.output / "marker"
        marker.write_text("keep")
        self.assertNotEqual(self.plan().returncode, 0)
        self.assertEqual(marker.read_text(), "keep")
        link = self.root / "link"
        link.symlink_to(self.campaign)
        self.assertNotEqual(self.plan(output=link).returncode, 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual((self.campaign / "baseline.config").read_text(), BASELINE)
        self.assertNotEqual(self.plan(output=self.trace).returncode, 0)
        self.assertEqual(self.trace.read_text(), trace_for(self.digest, HITS))

    def test_concurrent_publish(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.plan(), range(4)))
        self.assertEqual(sum(proc.returncode == 0 for proc in results), 1)
        self.assertEqual(self.result("campaign.id"), self.digest + "\n")
        self.assertEqual(list(self.root.glob(".kcfg-plan-*")), [])

    def test_review_requires_terminal(self):
        self.refused("--review")

    @unittest.skipUnless(
        INTERACTIVE and shutil.which("tmux"), "run with --tmux for PTY interaction"
    )
    def test_review_in_terminal(self):
        socket_root = Path(
            os.environ.get("CLAUDE_TMUX_SOCKET_DIR", "/tmp/claude-tmux-sockets")
        )
        socket_root.mkdir(exist_ok=True, parents=True)
        temp = tempfile.TemporaryDirectory(prefix="kcfg-plan-", dir=socket_root)
        self.addCleanup(temp.cleanup)
        tmux = ["tmux", "-S", str(Path(temp.name) / "test.sock"), "-f", "/dev/null"]

        def launch():
            subprocess.run(
                [
                    *tmux,
                    "new-session",
                    "-d",
                    "-s",
                    "review",
                    "-x",
                    "110",
                    "-y",
                    "32",
                    str(PROGRAM),
                    str(self.campaign),
                    str(self.trace),
                    str(self.output),
                    "--review",
                ],
                check=True,
            )

        def screen():
            return subprocess.run(
                [*tmux, "capture-pane", "-t", "review", "-p"],
                check=False,
                capture_output=True,
                text=True,
            )

        def wait(predicate):
            deadline = time.monotonic() + 6
            while not predicate():
                if time.monotonic() > deadline:
                    self.fail(
                        "Timed out driving FTXUI; last screen: " + screen().stdout
                    )
                time.sleep(0.03)

        def send(*keys):
            subprocess.run([*tmux, "send-keys", "-t", "review", *keys], check=True)

        self.addCleanup(
            lambda: subprocess.run(
                [*tmux, "kill-server"], check=False, capture_output=True
            )
        )
        launch()
        wait(lambda: "Kernel configuration review" in screen().stdout)
        send("q")
        wait(lambda: screen().returncode != 0)
        self.assertFalse(self.output.exists())
        launch()
        wait(lambda: "[x] CONFIG_LATE" in screen().stdout)
        send("End", "Space")
        wait(lambda: "[ ] CONFIG_LATE" in screen().stdout)
        send("s")
        wait(lambda: (self.output / "requested.config").exists())
        self.assertIn("CONFIG_LATE=y", self.result("requested.config"))
        self.assertNotIn("late-io", self.result("modules-load.late"))
        self.assertIn("rejected in review", self.result("report.tsv"))
        self.assertEqual(self.result("changes.tsv"), "CONFIG_IDLE\ty\tn\n")


if __name__ == "__main__":
    unittest.main()
