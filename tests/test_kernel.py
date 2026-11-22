import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

BUILD = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/kernel.py"))


class BuilderTests(unittest.TestCase):
    def test_kconfig_coercion_is_not_accepted(self):
        compare = BUILD["same_config"]
        compare({"CONFIG_A": "n"}, {}, "normalization")
        for wanted, got in (({"CONFIG_A": "m"}, {"CONFIG_A": "y"}), ({}, {"CONFIG_UNRELATED": "y"})):
            with self.assertRaisesRegex(ValueError, "normalization"):
                compare(wanted, got, "normalization")

    def test_discovery_only_suggests_literal_unconditional_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Makefile").write_text("obj-$(CONFIG_A) += first.o \\\n second.o\n"
                                          "ifneq ($(ARCH),x86)\nobj-$(CONFIG_B) += other.o\nendif\n")
            for stem in ("first", "second", "other"):
                (root / (stem + ".c")).touch()
            self.assertEqual(BUILD["literal_bindings"](root),
                             {root / "first.c": {"CONFIG_A"}, root / "second.c": {"CONFIG_A"}})

    def test_compiler_selects_exactly_the_mapped_target(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            compiler = p / "compiler"
            compiler.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            compiler.chmod(0o755)
            kit = SimpleNamespace(linux=p / "linux", cc=compiler)
            rows = {1: ("CONFIG_FOO", "foo", "keep", "drivers/foo.c")}
            wrapper = BUILD["compiler_wrapper"](kit, p, p / "plugin.so", rows)

            def invoke(name):
                return subprocess.check_output([wrapper, "-c", "-o", "unit.o", kit.linux / name], text=True).splitlines()

            self.assertIn("-fplugin-arg-kcfgtrace-id=1", invoke("drivers/foo.c"))
            self.assertFalse(any(a.startswith("-fplugin") for a in invoke("drivers/other/foo.c")))
            self.assertTrue(invoke("init/init_task.c")[-1].endswith("/driver/bridge.c"))

    def test_changes_require_policy_and_a_verified_object(self):
        rows = {1: ("CONFIG_FOO", "foo", "drop", "drivers/foo.c")}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "baseline.config").write_text("CONFIG_FOO=y\nCONFIG_BAR=y\n")
            (root / "coverage.tsv").write_text("1\t0\n")
            (root / "changes.tsv").write_text("CONFIG_FOO\ty\tn\n")
            (root / "requested.config").write_text("# CONFIG_FOO is not set\nCONFIG_BAR=y\n")
            with self.assertRaisesRegex(ValueError, "unauthorized"):
                BUILD["requested_values"](root, root, rows)
            (root / "coverage.tsv").write_text("1\t1\n")
            self.assertEqual(BUILD["requested_values"](root, root, rows)["CONFIG_FOO"], "n")
            (root / "requested.config").write_text("# CONFIG_FOO is not set\n# CONFIG_BAR is not set\n")
            with self.assertRaisesRegex(ValueError, "approved changes"):
                BUILD["requested_values"](root, root, rows)

    def test_unlisted_compiled_sibling_blocks_the_entire_option(self):
        with tempfile.TemporaryDirectory() as temp:
            obj = Path(temp)
            (obj / "drivers").mkdir()
            cmd = "savedcmd := clang -DKBUILD_MODNAME='\"combined\"' -c file.c\n"
            (obj / "drivers/.first.o.cmd").write_text(cmd)
            (obj / "drivers/.second.o.cmd").write_text(cmd)
            rows = {1: ("CONFIG_COMBINED", "combined", "drop", "drivers/first.c")}
            self.assertIn("CONFIG_COMBINED", BUILD["omitted_members"](obj, rows))
            rows[2] = ("CONFIG_COMBINED", "combined", "drop", "drivers/second.c")
            self.assertEqual(BUILD["omitted_members"](obj, rows), {})

    def test_kbuild_shared_or_absent_owner_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            obj = Path(temp) / "common.o"
            cmd = Path(temp) / ".common.o.cmd"
            self.assertIsNone(BUILD["object_owner"](obj))
            cmd.write_text('savedcmd := cc \'-DKBUILD_MODNAME="foo:bar"\' -c common.c\n')
            self.assertIsNone(BUILD["object_owner"](obj))
            cmd.write_text('savedcmd := cc \'-DKBUILD_MODNAME="foo"\' -c common.c\n')
            self.assertEqual(BUILD["object_owner"](obj), "foo")


if __name__ == "__main__":
    unittest.main()
