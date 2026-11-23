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

    def test_prepare_default_observes_without_authorizing_changes(self):
        self.prepare()
        self.assertEqual((self.campaign / "scope.tsv").read_text().splitlines()[1:],
                         ["1\tCONFIG_BAR\tbar\tkeep\tdrivers/bar.c", "2\tCONFIG_FOO\tfoo\tkeep\tdrivers/foo.c"])
        self.assertEqual((self.campaign / "baseline.config").read_bytes(), self.base.read_bytes())
        self.assertIn("#define KCFG_COUNT 2\n", (self.campaign / "bridge-config.h").read_text())
        key = DATA["read_campaign_id"](self.campaign)
        name = DATA["probe_symbol"](key)
        self.assertIn(f'#define KCFG_LINK_NAME "{name}"\n', (self.campaign / "bridge-config.h").read_text())
        self.assertLessEqual(len(name), 55)

    def test_requires_profile_settings_and_unambiguous_policy(self):
        for opts in (("--drop", "CONFIG_MISSING"), ("--defer", "CONFIG_FOO", "--drop", "CONFIG_FOO")):
            with self.subTest(opts=opts):
                self.assertNotEqual(self.prepare(*opts, check=False).returncode, 0)
                self.assertFalse(self.campaign.exists())
        self.base.write_text("CONFIG_MODULES=y\n")
        error = self.prepare(check=False)
        self.assertIn("CONFIG_TRACEPOINTS=y", error.stderr)
        self.assertFalse(self.campaign.exists())

    def test_discovery_handles_continuations_and_ignores_conditional_rules(self):
        (self.drivers / "Kbuild").write_text("obj-$(CONFIG_FOO) += first.o \\\n second.o\n"
                                            "ifneq ($(ARCH),x86)\nobj-$(CONFIG_FOO) += other.o\nendif\n")
        for name in ("first.c", "second.c", "other.c"):
            (self.drivers / name).touch()
        self.assertIn("no unique module", self.prepare("--defer", "CONFIG_FOO", check=False).stderr)
        self.prepare("--drop", "CONFIG_FOO")
        self.assertEqual((self.campaign / "scope.tsv").read_text().splitlines()[1:],
                         ["1\tCONFIG_FOO\t-\tdrop\tdrivers/first.c", "2\tCONFIG_FOO\t-\tdrop\tdrivers/second.c"])

    def test_explicit_scope_and_compiler_receipts_follow_only_mapped_object_compiles(self):
        scope = self.root / "scope.tsv"
        scope.write_text("# fixture\n1\tCONFIG_FOO\tfoo\tdefer\tdrivers/foo.c\n")
        self.assertIn("choose a scope file", self.prepare("--scope", scope, "--drop", "CONFIG_BAR", check=False).stderr)
        self.prepare("--scope", scope)
        self.assertEqual((self.campaign / "scope.tsv").read_bytes(), scope.read_bytes())
        wrapper = self.campaign / "clang"
        def invoke(src, *args):
            return subprocess.check_output([wrapper, *args, self.linux / src], text=True).splitlines()
        selected = invoke("drivers/foo.c", "-c", "-o", "foo.o")
        self.assertIn("-fplugin-arg-kcfgtrace-id=1", selected)
        self.assertIn(f"-fplugin-arg-kcfgtrace-receipt={self.campaign}/receipts/1", selected)
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
        self.prepare("--drop", "CONFIG_FOO", "--defer", "CONFIG_BAR")
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
        self.prepare("--drop", "CONFIG_FOO")
        probe = DATA["probe_symbol"](DATA["read_campaign_id"](self.campaign))
        self.compiled("drivers/foo.o", owner="foo", symbol=probe)
        (self.campaign / "receipts/1").write_text("1\n")
        self.compiled("drivers/missing.o", owner="foo:shared")
        self.assertIn("drivers/missing.o", self.coverage().stderr)
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n")
        (self.obj / "drivers/.missing.o.cmd").unlink()
        (self.drivers / "vendor").mkdir()
        (self.drivers / "vendor/Kbuild").write_text("obj-$(CONFIG_FOO) += also.o\n")
        (self.drivers / "vendor/also.c").touch()
        self.compiled("drivers/vendor/also.o", owner="also")
        self.assertIn("drivers/vendor/also.o", self.coverage().stderr)
        self.assertEqual((self.campaign / "coverage.tsv").read_text(), "1\t0\n")

    def reviewed(self):
        self.prepare("--drop", "CONFIG_BAR", "--defer", "CONFIG_FOO")
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

    def test_candidate_requires_the_reviewed_deferred_modules(self):
        self.reviewed()
        for late in ("", "other\n", "foo\nfoo\n", "foo\nother\n"):
            with self.subTest(late=late):
                (self.plan / "modules-load.late").write_text(late)
                self.assertIn("invalid deferred-module list", self.candidate(check=False).stderr)


if __name__ == "__main__":
    unittest.main()
