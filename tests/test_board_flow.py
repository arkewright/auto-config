import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = runpy.run_path(str(ROOT / "tools/kernel.py"))


class BoardHostTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "DEPS": str(self.root / "deps"),
                    "WORK": str(self.root / "work"), "HOST_BUILD": str(self.root / "host"),
                    "SDK": str(self.root / "sdk"), "LINUX": str(self.root / "linux"),
                    "GIT_ALLOW_PROTOCOL": "file", "PYTHON": sys.executable}
        self.sdk = self.root / "sdk/bin"
        self.sdk.mkdir(parents=True)
        for tool in ("clang", "ld.lld", "llvm-config"):
            self.executable(self.sdk / tool, '#!/bin/sh\nprintf "%s\\n" "$SDK/lib/cmake/llvm"\n')
        self.linux = self.root / "linux"
        self.linux.mkdir()
        (self.linux / "Makefile").touch()

    def executable(self, path, script):
        path.write_text(script)
        path.chmod(0o755)

    def cli(self, *args, check=True):
        return subprocess.run([ROOT / "kcfg", *args], env=self.env, cwd=self.root,
                              text=True, capture_output=True, check=check)

    def test_default_fetch_prepares_all_three_sources_and_reuses_local_edits(self):
        origin = self.root / "upstream"
        origin.mkdir()
        del self.env["LINUX"]
        self.env.update(SDK=str(self.root / "new-sdk"), LINUX_REF="board", LLVM_REF="board", FTXUI_REF="board",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"))
        repos = ("torvalds/linux", "llvm/llvm-project", "ArthurSonzogni/FTXUI")
        (self.root / "gitconfig").write_text(f'[url "{origin.as_uri()}"]\n' +
                                           "".join(f"\tinsteadOf = https://github.com/{repo}.git\n" for repo in repos))
        def git(*args):
            subprocess.run(["git", *args], env=self.env, cwd=origin, check=True, capture_output=True)
        git("init", "-b", "board")
        (origin / "llvm").mkdir()
        for entry in ("Makefile", "CMakeLists.txt", "llvm/CMakeLists.txt"):
            (origin / entry).touch()
        git("add", ".")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        self.cli("fetch")
        deps = Path(self.env["DEPS"])
        for path in ("linux/Makefile", "ftxui/CMakeLists.txt", "llvm-project/llvm/CMakeLists.txt"):
            self.assertTrue((deps / path).is_file())
        (deps / "linux/Makefile").write_text("vendor change\n")
        self.env.update(LINUX_REF="missing", LLVM_REF="missing", FTXUI_REF="missing")
        self.cli("fetch")
        self.assertEqual((deps / "linux/Makefile").read_text(), "vendor change\n")

    def test_tools_reuse_external_sdk_and_installed_ftxui_without_fetch(self):
        self.env["FTXUI_DIR"] = str(self.root / "installed ftxui")
        self.executable(self.sdk / "cmake", '#!/bin/sh\nprintf "%s\\n" "$@" >> "$HOME/cmake-log"\n')
        self.cli("tools")
        args = (self.root / "cmake-log").read_text().splitlines()
        self.assertIn(f"-Dftxui_DIR={self.env['FTXUI_DIR']}", args)
        self.assertIn("-DFTXUI_SOURCE_DIR=", args)
        self.assertIn(f"-DClang_DIR={self.sdk.parent}/lib/cmake/clang", args)
        self.assertNotIn("install", args)
        self.assertFalse((self.root / "deps").exists())

    def test_tools_build_missing_sdk_from_selected_source_and_use_selected_ftxui(self):
        (self.sdk / "llvm-config").unlink()
        source, tui = self.root / "llvm src", self.root / "tui"
        (source / "llvm").mkdir(parents=True)
        (source / "llvm/CMakeLists.txt").touch()
        tui.mkdir()
        (tui / "CMakeLists.txt").touch()
        self.env.update(LLVM_SOURCE=str(source), FTXUI_SOURCE_DIR=str(tui))
        self.executable(self.sdk / "cmake", '''#!/bin/sh
printf '%s\\n' "$@" >> "$HOME/cmake-log"
if [ "$1" = --build ] && [ "$6" = install ]; then ln -s clang "$SDK/bin/llvm-config"; fi
''')
        self.cli("tools")
        args = (self.root / "cmake-log").read_text().splitlines()
        self.assertIn(str(source / "llvm"), args)
        self.assertIn("-DLLVM_TARGETS_TO_BUILD=X86;RISCV;BPF", args)
        self.assertIn(f"-DFTXUI_SOURCE_DIR={tui}", args)
        self.assertFalse((self.root / "deps").exists())

    def test_native_config_is_unconstrained_and_board_edits_are_reported(self):
        (self.linux / "Makefile").write_text('''allyesconfig:
\t@test "$(origin KCONFIG_ALLCONFIG)" = undefined && test ! -f "$(O)/.config"
\t@printf '%s\\n' CONFIG_MODULES=y CONFIG_TRACEPOINTS=y CONFIG_TRIM_UNUSED_KSYMS=y CONFIG_BOARD=y > "$(O)/.config"
olddefconfig:
\t@test -f "$(O)/native.config"
''')
        fragment = self.root / "board.config"
        fragment.write_text("# CONFIG_BOARD is not set\n")
        self.env.update(BOARD_CONFIG=str(fragment), KCONFIG_ALLCONFIG="unrelated.fragment")
        self.cli("config")
        out = Path(self.env["WORK"]) / "config"
        self.assertIn("CONFIG_TRIM_UNUSED_KSYMS=y", (out / "native.config").read_text())
        self.assertIn("# CONFIG_BOARD is not set", (out / ".config").read_text())
        self.assertEqual((out / "changes.tsv").read_text().splitlines(),
                         ["CONFIG_BOARD\ty\tn", "CONFIG_TRIM_UNUSED_KSYMS\ty\tn"])

    def test_policy_becomes_dense_scope_and_unknown_symbol_fails(self):
        drivers = self.linux / "drivers"
        drivers.mkdir()
        (drivers / "Makefile").write_text("obj-$(CONFIG_RED) += red.o\nobj-$(CONFIG_GREEN) += green.o\n"
                                          "obj-$(CONFIG_BLUE) += blue.o\n")
        for file in ("red", "green", "blue"):
            (drivers / f"{file}.c").touch()
        scope = self.root / "scope.tsv"
        cmd = [sys.executable, "-I", ROOT / "tools/kernel.py", "inventory", "--under", ".", "--out", scope]
        subprocess.run([*cmd, "--defer", "CONFIG_RED", "--drop", "CONFIG_BLUE"], env=self.env, check=True)
        self.assertEqual(scope.read_text().splitlines()[1:],
                         ["1\tCONFIG_BLUE\tblue\tdrop\tdrivers/blue.c", "2\tCONFIG_RED\tred\tdefer\tdrivers/red.c"])
        for opts in (["--drop", "CONFIG_MISSING"], ["--drop", "CONFIG_RED", "--defer", "CONFIG_RED"]):
            failed = subprocess.run([*cmd, *opts], env=self.env, text=True, capture_output=True, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("CONFIG_", failed.stderr)

    def test_architecture_selects_image_modules_and_device_trees(self):
        self.assertEqual(KERNEL["build_targets"]("riscv", None), ("Image", "modules", "dtbs"))
        self.assertEqual(KERNEL["build_targets"]("riscv", "vmlinuz.efi"), ("vmlinuz.efi", "modules", "dtbs"))
        self.assertEqual(KERNEL["build_targets"]("x86_64", None), ("bzImage", "modules"))

    def test_plan_uses_returned_trace_and_forwards_review_and_extra_runs(self):
        processor = self.root / "host/processor"
        processor.mkdir(parents=True)
        self.executable(processor / "kcfg-plan", '#!/bin/sh\nprintf "%s\\n" "$@"\n')
        work = self.env["WORK"]
        output = self.cli("plan", "--also", "second.tsv", "--review").stdout.splitlines()
        self.assertEqual(output, [f"{work}/profile/campaign", f"{work}/trace.tsv", f"{work}/plan",
                                  "--also", "second.tsv", "--review"])
        self.assertEqual(self.cli("plan", "first.tsv").stdout.splitlines()[1], "first.tsv")
        self.assertNotEqual(self.cli("build", "typo", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
