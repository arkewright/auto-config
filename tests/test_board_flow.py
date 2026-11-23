import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / ".env.example").read_text()


class PublicEntryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.work = self.base / "trial"
        self.deps = self.base / "sources"
        self.host = self.base / "host"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.file = self.base / "my settings.env"
        self.log = self.base / "calls"
        self.env = {"HOME": str(self.base), "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "GIT_ALLOW_PROTOCOL": "file", "GIT_CONFIG_NOSYSTEM": "1", "LOG": str(self.log)}

    def settings(self, **values):
        values = {"DEPS": self.deps, "WORK": self.work, "HOST_BUILD": self.host, "PYTHON": sys.executable, **values}
        content = TEMPLATE
        for name, value in values.items():
            setting = ("(" + shlex.join(value) + ")") if isinstance(value, list) else shlex.quote(str(value))
            content, count = re.subn(rf"^{name}=.*$", lambda _, line=f"{name}={setting}": line, content, flags=re.MULTILINE)
            self.assertEqual(count, 1, name)
        self.file.write_text(content)

    def cli(self, *args, settings=True, entry=ROOT / "kcfg", success=True):
        result = subprocess.run([entry, *(["--env", self.file.name] if settings else []), *args], cwd=self.base,
                                env=self.env, text=True, capture_output=True, check=False)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    @staticmethod
    def executable(path, code):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code)
        path.chmod(0o755)

    def sdk(self):
        prefix = self.deps / "llvm"
        for name in ("clang", "clang++", "ld.lld", "llvm-config"):
            self.executable(prefix / "bin" / name, f'#!/bin/sh\nprintf "%s\\n" "{prefix}/lib/cmake/llvm"\n')
        return prefix

    def test_copy_template_and_fetch_to_chosen_paths_without_exporting(self):
        project = self.base / "project"
        project.mkdir()
        entry = project / "kcfg"
        shutil.copy2(ROOT / "kcfg", entry)
        self.assertIn("--build-kernel", self.cli("--help", settings=False, entry=entry).stdout)
        self.assertIn(".env.example", self.cli("--init-deps", settings=False, entry=entry, success=False).stderr)
        origin = self.base / "upstream"
        origin.mkdir()
        def git(*args):
            subprocess.run(["git", *args], cwd=origin, env=self.env, capture_output=True, check=True)
        git("init", "-b", "board")
        (origin / "llvm").mkdir()
        for name in ("Makefile", "llvm/CMakeLists.txt", "CMakeLists.txt"):
            (origin / name).touch()
        git("add", ".")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        dest = self.base / "board sources" / "linux"
        def configure(ref):
            self.settings(LINUX_SRC=dest, LINUX_URL=origin.as_uri(), LLVM_URL=origin.as_uri(), FTXUI_URL=origin.as_uri(),
                          LINUX_REF=ref, LLVM_REF=ref, FTXUI_REF=ref)
            shutil.copy2(self.file, project / ".env")
        configure("board")
        self.cli("--init-deps", entry=entry, settings=False)
        for file in (dest / "Makefile", self.deps / "llvm-project/llvm/CMakeLists.txt", self.deps / "ftxui/CMakeLists.txt"):
            self.assertTrue(file.is_file())
        (dest / ".git").rename(self.base / "git-metadata")
        (dest / "Makefile").write_text("local edits\n")
        configure("does-not-exist")
        self.cli("--init-deps", entry=entry, settings=False)
        self.assertEqual((dest / "Makefile").read_text(), "local edits\n")

    def test_toolchain_build_or_reuse_and_cmake_argument_boundaries(self):
        prefix = self.sdk()
        cmakedir = prefix / "lib/cmake/llvm"
        self.executable(self.bin / "cmake", f'''#!{sys.executable} -I
import json, os, sys
from pathlib import Path
with open(os.environ['LOG'], 'a') as stream:
    print(json.dumps(sys.argv[1:]), file=stream)
if '--build' in sys.argv and 'install' in sys.argv:
    path = Path({str(prefix / 'bin/llvm-config')!r})
    if not path.exists(): path.symlink_to('clang')
''')
        tui = self.base / "installed tui"
        sysroot = self.base / "sysroot"
        self.settings(LLVM_PREFIX=os.path.relpath(prefix, ROOT), FTXUI_DIR=tui, SYSROOT=sysroot,
                      TOOLS_CMAKE_ARGS=["-DFLAG=two words"])
        self.cli("--build-tools")
        cfg, build = [json.loads(line) for line in self.log.read_text().splitlines()]
        for arg in (f"-Dftxui_DIR={tui}", "-DFTXUI_SOURCE_DIR=", f"-DLLVM_DIR={cmakedir}", "-DFLAG=two words",
                    f"-DCMAKE_SYSROOT={sysroot}", f"-DCMAKE_CXX_COMPILER={prefix}/bin/clang++"):
            self.assertIn(arg, cfg)
        self.assertEqual(build[:2], ["--build", str(self.host)])
        (prefix / "bin/llvm-config").unlink()
        self.log.unlink()
        source = self.base / "custom llvm"
        self.settings(LLVM_SRC=source, JOBS=3, LLVM_CMAKE_ARGS=["-DCUSTOM=list;with;semicolons"])
        self.cli("--build-tools")
        bootstrap, install, host, _ = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIn(str(source / "llvm"), bootstrap)
        self.assertIn("-DCUSTOM=list;with;semicolons", bootstrap)
        self.assertIn(f"-DCMAKE_INSTALL_PREFIX={prefix}", bootstrap)
        self.assertEqual(install[-4:], ["--parallel", "3", "--target", "install"])
        self.assertIn(f"-DFTXUI_SOURCE_DIR={self.deps}/ftxui", host)
        self.assertFalse((self.deps / "llvm-project").exists())

    def kernel_source(self):
        linux = self.deps / "linux"
        (linux / "arch/riscv/boot/dts").mkdir(parents=True)
        (linux / "Makefile").write_text('''SRCARCH := $(if $(filter x86_64 i386,$(ARCH)),x86,$(ARCH))
allyesconfig:
\t@test "$(origin KCONFIG_ALLCONFIG)" = undefined && test ! -f "$(O)/.config"
\t@printf '%s\\n' CONFIG_TRIM_UNUSED_KSYMS=y CONFIG_BOARD=y > "$(O)/.config"
olddefconfig:
\t@test -f "$(O)/.config"
\t@printf '%s\\n' CONFIG_NORMALIZED=y >> "$(O)/.config"
bzImage Image vmlinuz.efi:
\t@mkdir -p "$(O)/arch/$(SRCARCH)/boot" "$(O)/include/config"
\t@printf '%s\\n' "$(KCFLAGS)" > "$(O)/arch/$(SRCARCH)/boot/$@"
\t@printf '6.12%s\\n' "$(LOCALVERSION)" > "$(O)/include/config/kernel.release"
modules dtbs:
\t@printf '%s\\n' $@ >> "$(LOG)"
modules_install:
\t@mkdir -p "$(MODLIB)"; touch "$(MODLIB)/modules.order"
dtbs_install:
\t@mkdir -p "$(INSTALL_DTBS_PATH)/board"; touch "$(INSTALL_DTBS_PATH)/board/test.dtb"
''')
        self.executable(linux / "scripts/config", f'''#!{sys.executable} -I
import sys
from pathlib import Path
_, _, name, *args = sys.argv
p = Path(name)
lines = p.read_text().splitlines()
for opt, key in zip(args[::2], args[1::2]):
    lines = [line for line in lines if not line.startswith('CONFIG_' + key + '=')]
    lines.append('CONFIG_' + key + '=y' if opt == '-e' else '# CONFIG_' + key + ' is not set')
p.write_text('\\n'.join(lines) + '\\n')
''')

    def test_native_config_edits_and_default_targets_in_one_archive(self):
        self.sdk()
        self.kernel_source()
        self.env["KCONFIG_ALLCONFIG"] = "ambient-fragment"
        self.settings(CONFIG_OPTIONS=["--disable", "BOARD"], KBUILD_ARGS=["KCFLAGS=-O2 -g0"])
        self.cli("--build-kernel", "baseline")
        out = self.work / "baseline"
        self.assertIn("CONFIG_TRIM_UNUSED_KSYMS=y", (out / "allyes.config").read_text())
        config = (out / ".config").read_text().splitlines()
        for item in ("CONFIG_MODULES=y", "CONFIG_TRACEPOINTS=y", "# CONFIG_TRIM_UNUSED_KSYMS is not set",
                     "# CONFIG_BOARD is not set", "CONFIG_NORMALIZED=y"):
            self.assertIn(item, config)
        with tarfile.open(self.work / "packages/kernel-baseline.tar.gz") as bundle:
            root, release = "kernel-baseline", "6.12-kcfg-baseline"
            self.assertFalse(any("/dtbs/" in n for n in bundle.getnames()))
            self.assertIn(f"{root}/lib/modules/{release}/modules.order", bundle.getnames())
            self.assertEqual(bundle.extractfile(f"{root}/boot/vmlinuz-{release}").read(), b"-O2 -g0\n")
        self.assertTrue((out / "kernel/arch/x86/boot/bzImage").is_file())
        self.assertEqual(self.log.read_text().splitlines(), ["modules"])

    def test_riscv_selects_image_and_device_trees_by_changing_arch_only(self):
        self.sdk()
        self.kernel_source()
        self.settings(ARCH="riscv")
        self.cli("--build-kernel", "baseline")
        self.assertTrue((self.work / "baseline/kernel/arch/riscv/boot/Image").is_file())
        self.assertEqual(set(self.log.read_text().splitlines()), {"modules", "dtbs"})
        with tarfile.open(self.work / "packages/kernel-baseline.tar.gz") as bundle:
            self.assertIn("kernel-baseline/boot/dtbs/6.12-kcfg-baseline/board/test.dtb", bundle.getnames())

    def test_supplied_config_only_uses_olddefconfig_and_selected_image(self):
        self.sdk()
        self.kernel_source()
        seed = self.base / "vendor.config"
        initial = b"CONFIG_BOARD=y\n# CONFIG_MODULES is not set\n"
        seed.write_bytes(initial)
        self.settings(KERNEL_CONFIG=seed, ARCH="arm64", KERNEL_TARGETS=["vmlinuz.efi"],
                      KERNEL_IMAGE="arch/arm64/boot/vmlinuz.efi", PACKAGE_DIR=self.base / "dist", PACKAGE_NAME="vendor")
        self.cli("--build-kernel", "baseline")
        self.assertFalse((self.work / "baseline/allyes.config").exists())
        self.assertEqual(seed.read_bytes(), initial)
        self.assertEqual((self.work / "baseline/.config").read_bytes(), initial + b"CONFIG_NORMALIZED=y\n")
        with tarfile.open(self.base / "dist/vendor-baseline.tar.gz") as bundle:
            self.assertFalse(any("/dtbs/" in n or "/modules/" in n for n in bundle.getnames()))
        self.assertFalse(self.log.exists())

    def test_trace_settings_and_review_forward_to_processor(self):
        self.executable(self.host / "processor/kcfg-plan", '#!/bin/sh\nprintf "%s\\n" "$@"\n')
        other = self.base / "second trace.tsv"
        self.settings(EXTRA_TRACES=[str(other)])
        self.assertEqual(self.cli("--plan", "--review").stdout.splitlines(),
                         [str(self.work / "profile/campaign"), str(self.work / "trace.tsv"), str(self.work / "plan"),
                          "--also", str(other), "--review"])

    def test_deferred_modules_require_modules_build_target(self):
        self.sdk()
        self.settings(KERNEL_TARGETS=["Image"])
        result = self.cli("--build-kernel", success=False)
        self.assertIn("modules", result.stderr)
        plan = self.work / "plan"
        plan.mkdir(parents=True)
        (plan / "modules-load.late").write_text("configs\n")
        (plan / "requested.config").write_text("CONFIG_IKCONFIG=m\n")
        result = self.cli("--build-kernel", "candidate", success=False)
        self.assertIn("modules", result.stderr)
        self.assertFalse((self.work / "candidate/kernel").exists())


if __name__ == "__main__":
    unittest.main()
