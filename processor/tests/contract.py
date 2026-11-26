"""Exercise the shipped executable, including failure-before-publication cases."""

import concurrent.futures
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
SWITCHES = {
    "MODULES": "bool", "USED": "tristate", "IDLE": "bool", "ROOT": "tristate",
    "LATE": "tristate", "PARTIAL": "bool", "ALWAYS": "bool", "ALREADY": "tristate",
    "OFF": "bool", "NO_MODULE": "tristate", "UNRELATED": "bool", "ABSENT": "bool",
}
GRAPH = "KCFG-GRAPH\t1\nmodules\tCONFIG_MODULES\n" + "".join(
    f"symbol\tCONFIG_{name}\t{kind}\n" for name, kind in SWITCHES.items()
)
HITS = {1: "0\t0\t1", 2: "0\t1\t0", 3: "0\t0\t1", 4: "1\t0\t0", 5: "0\t1\t0"}
CAMPAIGN_ID = "0123456789abcdef" * 4
OTHER_CAMPAIGN_ID = "d" * 64


def trace_for(campaign_id, hits):
    records = [f"KCFG-TRACE\t1\t{campaign_id}"]
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
            "campaign.id": CAMPAIGN_ID + "\n",
            "dependencies.tsv": GRAPH,
        }.items():
            (self.campaign / name).write_text(data)
        self.trace.write_text(trace_for(CAMPAIGN_ID, HITS))

    def plan(self, *extra, output=None, batch=True):
        flags = ["--batch"] if batch else []
        return subprocess.run(
            [PROGRAM, self.campaign, self.trace, output or self.output, *flags, *extra],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )

    def ok(self, *extra):
        proc = self.plan(*extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def refused(self, *extra, diagnostic=None):
        proc = self.plan(*extra)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(proc.stderr)
        if diagnostic is not None:
            self.assertIn(diagnostic, proc.stderr)
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
        self.assertEqual(self.result("campaign.id"), CAMPAIGN_ID + "\n")
        self.assertIn("CONFIG_IDLE\ty\tn\t0\t0\t1\t1\t1\t", self.result("report.tsv"))
        self.assertIn(
            "CONFIG_PARTIAL\ty\ty\t0\t0\t0\t1\t2\tincomplete", self.result("report.tsv")
        )

    def test_multiple_runs_conservative_union(self):
        second = self.root / "second"
        second.write_text(trace_for(CAMPAIGN_ID, {3: "0\t1\t0", 5: "1\t0\t0"}))
        self.ok("--also", second)
        self.assertEqual(self.result("requested.config"), BASELINE)
        self.assertEqual(self.result("changes.tsv"), "")
        self.assertEqual(self.result("modules-load.late"), "already\n")

    def test_primary_trace_from_different_campaign(self):
        self.trace.write_text(trace_for(OTHER_CAMPAIGN_ID, HITS))
        self.refused(diagnostic="wrong trace version or campaign ID")

    def test_additional_trace_from_different_campaign(self):
        second = self.root / "second"
        second.write_text(trace_for(OTHER_CAMPAIGN_ID, {}))
        self.refused("--also", second, diagnostic="wrong trace version or campaign ID")

    def test_no_coverage_preserves_all(self):
        (self.campaign / "coverage.tsv").write_text("3\t0\n")
        self.ok()
        self.assertEqual(self.result("requested.config"), BASELINE)
        self.assertEqual(self.result("modules-load.late"), "")

    def test_no_module_support(self):
        text = BASELINE.replace("CONFIG_MODULES=y", "# CONFIG_MODULES is not set").replace("=m\n", "=y\n")
        (self.campaign / "baseline.config").write_text(text + "\n")
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
            valid.replace(CAMPAIGN_ID, CAMPAIGN_ID.upper()),
            valid.replace(CAMPAIGN_ID, CAMPAIGN_ID + " "),
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

    def test_missing_campaign_id(self):
        (self.campaign / "campaign.id").unlink()
        self.refused(diagnostic="campaign.id:")

    def test_malformed_campaign_id(self):
        for text in (
            "",
            CAMPAIGN_ID,
            CAMPAIGN_ID[:-1] + "\n",
            CAMPAIGN_ID + "\n\n",
            CAMPAIGN_ID + "\r\n",
            CAMPAIGN_ID.upper() + "\n",
            CAMPAIGN_ID[:-1] + "g\n",
            CAMPAIGN_ID[:-1] + " \n",
        ):
            with self.subTest(text=text):
                (self.campaign / "campaign.id").write_bytes(text.encode())
                self.refused(diagnostic="campaign.id:")

    def test_id_does_not_bind_valid_scope_contents(self):
        (self.campaign / "scope.tsv").write_text(
            self.scope.replace("idle\tdrop", "idle\tkeep")
        )
        self.ok()
        self.assertEqual(self.result("changes.tsv"), "CONFIG_LATE\ty\tm\n")
        self.assertEqual(self.result("campaign.id"), CAMPAIGN_ID + "\n")

    def test_malformed_baseline(self):
        for text in (
            BASELINE + "\n# CONFIG_USED is not set\n",
            BASELINE + "\nCONFIG X=y",
            "",
            "CONFIG_X=y\0",
        ):
            with self.subTest(text=text[-20:]):
                (self.campaign / "baseline.config").write_bytes(text.encode())
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
        self.assertEqual(self.trace.read_text(), trace_for(CAMPAIGN_ID, HITS))

    def test_concurrent_publish(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.plan(), range(4)))
        self.assertEqual(sum(proc.returncode == 0 for proc in results), 1)
        self.assertEqual(self.result("campaign.id"), CAMPAIGN_ID + "\n")
        self.assertEqual(list(self.root.glob(".kcfg-plan-*")), [])

    def with_rules(self, *rules):
        graph = self.root / "parser-export.tsv"
        graph.write_text(GRAPH + "".join(row + "\n" for row in rules))
        return graph

    def force(self, opinions):
        path = self.root / "opinions.config"
        path.write_text(opinions)
        return path

    def test_default_requires_terminal(self):
        proc = self.plan(batch=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("interactive terminal", proc.stderr)
        self.assertFalse(self.output.exists())

    def test_missing_or_incomplete_graph(self):
        path = self.campaign / "dependencies.tsv"
        path.unlink()
        self.refused(diagnostic="dependencies.tsv")
        path.write_text(GRAPH.replace("symbol\tCONFIG_USED\ttristate\n", ""))
        self.refused(diagnostic="CONFIG_USED")
        path.write_text(GRAPH + "depends\tCONFIG_USED\tCONFIG_IDLE ||\n")
        self.refused(diagnostic="dependencies.tsv")

    def test_forced_values_are_an_explicit_authority(self):
        opinions = self.force("# final board choices\nCONFIG_USED=n\nCONFIG_IDLE=y\nCONFIG_LATE=m\nCONFIG_ABSENT=y\nCONFIG_OFF=y\n")
        proc = self.plan("--force", opinions)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CONFIG_USED: locked by forced opinion; overrides recorded use", proc.stderr)
        self.assertEqual(self.result("overrides.tsv").splitlines(), [
            "CONFIG_ABSENT\ty\tforced", "CONFIG_IDLE\ty\tforced", "CONFIG_LATE\tm\tforced",
            "CONFIG_OFF\ty\tforced", "CONFIG_USED\tn\tforced",
        ])
        config = self.result("requested.config")
        for item in ("CONFIG_IDLE=y", "CONFIG_LATE=m", "CONFIG_ABSENT=y", "CONFIG_OFF=y", "# CONFIG_USED is not set"):
            self.assertIn(item, config.splitlines())
        self.assertIn("CONFIG_ABSENT\tn\ty\n", self.result("changes.tsv"))
        self.assertEqual(self.result("modules-load.late"), "already\n")

    def test_invalid_opinions_do_not_publish(self):
        for value in ("CONFIG_USES=y", "CONFIG_NUM=n", "CONFIG_IDLE=m", "CONFIG_IDLE=maybe", "CONFIG_IDLE=y\nCONFIG_IDLE=n"):
            with self.subTest(opinions=value):
                self.refused("--force", self.force(value))

    def test_force_dependency_conflict_is_not_applied(self):
        graph = self.with_rules("depends\tCONFIG_USED\tCONFIG_IDLE")
        self.refused("--graph", graph, "--force", self.force("CONFIG_USED=y\nCONFIG_IDLE=n\n"), diagnostic="cannot apply")

    def test_dependency_chain_protects_zero_hit_prerequisites(self):
        graph = self.with_rules("depends\tCONFIG_USED\tCONFIG_IDLE", "depends\tCONFIG_IDLE\tCONFIG_ALWAYS")
        (self.campaign / "scope.tsv").write_text(self.scope.replace("keep\tdrivers/always", "drop\tdrivers/always"))
        self.ok("--graph", graph)
        self.assertEqual(self.result("changes.tsv"), "CONFIG_LATE\ty\tm\n")
        self.assertIn("CONFIG_IDLE\ty\ty\t0\t0\t1\t1\t1\tretained:", self.result("report.tsv"))

    def test_drop_unused_parent_and_child_together(self):
        graph = self.with_rules("depends\tCONFIG_ALWAYS\tCONFIG_IDLE")
        (self.campaign / "scope.tsv").write_text(self.scope.replace("keep\tdrivers/always", "drop\tdrivers/always"))
        self.ok("--graph", graph)
        self.assertEqual(self.result("changes.tsv").splitlines(), [
            "CONFIG_ALWAYS\ty\tn", "CONFIG_IDLE\ty\tn", "CONFIG_LATE\ty\tm",
        ])

    def test_disjunction_retains_one_available_alternative(self):
        graph = self.with_rules("depends\tCONFIG_USED\tCONFIG_ALWAYS CONFIG_IDLE ||")
        (self.campaign / "scope.tsv").write_text(self.scope.replace("keep\tdrivers/always", "drop\tdrivers/always"))
        self.ok("--graph", graph)
        config = self.result("requested.config").splitlines()
        self.assertIn("CONFIG_USED=y", config)
        self.assertEqual(sum(f"CONFIG_{symbol}=y" in config for symbol in ("ALWAYS", "IDLE")), 1)

    def test_unreachable_branch_does_not_protect_unused_option(self):
        graph = self.with_rules("depends\tCONFIG_USED\tCONFIG_IDLE CONFIG_OFF && CONFIG_ALWAYS ||")
        (self.campaign / "scope.tsv").write_text(self.scope.replace("keep\tdrivers/always", "drop\tdrivers/always"))
        self.ok("--graph", graph)
        self.assertEqual(self.result("changes.tsv"), "CONFIG_IDLE\ty\tn\nCONFIG_LATE\ty\tm\n")

    def test_conditional_select_evaluates_current_config(self):
        graph = self.with_rules("select\tCONFIG_USED\tCONFIG_IDLE\tCONFIG_UNRELATED")
        self.ok("--graph", graph)
        self.assertIn("CONFIG_IDLE=y", self.result("requested.config").splitlines())
        other = self.root / "after"
        proc = self.plan("--graph", graph, "--force", self.force("CONFIG_UNRELATED=n"), output=other)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("# CONFIG_IDLE is not set", (other / "requested.config").read_text().splitlines())

    def test_batch_ignores_interactive_draft(self):
        self.output.with_suffix(".draft").write_text("unrelated draft")
        self.ok()
        self.assertIn("CONFIG_IDLE\ty\tn\n", self.result("changes.tsv"))

    @unittest.skipUnless(
        INTERACTIVE and shutil.which("tmux"), "run with --tmux for PTY interaction"
    )
    def test_editor_in_terminal(self):
        socket_root = Path(
            os.environ.get("CLAUDE_TMUX_SOCKET_DIR", "/tmp/claude-tmux-sockets")
        )
        socket_root.mkdir(exist_ok=True, parents=True)
        temp = tempfile.TemporaryDirectory(prefix="kcfg-plan-", dir=socket_root)
        self.addCleanup(temp.cleanup)
        tmux = ["tmux", "-S", str(Path(temp.name) / "test.sock"), "-f", "/dev/null"]

        opinions = self.force("CONFIG_IDLE=y\n")
        graph = self.with_rules("depends\tCONFIG_USED\tCONFIG_ALWAYS")

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
                    "--force", str(opinions), "--graph", str(graph),
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
        def shown(text):
            wait(lambda: text in screen().stdout)

        def row(before, current, symbol):
            wait(lambda: any(line.split()[:5] == [">", "*", before, current, symbol]
                             for line in screen().stdout.splitlines()))

        launch()
        shown("Kernel configuration editor")
        send("q")
        wait(lambda: screen().returncode != 0)
        self.assertFalse(self.output.exists())

        launch()
        shown("Kernel configuration editor")
        send("?")
        shown("Configuration editor help")
        send("?", "/idle", "Enter", "i", "n")
        shown("CONFIG_IDLE is locked by forced opinion at y")
        send("a")
        shown("Analysis complete")
        send("/unrelated", "Enter", "i", "n")
        row("y", "n", "CONFIG_UNRELATED")
        send("s")
        shown("Draft saved")
        self.assertFalse(self.output.exists())
        self.assertIn("CONFIG_UNRELATED=n\n", self.output.with_suffix(".draft").read_text())
        send("r")
        shown("Manual choice released")
        send("u")
        row("y", "n", "CONFIG_UNRELATED")
        send("/always", "Enter", "i", "n", "A")
        shown("cannot apply:")
        self.assertFalse(self.output.exists())
        send("c", "u")
        shown("Undone")
        send(":w", "Enter")
        shown("Draft saved")
        send("/unrelated", "Enter", "i", "y", "q")
        shown("Discard unsaved edits?")
        send("y")
        wait(lambda: screen().returncode != 0)
        self.assertFalse(self.output.exists())

        launch()
        shown("Kernel configuration editor")
        send("/unrelated", "Enter")
        row("y", "n", "CONFIG_UNRELATED")
        send("/does_not_exist", "Enter")
        shown("No matching options")
        send("Escape")
        wait(lambda: "No matching options" not in screen().stdout)
        send("d", "g", "g", "j", "G", "k", ":wq", "Enter")
        wait(lambda: (self.output / "requested.config").exists())
        self.assertEqual(self.result("changes.tsv"), "CONFIG_LATE\ty\tm\nCONFIG_UNRELATED\ty\tn\n")
        self.assertEqual(self.result("overrides.tsv"), "CONFIG_IDLE\ty\tforced\nCONFIG_UNRELATED\tn\tmanual\n")
        self.assertIn("CONFIG_IDLE=y", self.result("requested.config").splitlines())
        self.assertEqual(self.result("modules-load.late"), "already\nlate-io\n")


if __name__ == "__main__":
    unittest.main()
