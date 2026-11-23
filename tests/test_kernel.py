import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BUILD = runpy.run_path(str(TOOLS / "kernel.py"))


class SourceTests(unittest.TestCase):
    def test_external_linux_tree_skips_default_clone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache, board = root / "cache", root / "vendor"
            (cache / "ftxui").mkdir(parents=True)
            (cache / "ftxui/CMakeLists.txt").touch()
            board.mkdir()
            (board / "Makefile").touch()
            env = dict(os.environ, LINUX=str(board), GIT_ALLOW_PROTOCOL="file")
            subprocess.run([sys.executable, "-I", TOOLS / "sources.py", "--cache", cache], env=env, check=True)
            self.assertFalse((cache / "linux").exists())

    @unittest.skipUnless(shutil.which("git"), "cloning requires Git")
    def test_ref_selects_new_clone_but_preserves_dirty_and_archived_trees(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origin, cache = root / "origin", root / "cache"
            origin.mkdir()
            (cache / "ftxui").mkdir(parents=True)
            (cache / "ftxui/CMakeLists.txt").touch()
            gitconfig = root / "gitconfig"
            gitconfig.write_text('[user]\nname = Test\nemail = test@example.invalid\n'
                                 f'[url "{origin.as_uri()}"]\ninsteadOf = https://github.com/torvalds/linux.git\n'
                                 'insteadOf = https://github.com/llvm/llvm-project.git\n')
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "LINUX"}
            env.update(GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1", GIT_ALLOW_PROTOCOL="file", LLVM_REF="board")

            def run(*args):
                subprocess.run(args, cwd=origin, check=True, env=env)

            run("git", "init", "-q", "-b", "upstream")
            (origin / "Makefile").write_text("board\n")
            (origin / "llvm").mkdir()
            (origin / "llvm/CMakeLists.txt").touch()
            run("git", "add", "Makefile", "llvm/CMakeLists.txt")
            run("git", "commit", "-qm", "board")
            run("git", "branch", "board")
            (origin / "Makefile").write_text("upstream\n")
            run("git", "commit", "-qam", "upstream")
            fetch = (sys.executable, "-I", TOOLS / "sources.py", "--cache", cache, "--llvm-source", "--linux-ref")
            run(*fetch, "board")
            self.assertEqual((cache / "llvm-project/Makefile").read_text(), "board\n")
            tree = cache / "linux"
            self.assertEqual((tree / "Makefile").read_text(), "board\n")
            (tree / "Makefile").write_text("vendor edits\n")
            run(*fetch, "nonexistent-ref")
            self.assertEqual((tree / "Makefile").read_text(), "vendor edits\n")
            (tree / ".git").rename(root / "metadata")
            run(*fetch, "nonexistent-ref")
            self.assertEqual((tree / "Makefile").read_text(), "vendor edits\n")


class BuilderTests(unittest.TestCase):
    def test_toolchain_uses_selected_compiler_and_untagged_kernel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Makefile").write_text("kernelversion:\n\t@printf '%s\\n' 6.12-board\n")
            sdk = root / "sdk"
            (sdk / "bin").mkdir(parents=True)
            clang = sdk / "bin/clang"
            clang.write_text("#!/bin/sh\nprintf '%s\\n' 'clang version 23.1.5'\n")
            clang.chmod(0o755)
            args = SimpleNamespace(linux=root, llvm=sdk, cc=None, arch="riscv", jobs=1,
                                   hostcc=None, hostcxx=None, host_sysroot=None)
            kit = BUILD["Toolchain"](args)
            self.assertEqual(kit.cc, str(clang))
            self.assertEqual(kit.kernel_version(), "6.12-board")
            args.cc = str(sdk / "bin/missing")
            with self.assertRaisesRegex(ValueError, "Clang not found"):
                BUILD["Toolchain"](args)
            args.cc, args.llvm = None, root / "empty-sdk"
            with patch.dict(os.environ, PATH=str(clang.parent)), self.assertRaisesRegex(ValueError, "Clang not found"):
                BUILD["Toolchain"](args)

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

    def test_capture_identifier_has_one_wire_format(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key = b"0a" * 32
            path = root / "campaign.id"
            path.write_bytes(key + b"\n")
            self.assertEqual(BUILD["read_campaign_id"](root), key.decode())
            for invalid in (key, key + b"\r\n", key.upper() + b"\n", key[1:] + b"\n"):
                with self.subTest(invalid=invalid):
                    path.write_bytes(invalid)
                    with self.assertRaisesRegex(ValueError, "invalid campaign.id"):
                        BUILD["read_campaign_id"](root)

    def test_candidate_rejects_mixed_capture_or_target_before_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            campaign, plan = root / "campaign", root / "plan"
            campaign.mkdir()
            plan.mkdir()
            (root / "drivers").mkdir()
            (root / "drivers/board.c").touch()
            (campaign / "scope.tsv").write_text("1\tCONFIG_BOARD\tboard\tkeep\tdrivers/board.c\n")
            key, other = "a" * 64, "b" * 64
            (campaign / "campaign.id").write_text(key + "\n")
            args = SimpleNamespace(campaign=campaign, plan=plan, out=root / "candidate")
            kit = SimpleNamespace(linux=root, arch="riscv", kernel_version=lambda: "6.12-board")
            cases = ((other, key, "riscv", "6.12-board", "mismatched campaign"),
                     (key, other, "riscv", "6.12-board", "different capture campaign"),
                     (key, key, "arm64", "6.12-board", "architecture changed"),
                     (key, key, "riscv", "6.11-board", "kernel version changed"))
            for build_id, plan_id, arch, version, error in cases:
                with self.subTest(error=error):
                    build = {"id": build_id, "arch": arch, "kernel_version": version}
                    (campaign / "build.json").write_text(json.dumps(build))
                    (plan / "campaign.id").write_text(plan_id + "\n")
                    with self.assertRaisesRegex(ValueError, error):
                        BUILD["candidate"](args, kit)
                    self.assertFalse(args.out.exists())

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
