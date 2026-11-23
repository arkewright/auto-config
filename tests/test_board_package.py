import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = r'''
source "$ROOT/scripts/bundle.bash"
role=$1; shift
obj=$WORK/$role/kernel
boot_tree=arch/$ARCH/boot
compiler=$KERNEL_CC
if [[ $role == profile ]]; then compiler=$WORK/profile/campaign/clang; fi
KERNEL_TARGETS=("$@")
kmake() { fake-make "CC=$compiler" "O=$obj" "$@"; }
package_kernel
'''
FAKE_MAKE = '''#!/usr/bin/env bash
set -eu
printf '%s\\t' "$@" >> "$LOG"
printf '\\n' >> "$LOG"
ext= extout= obj= moddir= trees= release=
for arg; do
    case $arg in
        M=*) ext=${arg#*=} ;; MO=*) extout=${arg#*=} ;; O=*) obj=${arg#*=} ;;
        MODLIB=*) moddir=${arg#*=} ;; INSTALL_DTBS_PATH=*) trees=${arg#*=} ;;
        KERNELRELEASE=*) release=${arg#*=} ;;
    esac
done
[[ $release == "$(<"$obj/include/config/kernel.release")" ]]
case ${!#} in
    modules_install)
        if [[ -z $ext ]]; then
            mkdir -p "$moddir/kernel"
            cp "$obj/sample.ko" "$moddir/kernel/sample.ko"
            ln -s "$obj" "$moddir/build"
        else
            [[ $ext == "$ROOT/driver" ]]
            mkdir -p "$moddir/updates"
            cp "$extout/kcfg_collect.ko" "$moddir/updates/"
        fi ;;
    dtbs_install)
        mkdir -p "$trees"
        cp -R "$obj/arch/$ARCH/boot/dts/." "$trees/" ;;
    *) exit 33 ;;
esac
'''


class PackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.work = self.tmp / "work"
        self.packages = self.tmp / "packages with spaces"
        self.log = self.tmp / "calls"
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "ROOT": str(ROOT), "WORK": str(self.work),
                    "KERNEL_CC": "vendor-clang", "ARCH": "riscv", "KERNEL_IMAGE": "arch/riscv/boot/Image",
                    "PACKAGE_NAME": "vendor kernel", "PACKAGE_DIR": str(self.packages), "GZIP_LEVEL": "6",
                    "MODULE_STRIP": "1", "DEPLOY_SCRIPT": str(ROOT / "deploy.sh"), "LOG": str(self.log)}
        self.command("fake-make", FAKE_MAKE)
        for role in ("baseline", "profile", "candidate"):
            obj = self.work / role / "kernel"
            (obj / "include/config").mkdir(parents=True)
            (obj / "include/config/kernel.release").write_text(f"7.4-board-{role}\n")
            (obj / ".config").write_text(f"config {role}\n")
            (obj / "sample.ko").write_text(f"module {role}\n")
            boot = obj / "arch/riscv/boot"
            (boot / "dts/vendor").mkdir(parents=True)
            (boot / "Image").write_text(f"image {role}\n")
            (boot / "dts/vendor/board.dtb").write_text("board\n")
            (boot / "dts/vendor/overlay.dtbo").write_text("overlay\n")
        (self.work / "profile/driver").mkdir()
        (self.work / "profile/driver/kcfg_collect.ko").write_text("collector\n")
        (self.work / "plan").mkdir()
        (self.work / "plan/modules-load.late").write_text("configs\n")

    def command(self, name, script):
        path = self.bin / name
        path.write_text(script)
        path.chmod(0o755)

    def package(self, role, *targets, **settings):
        env = self.env | settings
        result = subprocess.run(["bash", "-euc", "set -o pipefail\n" + PACKAGE, "package", role, *targets],
                                env=env, cwd=self.tmp, check=True, capture_output=True, text=True)
        name = env["PACKAGE_NAME"] + "-" + role
        archive = Path(env["PACKAGE_DIR"]) / (name + ".tar.gz")
        self.assertEqual(result.stdout.strip(), str(archive))
        dest = Path(tempfile.mkdtemp(dir=self.tmp))
        subprocess.run(["tar", "-xzf", archive, "-C", dest], check=True)
        self.assertEqual(list(dest.iterdir()), [dest / name])
        return dest / name

    def test_versioned_payloads_and_profile_only_collector(self):
        for role in ("baseline", "profile", "candidate"):
            with self.subTest(role=role):
                self.log.write_text("")
                bundle = self.package(role, "Image", "modules", "dtbs")
                release = f"7.4-board-{role}"
                contents = {"boot", "lib", "release", "deploy.sh"}
                if role == "candidate":
                    contents.add("modules-load.late")
                    self.assertEqual((bundle / "modules-load.late").read_text(), "configs\n")
                self.assertEqual({p.name for p in bundle.iterdir()}, contents)
                for name, value in (("release", release), (f"boot/config-{release}", f"config {role}"),
                                    (f"boot/vmlinuz-{release}", f"image {role}"),
                                    (f"boot/dtbs/{release}/vendor/board.dtb", "board"),
                                    (f"boot/dtbs/{release}/vendor/overlay.dtbo", "overlay")):
                    self.assertEqual((bundle / name).read_text(), value + "\n")
                script = bundle / "deploy.sh"
                self.assertEqual(script.read_bytes(), (ROOT / "deploy.sh").read_bytes())
                self.assertEqual(script.stat().st_mode & 0o777, 0o755)
                moddir = bundle / "lib/modules" / release
                self.assertEqual((moddir / "kernel/sample.ko").read_text(), f"module {role}\n")
                self.assertFalse((moddir / "build").is_symlink())
                calls = [line.rstrip("\t").split("\t") for line in self.log.read_text().splitlines()]
                self.assertEqual([c[-1] for c in calls],
                                 ["modules_install"] * (2 if role == "profile" else 1) + ["dtbs_install"])
                self.assertIn("DEPMOD=/bin/true", calls[0])
                self.assertIn("INSTALL_MOD_STRIP=1", calls[0])
                collector = moddir / "updates/kcfg_collect.ko"
                if role == "profile":
                    self.assertEqual(collector.read_text(), "collector\n")
                    self.assertIn(f"CC={self.work}/profile/campaign/clang", calls[0])
                    self.assertIn(f"MO={self.work}/profile/driver", calls[1])
                    self.assertIn("CC=vendor-clang", calls[1])
                else:
                    self.assertFalse(collector.exists())

    def test_custom_image_installer_and_explicit_dtb(self):
        obj = self.work / "baseline/kernel"
        image = obj / "arch/riscv/boot/vmlinuz.efi"
        image.write_text("custom image\n")
        deploy = self.tmp / "board deploy.sh"
        deploy.write_text("#!/bin/sh\n# board instructions\n")
        bundle = self.package("baseline", "vmlinuz.efi", "vendor/board.dtb", DEPLOY_SCRIPT=str(deploy),
                              GZIP_LEVEL="1", KERNEL_IMAGE=str(image.relative_to(obj)))
        self.assertFalse(self.log.exists())
        self.assertFalse((bundle / "lib").exists())
        self.assertEqual((bundle / "deploy.sh").read_bytes(), deploy.read_bytes())
        self.assertEqual((bundle / "boot/vmlinuz-7.4-board-baseline").read_text(), "custom image\n")
        self.assertEqual([p.name for p in (bundle / "boot/dtbs/7.4-board-baseline/vendor").iterdir()], ["board.dtb"])

    def board(self):
        bwrap = shutil.which("bwrap")
        if not bwrap or not Path("/lib/modules").is_dir():
            self.skipTest("isolated deploy test needs bubblewrap and /lib/modules")
        boot, modules = self.tmp / "boot", self.tmp / "modules"
        boot.mkdir()
        modules.mkdir()
        sandbox = [bwrap, "--unshare-user", "--uid", "0", "--gid", "0", "--unshare-net", "--ro-bind", "/", "/",
                   "--dev", "/dev", "--proc", "/proc", "--bind", self.tmp, self.tmp, "--bind", boot, "/boot",
                   "--bind", modules, "/lib/modules", "--chdir", str(self.tmp), "--"]
        probe = subprocess.run([*sandbox, "true"], capture_output=True, check=False, text=True)
        if probe.returncode:
            self.skipTest(probe.stderr.strip())
        self.command("depmod", '''#!/bin/sh
set -eu
test "$1" = -a && test -f "/lib/modules/$2/kernel/sample.ko"
printf 'depmod %s\\n' "$2" >> "$BOARD_LOG"
''')
        self.command("update-initramfs", '''#!/bin/sh
set -eu
test "$2" = -k && test -f "/boot/vmlinuz-$3"
printf 'initramfs %s %s\\n' "$1" "$3" >> "$BOARD_LOG"
printf '%s\\n' "$3" > "/boot/initrd.img-$3"
''')
        self.command("update-grub", '#!/bin/sh\nprintf "grub\\n" >> "$BOARD_LOG"\n')
        return sandbox, boot, modules

    def test_deployment_adds_kernel_and_updates_only_its_initramfs(self):
        sandbox, boot, modules = self.board()
        release = "7.4-board-profile"
        (boot / "vmlinuz-other").write_text("old kernel\n")
        (boot / "initrd.img-other").write_text("old initramfs\n")
        bundle = self.package("profile", "Image", "modules", "dtbs")
        board_log = self.tmp / "board-log"
        for mode in ("-c", "-u"):
            board_log.write_text("")
            subprocess.run([*sandbox, bundle / "deploy.sh"], check=True, env=self.env | {"BOARD_LOG": str(board_log)})
            self.assertEqual(board_log.read_text().splitlines(), [f"depmod {release}", f"initramfs {mode} {release}", "grub"])
            self.assertEqual((boot / f"initrd.img-{release}").read_text(), release + "\n")
            self.assertEqual((boot / f"config-{release}").read_text(), "config profile\n")
            self.assertEqual((boot / "dtbs" / release / "vendor/board.dtb").read_text(), "board\n")
            self.assertEqual((modules / release / "updates/kcfg_collect.ko").read_text(), "collector\n")
            self.assertEqual((boot / "vmlinuz-other").read_text(), "old kernel\n")
            self.assertEqual((boot / "initrd.img-other").read_text(), "old initramfs\n")

    def test_image_only_needs_neither_device_trees_nor_modules(self):
        bundle = self.package("baseline", "Image")
        self.assertFalse(self.log.exists())
        self.assertFalse((bundle / "boot/dtbs").exists())
        self.assertFalse((bundle / "lib").exists())
        sandbox, _, modules = self.board()
        board_log = self.tmp / "board-log"
        subprocess.run([*sandbox, bundle / "deploy.sh"], check=True, env=self.env | {"BOARD_LOG": str(board_log)})
        self.assertEqual(board_log.read_text().splitlines(), ["initramfs -c 7.4-board-baseline", "grub"])
        self.assertEqual(list(modules.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
