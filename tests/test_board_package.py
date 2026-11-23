import gzip
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = "1234567890abcdef" * 4
RELEASES = {"baseline": "7.4-kcfg-baseline", "profile": f"7.4-kcfg-{CAMPAIGN[:16]}", "candidate": "7.4-kcfg-candidate"}
KERNEL = r"/boot/Linux slot;$(touch injected)\trial"
INITRD = "/boot/initrd slot $literal"
BOOT_DTB = "/boot/default board.dtb"
DTB = "vendor/board.dtb"

FAKE_MAKE = r"""#!/usr/bin/env bash
set -eu
printf '%s\t' "$@" >> "$MAKE_LOG"
printf '\n' >> "$MAKE_LOG"
obj= ext= extout= moddir= dtbdir= action= arch= strip= depmod= kernel_cc=
for arg; do
    case $arg in
        O=*) obj=${arg#*=} ;; M=*) ext=${arg#*=} ;; MO=*) extout=${arg#*=} ;;
        MODLIB=*) moddir=${arg#*=} ;; INSTALL_DTBS_PATH=*) dtbdir=${arg#*=} ;;
        ARCH=*) arch=${arg#*=} ;; INSTALL_MOD_STRIP=*) strip=${arg#*=} ;;
        DEPMOD=*) depmod=${arg#*=} ;; CC=*) kernel_cc=${arg#*=} ;;
        modules_install|dtbs_install) action=$arg ;;
        Image|vmlinuz.efi|vmlinux|modules|dtbs|kernelrelease) exit 56 ;;
    esac
done
[[ $arch == riscv && -f $obj/.config && -n $kernel_cc ]]
case $action in
    modules_install)
        [[ $strip == 1 && $depmod == /bin/true ]]
        if [[ -n $ext ]]; then
            [[ -d $ext && -f $extout/kcfg_collect.ko ]]
            mkdir -p "$moddir/updates"
            printf 'stripped and signed\n%s\n' "$(<"$extout/kcfg_collect.ko")" |
                gzip -n -c > "$moddir/updates/kcfg_collect.ko.gz"
        else
            mkdir -p "$moddir/kernel"
            ln -s "$obj" "$moddir/build"
            ln -s "$obj" "$moddir/source"
            cp "$obj/modules.builtin" "$moddir/modules.builtin"
            cp "$obj/sample.ko" "$moddir/kernel/sample.ko.zst"
        fi
        ;;
    dtbs_install)
        mkdir -p "$dtbdir/vendor"
        cp "$obj/arch/riscv/boot/dts/vendor/"* "$dtbdir/vendor/"
        ;;
    *) exit 57 ;;
esac
"""


class BoardPackageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.temp = Path(tmp.name)
        self.work = self.temp / "work"
        self.root = self.temp / "repo"
        self.sdk = self.temp / "sdk"
        self.tools = self.temp / "bin"
        self.log = self.temp / "make.log"
        for path in (self.root / "driver", self.temp / "linux", self.sdk / "bin", self.tools):
            path.mkdir(parents=True)
        (self.temp / "linux/Makefile").touch()
        target = self.root / "scripts/target"
        target.mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/target/install", target / "install")
        (target / "run").write_text("#!/usr/bin/env bash\n# packaged run fixture\n")
        self.executable(self.tools / "make", FAKE_MAKE)
        self.env = {"PATH": f"{self.tools}{os.pathsep}{os.environ['PATH']}", "ROOT": str(self.root), "WORK": str(self.work),
                    "LINUX": str(self.temp / "linux"), "SDK": str(self.sdk), "MAKE_LOG": str(self.log),
                    "GRUB_KERNEL": KERNEL, "GRUB_INITRD": INITRD}
        for kind, release in RELEASES.items():
            obj = self.work / kind / "kernel"
            (obj / "include/config").mkdir(parents=True)
            (obj / "include/config/kernel.release").write_text(release + "\n")
            (obj / ".config").write_text(f"config from {kind}\n")
            (obj / "modules.builtin").write_text("kernel/builtin.ko\n")
            (obj / "sample.ko").write_text(f"module from {kind}\n")
            boot = obj / "arch/riscv/boot"
            trees = boot / "dts/vendor"
            trees.mkdir(parents=True)
            (trees / "board.dtb").write_text(f"tree from {kind}\n")
            (trees / "extra.dtbo").write_text(f"overlay from {kind}\n")
            (boot / "Image").write_text(f"image from {kind}\n")
        capture = self.work / "profile/campaign"
        capture.mkdir()
        (capture / "campaign.id").write_text(CAMPAIGN + "\n")
        self.executable(capture / "clang", "#!/usr/bin/env bash\nexit 55\n")
        driver = self.work / "profile/driver"
        driver.mkdir()
        (driver / "kcfg_collect.ko").write_text("collector from profile\n")
        plan = self.work / "plan"
        plan.mkdir()
        (plan / "modules-load.late").write_text("late_module\n")

    @staticmethod
    def executable(path, text):
        path.write_text(text)
        path.chmod(0o755)

    def pack(self, kind, **settings):
        return subprocess.run(["bash", ROOT / "scripts/host/pack.sh", kind], cwd=self.temp,
                              env=self.env | settings, check=False, capture_output=True, text=True)

    def archive(self, kind):
        return self.work / "packages" / f"{kind}.tar.gz"

    def unpack(self, kind):
        dest = Path(tempfile.mkdtemp(dir=self.temp))
        subprocess.run(["tar", "-xzf", self.archive(kind), "-C", dest], check=True)
        self.assertEqual(list(dest.iterdir()), [dest / kind])
        return dest / kind

    def test_archive_layout_and_install_only_kbuild_targets(self):
        for kind, release in RELEASES.items():
            with self.subTest(kind=kind):
                self.log.write_text("")
                result = self.pack(kind, KERNEL_CC="custom-clang")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(self.archive(kind)))
                bundle = self.unpack(kind)
                self.assertEqual({p.name for p in bundle.iterdir()},
                                 {"release", "kind", "modules-load.late", "install", "run", "GRUB_KERNEL",
                                  "GRUB_INITRD", "GRUB_DTB", "DTB", "boot", "lib", "dtbs"})
                for path, contents in (
                    ("release", release), ("kind", kind), ("GRUB_KERNEL", KERNEL), ("GRUB_INITRD", INITRD),
                    ("GRUB_DTB", ""), ("DTB", ""), ("boot/Image", f"image from {kind}"),
                    ("boot/config", f"config from {kind}"), (f"dtbs/{DTB}", f"tree from {kind}"),
                    ("dtbs/vendor/extra.dtbo", f"overlay from {kind}"),
                    (f"lib/modules/{release}/kernel/sample.ko.zst", f"module from {kind}"),
                ):
                    self.assertEqual((bundle / path).read_text(), contents + "\n")
                self.assertEqual((bundle / "modules-load.late").read_text(),
                                 "late_module\n" if kind == "candidate" else "")
                for helper in ("install", "run"):
                    self.assertEqual((bundle / helper).stat().st_mode & 0o777, 0o755)
                    self.assertEqual((bundle / helper).read_bytes(), (self.root / "scripts/target" / helper).read_bytes())
                moddir = bundle / "lib/modules" / release
                self.assertFalse((moddir / "build").is_symlink())
                self.assertFalse((moddir / "source").is_symlink())
                collector = moddir / "updates/kcfg_collect.ko.gz"
                if kind == "profile":
                    self.assertEqual(gzip.decompress(collector.read_bytes()), b"stripped and signed\ncollector from profile\n")
                else:
                    self.assertFalse(collector.exists())

                calls = [line.rstrip("\t").split("\t") for line in self.log.read_text().splitlines()]
                targets = ["modules_install"] * (2 if kind == "profile" else 1) + ["dtbs_install"]
                self.assertEqual([call[-1] for call in calls], targets)
                for call in calls:
                    self.assertIn(f"O={self.work / kind / 'kernel'}", call)
                    self.assertIn(f"KERNELRELEASE={release}", call)
                    self.assertIn(f"LLVM={self.sdk / 'bin'}/", call)
                if kind == "profile":
                    self.assertIn(f"CC={self.work / 'profile/campaign/clang'}", calls[0])
                    self.assertIn(f"M={self.root / 'driver'}", calls[1])
                    self.assertIn(f"MO={self.work / 'profile/driver'}", calls[1])
                    self.assertIn("CC=custom-clang", calls[1])
                else:
                    self.assertIn("CC=custom-clang", calls[0])

    def test_optional_local_efi_signing_uses_the_selected_recorded_image(self):
        image = self.work / "baseline/kernel/arch/riscv/boot/vmlinuz.efi"
        image.write_text("efi zboot\n")
        self.executable(self.tools / "sbsign", r"""#!/usr/bin/env bash
set -eu
[[ $# == 7 && $1 == --key && $2 == 'local key' && $3 == --cert && $4 == 'local cert' && $5 == --output ]]
printf 'signed\n' > "$6"
while IFS= read -r line; do printf '%s\n' "$line"; done < "$7" >> "$6"
""")
        result = self.pack("baseline", IMAGE_TARGET="vmlinuz.efi", EFI_KEY="local key", EFI_CERT="local cert")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image.read_text(), "efi zboot\n")
        self.assertEqual((self.unpack("baseline") / "boot/Image").read_text(), "signed\nefi zboot\n")

    def test_archive_headers_use_source_date_epoch_and_stable_order(self):
        epoch = 1_700_000_000
        for _ in range(2):
            result = self.pack("baseline", SOURCE_DATE_EPOCH=str(epoch))
            self.assertEqual(result.returncode, 0, result.stderr)
            with tarfile.open(self.archive("baseline")) as archive:
                members = archive.getmembers()
                self.assertEqual([m.name for m in members], sorted(m.name for m in members))
                self.assertEqual({(m.uid, m.gid, m.mtime) for m in members}, {(0, 0, epoch)})

    def test_pack_failure_does_not_publish_or_replace_an_archive(self):
        for settings, error in (
            ({"GRUB_KERNEL": "relative"}, "GRUB_KERNEL"),
            ({"GRUB_INITRD": "/first\n/second"}, "GRUB_INITRD"),
            ({"GRUB_DTB": BOOT_DTB}, "DTB"),
            ({"GRUB_DTB": BOOT_DTB, "DTB": "vendor/../board.dtb"}, "DTB"),
            ({"EFI_KEY": "only key"}, "EFI_CERT"),
        ):
            with self.subTest(settings=settings):
                result = self.pack("candidate", **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertFalse(self.archive("candidate").exists())
                self.assertFalse(self.log.exists())
        result = self.pack("candidate")
        self.assertEqual(result.returncode, 0, result.stderr)
        previous = self.archive("candidate").read_bytes()
        result = self.pack("candidate", GRUB_DTB=BOOT_DTB, DTB="vendor/not-built.dtb")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not installed from this build", result.stderr)
        self.assertEqual(self.archive("candidate").read_bytes(), previous)
        self.assertEqual(list(self.archive("candidate").parent.iterdir()), [self.archive("candidate")])

    def fake_board(self):
        bwrap = shutil.which("bwrap")
        if not bwrap or not Path("/lib/modules").is_dir():
            self.skipTest("installer isolation requires bubblewrap and a /lib/modules mount point")
        boot, modules = self.temp / "board-boot", self.temp / "board-modules"
        boot.mkdir()
        modules.mkdir()
        sandbox = [bwrap, "--unshare-user", "--uid", "0", "--gid", "0", "--unshare-net", "--ro-bind", "/", "/",
                   "--dev", "/dev", "--proc", "/proc", "--bind", self.temp, self.temp, "--bind", boot, "/boot",
                   "--bind", modules, "/lib/modules", "--chdir", self.temp, "--"]
        probe = subprocess.run([*sandbox, "true"], check=False, capture_output=True, text=True)
        if probe.returncode:
            self.skipTest(f"installer mount namespace unavailable: {probe.stderr.strip()}")
        self.executable(self.tools / "depmod", r"""#!/usr/bin/env bash
set -eu
[[ $# == 2 && $1 == -a && -f /lib/modules/$2/kernel/sample.ko.zst && -f /boot/config-$2 ]]
printf 'depmod %s\n' "$2" >> "$BOARD_LOG"
printf 'module index\n' > "/lib/modules/$2/modules.dep"
""")
        self.executable(self.tools / "mkinitramfs", r"""#!/usr/bin/env bash
set -eu
[[ $# == 3 && $1 == -o && -s /lib/modules/$3/modules.dep ]]
printf 'mkinitramfs %s\n' "$3" >> "$BOARD_LOG"
printf 'initrd for %s\n' "$3" > "$2"
[[ ${FAIL_INITRD:-} != 1 ]] || exit 27
""")
        self.board_log = self.temp / "board.log"
        return sandbox, boot, modules

    def install(self, sandbox, bundle, **settings):
        return subprocess.run([*sandbox, bundle / "install"],
                              env=self.env | {"BOARD_LOG": str(self.board_log)} | settings,
                              check=False, capture_output=True, text=True)

    def test_extracted_install_uses_literal_fixed_paths_for_every_release(self):
        sandbox, boot, modules = self.fake_board()
        kernel, initrd = boot / Path(KERNEL).name, boot / Path(INITRD).name
        (boot / "old image").write_text("original image\n")
        kernel.symlink_to("old image")
        initrd.write_text("original initrd\n")
        (boot / "grub").mkdir()
        grub = boot / "grub/grub.cfg"
        grub.write_text("unchanged default entry\n")
        for kind, release in RELEASES.items():
            with self.subTest(kind=kind):
                settings = {} if kind == "baseline" else {"GRUB_DTB": BOOT_DTB, "DTB": DTB}
                result = self.pack(kind, **settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.install(sandbox, self.unpack(kind), GRUB_KERNEL="/ignored", GRUB_INITRD="/ignored")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(kernel.read_text(), f"image from {kind}\n")
                self.assertEqual(initrd.read_text(), f"initrd for {release}\n")
                default_dtb = boot / Path(BOOT_DTB).name
                if settings:
                    self.assertEqual(default_dtb.read_text(), f"tree from {kind}\n")
                else:
                    self.assertFalse(default_dtb.exists())
                self.assertEqual((boot / "dtbs" / release / DTB).read_text(), f"tree from {kind}\n")
                self.assertEqual((boot / f"config-{release}").read_text(), f"config from {kind}\n")
                self.assertEqual((modules / release / "modules.dep").read_text(), "module index\n")
        self.assertEqual(self.board_log.read_text().splitlines(),
                         [f"{tool} {release}" for release in RELEASES.values() for tool in ("depmod", "mkinitramfs")])
        self.assertEqual({p.name for p in modules.iterdir()}, set(RELEASES.values()))
        self.assertFalse(kernel.is_symlink())
        self.assertEqual((boot / "old image").read_text(), "original image\n")
        self.assertEqual(grub.read_text(), "unchanged default entry\n")
        self.assertFalse((self.temp / "injected").exists())
        self.assertFalse(list(self.temp.rglob(".kcfg-install.*")))

    def test_extracted_install_preserves_fixed_boot_files_when_mkinitramfs_fails(self):
        sandbox, boot, modules = self.fake_board()
        kernel, initrd, dtb = (boot / Path(path).name for path in (KERNEL, INITRD, BOOT_DTB))
        release = RELEASES["candidate"]
        moddir = modules / release
        moddir.mkdir()
        (moddir / "old.ko").write_text("old module")
        old = {kernel: "old image", initrd: "old initrd", dtb: "old default tree"}
        for path, contents in old.items():
            path.write_text(contents)
        result = self.pack("candidate", GRUB_DTB=BOOT_DTB, DTB=DTB)
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = self.unpack("candidate")
        result = self.install(sandbox, bundle, FAIL_INITRD="1")
        self.assertEqual(result.returncode, 27, result.stderr)
        for path, contents in old.items():
            self.assertEqual(path.read_text(), contents)
        self.assertFalse(list(self.temp.rglob(".kcfg-install.*")))
        result = self.install(sandbox, bundle)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(kernel.read_text(), "image from candidate\n")
        self.assertFalse((moddir / "old.ko").exists())

    def test_extracted_install_rejects_nonabsolute_or_overlapping_boot_paths(self):
        sandbox, boot, modules = self.fake_board()
        result = self.pack("baseline")
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = self.unpack("baseline")
        for path in ("", "relative", f"/boot/./{Path(KERNEL).name}"):
            with self.subTest(path=path):
                (bundle / "GRUB_INITRD").write_text(path + "\n")
                result = self.install(sandbox, bundle)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("GRUB_INITRD" if not path.startswith("/") else "distinct", result.stderr)
                self.assertFalse(self.board_log.exists())
                self.assertEqual(list(modules.iterdir()), [])
                self.assertFalse(list(boot.rglob(".kcfg-install.*")))


if __name__ == "__main__":
    unittest.main()
