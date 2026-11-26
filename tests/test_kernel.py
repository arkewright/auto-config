import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CAPTURE = Path(__file__).resolve().parents[1] / "scripts/capture.py"
DATA = runpy.run_path(str(CAPTURE))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="test capture ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.linux = self.root / "linux"
        self.drivers = self.linux / "drivers"
        self.drivers.mkdir(parents=True)
        (self.drivers / "Kbuild").write_text("obj-$(CONFIG_FOO) += foo.o\nobj-$(CONFIG_BAR) += bar.o\n")
        for name in ("foo.c", "bar.c"):
            (self.drivers / name).touch()
        self.base = self.root / "board.config"
        self.base.write_text('CONFIG_MODULES=y\nCONFIG_TRACEPOINTS=y\nCONFIG_BAR=m\nCONFIG_FOO=y\nCONFIG_NAME="board"\n')
        self.campaign = self.root / "campaign"
        self.cc = self.root / "test compiler"
        self.cc.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        self.cc.chmod(0o755)
        self.plugin = self.root / "frontend.so"
        self.obj = self.root / "kernel"
        (self.obj / "include/config").mkdir(parents=True)
        (self.obj / "include/config/kernel.release").write_text("test-profile\n")
        self.nm = self.root / "test nm"
        self.nm.write_text('''#!/bin/sh
[ "$1" = --undefined-only ] || exit 1
IFS= read -r symbol < "$2" && printf ' U %s\\n' "$symbol"
''')
        self.nm.chmod(0o755)
        self.plan = self.root / "plan"
        self.actual = self.root / "normalized.config"

    def call(self, action, *args, check=True):
        return subprocess.run([sys.executable, "-I", CAPTURE, action, "--linux", self.linux, "--campaign", self.campaign,
                               *args], text=True, capture_output=True, check=check)

    def prepare(self, *options, check=True):
        return self.call("prepare", "--cc", self.cc, "--plugin", self.plugin, "--config", self.base, *options, check=check)

    def coverage(self):
        return self.call("coverage", "--obj", self.obj, "--nm", self.nm, "--arch", "riscv")

    def candidate(self, *, arch="riscv", check=True):
        return self.call("candidate", "--plan", self.plan, "--config", self.actual, "--arch", arch, check=check)

    def compiled(self, filename, *, owner, symbol="another_probe"):
        target = self.obj / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(symbol + "\n")
        target.with_name(f".{target.name}.cmd").write_text(f'''savedcmd := clang '-DKBUILD_MODNAME="{owner}"' -c unit.c\n''')

    def test_prepare_numbers_all_discovered_sources(self):
        self.prepare()
        self.assertEqual((self.campaign / "scope.tsv").read_text().splitlines()[1:],
                         ["1\tCONFIG_BAR\tbar\tauto\tdrivers/bar.c", "2\tCONFIG_FOO\tfoo\tauto\tdrivers/foo.c"])
        self.assertEqual((self.campaign / "baseline.config").read_bytes(), self.base.read_bytes())
        self.assertIn("#define KCFG_COUNT 2\n", (self.campaign / "bridge-config.h").read_text())
        key = DATA["read_campaign_id"](self.campaign)
        name = DATA["probe_symbol"](key)
        self.assertIn(f'#define KCFG_LINK_NAME "{name}"\n', (self.campaign / "bridge-config.h").read_text())
        self.assertLessEqual(len(name), 55)

    def test_policy_does_not_filter_observations(self):
        for policy in ("keep", "drop"):
            with self.subTest(policy=policy):
                self.campaign = self.root / policy
                self.prepare("--default-policy", policy)
                units = DATA["read_scope"](self.campaign / "scope.tsv", self.linux)
                self.assertEqual([(u.symbol, u.policy) for u in units], [("CONFIG_BAR", policy), ("CONFIG_FOO", policy)])

    def test_no_external_observation_selectors(self):
        for flag, value in (("--scope", "units.tsv"), ("--drop", "CONFIG_FOO"), ("--defer", "CONFIG_BAR")):
            with self.subTest(flag=flag):
                self.assertIn(f"unrecognized arguments: {flag}", self.prepare(flag, value, check=False).stderr)
                self.assertFalse(self.campaign.exists())

    def test_requires_profile_settings(self):
        self.base.write_text("CONFIG_MODULES=y\n")
        error = self.prepare(check=False)
        self.assertIn("CONFIG_TRACEPOINTS=y", error.stderr)
        self.assertFalse(self.campaign.exists())

    def test_discovery_separates_multi_object_symbols_from_partial_or_ambiguous_bindings(self):
        (self.drivers / "Kbuild").write_text("obj-$(CONFIG_FOO) += first.o \\\n second.o\n"
                                            "obj-$(CONFIG_MIXED) += bar.o composite.o\ncomposite-y := part.o\n"
                                            "obj-$(CONFIG_VENDOR) += vendor/ extra.o\n"
                                            "obj-$(CONFIG_CONDITIONAL) += stable.o\n"
                                            "ifdef CONFIG_ARCH\nobj-$(CONFIG_CONDITIONAL) += optional.o\nendif\n"
                                            "obj-$(CONFIG_SHARED) += shared.o other.o\nobj-y += shared.o\n"
                                            "obj-$(CONFIG_AUX) += aux.o\naggregate-$(CONFIG_AUX) += nested.o\n"
                                            "obj-$(CONFIG_GUARD) += guard.o\nifdef CONFIG_GUARD\nobj-y += nested.o\nendif\n")
        for name in ("first", "second", "part", "extra", "stable", "optional", "shared", "other", "aux", "nested", "guard"):
            (self.drivers / (name + ".c")).touch()
        self.prepare()
        self.assertEqual((self.campaign / "scope.tsv").read_text().splitlines()[1:],
                         ["1\tCONFIG_FOO\t-\tauto\tdrivers/first.c", "2\tCONFIG_FOO\t-\tauto\tdrivers/second.c"])

    def test_compiler_receipts_follow_the_generated_map_only_on_object_compiles(self):
        self.prepare()
        wrapper = self.campaign / "clang"
        def invoke(src, *args):
            return subprocess.check_output([wrapper, *args, self.linux / src], text=True).splitlines()
        selected = invoke("drivers/foo.c", "-c", "-o", "foo.o")
        self.assertIn("-fplugin-arg-kcfgtrace-id=2", selected)
        self.assertIn(f"-fplugin-arg-kcfgtrace-receipt={self.campaign}/receipts/2", selected)
        self.assertIn(f"-fplugin={self.plugin}", selected)
        for args in (("drivers/other/foo.c", "-c", "-o", "foo.o"), ("drivers/foo.c", "-E"),
                     ("drivers/foo.c", "-S", "-o", "foo.s")):
            self.assertFalse(any(arg.startswith("-fplugin") for arg in invoke(*args)))
        self.assertTrue(invoke("init/init_task.c", "-E")[-1].endswith("/driver/bridge.c"))

    def test_bad_campaign_ids_do_not_name_probes(self):
        self.campaign.mkdir()
        key = b"0a" * 32
        for raw in (key, key + b"\r\n", key.upper() + b"\n", key[1:] + b"\n"):
            with self.subTest(raw=raw):
                (self.campaign / "campaign.id").write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "campaign.id"):
                    DATA["read_campaign_id"](self.campaign)

    def test_coverage_needs_compiler_receipt_probe_reference_and_single_owner(self):
        self.prepare()
        probe = DATA["probe_symbol"](DATA["read_campaign_id"](self.campaign))
        self.compiled("drivers/bar.o", owner="bar", symbol=probe)
        self.compiled("drivers/foo.o", owner="foo")
        (self.campaign / "receipts/2").write_text("8\n")
        self.coverage()
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n2\t0\n")
        (self.campaign / "receipts/1").write_text("3\n")
        self.compiled("drivers/foo.o", owner="foo:another", symbol=probe)
        self.coverage()
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t3\n2\t0\n")
        self.compiled("drivers/foo.o", owner="foo", symbol=probe)
        self.coverage()
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t3\n2\t8\n")
        self.assertIn("2\tdrivers/foo.c\tfoo\t8", (self.campaign / "objects.tsv").read_text())
        self.assertEqual(json.loads((self.campaign / "build.json").read_text())["release"], "test-profile")

    def test_omitted_members_by_kbuild_module_or_config_disqualify_a_symbol(self):
        self.prepare()
        probe = DATA["probe_symbol"](DATA["read_campaign_id"](self.campaign))
        self.compiled("drivers/foo.o", owner="foo", symbol=probe)
        (self.campaign / "receipts/2").write_text("1\n")
        self.coverage()
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n2\t1\n")
        self.compiled("drivers/missing.o", owner="foo:shared")
        self.assertIn("drivers/missing.o", self.coverage().stderr)
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n2\t0\n")
        (self.obj / "drivers/.missing.o.cmd").unlink()
        (self.drivers / "vendor").mkdir()
        (self.drivers / "vendor/Kbuild").write_text("obj-$(CONFIG_FOO) += also.o\n")
        (self.drivers / "vendor/also.c").touch()
        self.compiled("drivers/vendor/also.o", owner="also")
        self.assertIn("drivers/vendor/also.o", self.coverage().stderr)
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n2\t0\n")

    def test_config_dependency_outside_observation_is_reported_even_in_observe_only_mode(self):
        self.prepare("--default-policy", "keep")
        probe = DATA["probe_symbol"](DATA["read_campaign_id"](self.campaign))
        self.compiled("drivers/foo.o", owner="foo", symbol=probe)
        (self.campaign / "receipts/2").write_text("1\n")
        self.compiled("kernel/console.o", owner="console")
        cmd = self.obj / "kernel/.console.o.cmd"
        command = cmd.read_text()
        for key in ("FOO", "foo.h"):
            with self.subTest(key=key):
                cmd.write_text(command + f"deps_kernel/console.o := $(wildcard include/config/{key})\n")
                self.assertIn("kernel/console.o", self.coverage().stderr)
                self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n2\t0\n")
        cmd.write_text(command + "deps_kernel/console.o := $(wildcard include/config/FOO_OTHER)\n")
        self.coverage()
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n2\t1\n")

    def reviewed(self):
        self.prepare()
        probe = DATA["probe_symbol"](DATA["read_campaign_id"](self.campaign))
        for i, name in enumerate(("bar", "foo"), 1):
            self.compiled(f"drivers/{name}.o", owner=name, symbol=probe)
            (self.campaign / "receipts" / str(i)).write_text("1\n")
        self.coverage()
        self.plan.mkdir()
        shutil.copyfile(self.campaign / "campaign.id", self.plan / "campaign.id")
        (self.plan / "changes.tsv").write_text("CONFIG_BAR\tm\tn\nCONFIG_FOO\ty\tm\n")
        (self.plan / "modules-load.late").write_text("foo\n")
        self.actual.write_text(self.base.read_text().replace("CONFIG_BAR=m\n", "").replace("CONFIG_FOO=y", "CONFIG_FOO=m"))

    def test_normalized_candidate_matches_reviewed_changes_and_only_those_changes(self):
        self.reviewed()
        self.candidate()
        self.actual.write_text(self.actual.read_text().replace('"board"', '"other"'))
        self.assertIn("CONFIG_NAME", self.candidate(check=False).stderr)

    def test_candidate_honors_archived_policies_and_only_defers_named_modules(self):
        self.reviewed()
        manifest = self.campaign / "scope.tsv"
        current = manifest.read_text()
        archived = current.replace("\tbar\tauto\t", "\tbar\tdrop\t").replace("\tfoo\tauto\t", "\tfoo\tdefer\t")
        manifest.write_text(archived)
        self.candidate()
        for name, rule in (("foo", "drop"), ("foo", "keep"), ("bar", "defer"), ("bar", "keep")):
            with self.subTest(name=name, rule=rule):
                manifest.write_text(current.replace(f"\t{name}\tauto\t", f"\t{name}\t{rule}\t"))
                self.assertIn(f"unauthorized configuration change: CONFIG_{name.upper()}", self.candidate(check=False).stderr)
        manifest.write_text(current.replace("\tfoo\tauto\t", "\t-\tauto\t"))
        self.assertIn("unauthorized configuration change: CONFIG_FOO", self.candidate(check=False).stderr)

    def test_candidate_preserves_capture_attribution_and_board_policy(self):
        self.reviewed()
        self.assertIn("target architecture", self.candidate(arch="arm64", check=False).stderr)
        (self.plan / "campaign.id").write_text("0" * 64 + "\n")
        self.assertIn("different capture", self.candidate(check=False).stderr)
        shutil.copyfile(self.campaign / "campaign.id", self.plan / "campaign.id")
        (self.campaign / "coverage.tsv").write_text("1\t1\n")
        self.assertIn("unauthorized configuration change: CONFIG_FOO", self.candidate(check=False).stderr)
        (self.plan / "changes.tsv").write_text("CONFIG_BAR\tm\tn\nCONFIG_NAME\ty\tn\n")
        self.assertIn("unauthorized configuration change: CONFIG_NAME", self.candidate(check=False).stderr)

    def test_manual_and_forced_edits_can_override_policy_but_not_normalization(self):
        self.reviewed()
        (self.plan / "changes.tsv").write_text("CONFIG_BAR\tm\ty\nCONFIG_FOO\ty\tm\nCONFIG_EXTRA\tn\ty\n")
        (self.plan / "overrides.tsv").write_text("CONFIG_BAR\ty\tmanual\nCONFIG_FOO\tm\tforced\n"
                                                "CONFIG_MODULES\ty\tforced\nCONFIG_EXTRA\ty\tmanual\n")
        actual = self.base.read_text().replace("CONFIG_FOO=y", "CONFIG_FOO=m").replace("CONFIG_BAR=m", "CONFIG_BAR=y")
        self.actual.write_text(actual + "CONFIG_EXTRA=y\n")
        self.assertIn("invalid deferred-module list", self.candidate(check=False).stderr)
        (self.plan / "modules-load.late").write_text("")
        self.candidate()
        self.actual.write_text(actual)
        self.assertIn("CONFIG_EXTRA: y -> n", self.candidate(check=False).stderr)
        self.actual.write_text((actual + "CONFIG_EXTRA=y\n").replace("CONFIG_MODULES=y\n", ""))
        self.assertIn("CONFIG_MODULES: y -> n", self.candidate(check=False).stderr)

    def test_candidate_requires_consistent_tristate_overrides_and_unique_deltas(self):
        self.reviewed()
        overrides = self.plan / "overrides.tsv"
        for row, error in (("bad\tm\tmanual", "invalid explicit setting"),
                           ("CONFIG_FOO\tm\tauto", "invalid explicit setting"),
                           ("CONFIG_NAME\tn\tmanual", "invalid explicit setting"),
                           ("CONFIG_FOO\tx\tforced", "invalid explicit setting"),
                           ("CONFIG_FOO\tm\tforced\nCONFIG_FOO\ty\tmanual", "invalid explicit setting"),
                           ("CONFIG_FOO\ty\tmanual", "unauthorized configuration change"),
                           ("CONFIG_EXTRA\ty\tforced", "explicit setting missing")):
            with self.subTest(row=row):
                overrides.write_text(row + "\n")
                self.assertIn(error, self.candidate(check=False).stderr)
        overrides.write_text("CONFIG_EXTRA\tn\tforced\n")
        self.candidate()
        delta = self.plan / "changes.tsv"
        delta.write_text(delta.read_text() + "CONFIG_BAR\tm\tn\n")
        self.assertIn("unauthorized configuration change: CONFIG_BAR", self.candidate(check=False).stderr)

    def test_candidate_requires_the_reviewed_deferred_modules(self):
        self.reviewed()
        for late in ("", "other\n", "foo\nfoo\n", "foo\nother\n"):
            with self.subTest(late=late):
                (self.plan / "modules-load.late").write_text(late)
                self.assertIn("invalid deferred-module list", self.candidate(check=False).stderr)


if __name__ == "__main__":
    unittest.main()
