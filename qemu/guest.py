#!/usr/bin/env -S uv run --no-project python -I
"""Boot a private Ubuntu RAM filesystem, without privileged operations on the host."""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "24.04.4"
ARCHIVE = f"ubuntu-base-{VERSION}-base-amd64.tar.gz"
RELEASE = f"https://cdimage.ubuntu.com/ubuntu-base/releases/{VERSION}/release"
CACHE = HERE.parent / ".deps" / "guest"
MODES = ("profile", "baseline", "candidate")


def nonempty_file(path):
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"expected a nonempty regular file: {path}")
    return path


def fetch(cache):
    cache.mkdir(parents=True, exist_ok=True)
    release = cache / f"ubuntu-base-{VERSION}"
    if release.exists():
        return nonempty_file(release / ARCHIVE)
    with tempfile.TemporaryDirectory(dir=cache, prefix="download-") as tmp:
        staged = Path(tmp) / "release"
        staged.mkdir()
        subprocess.run(
            ["curl", "--disable", "--fail", "--location", "--proto", "=https", "--proto-redir",
             "=https", "--retry", "2", "--max-time", "300", "--silent", "--show-error",
             "--output", str(staged / ARCHIVE), f"{RELEASE}/{ARCHIVE}"], check=True,
        )
        nonempty_file(staged / ARCHIVE)
        # Publish only after a successful download. A concurrent fetch may win the rename.
        try:
            staged.rename(release)
        except OSError:
            if not release.is_dir():
                raise
    return nonempty_file(release / ARCHIVE)


def file_entry(archive, path, name, mode):
    if not path.is_file():
        raise ValueError(f"not a regular input file: {path}")
    with path.open("rb") as contents:
        info = tarfile.TarInfo(name)
        info.mode = mode
        info.size = os.fstat(contents.fileno()).st_size
        archive.addfile(info, contents)


def text_entry(archive, name, lines):
    contents = "".join(f"{line}\n" for line in lines).encode("ascii")
    info = tarfile.TarInfo(name)
    info.mode = 0o444
    info.size = len(contents)
    archive.addfile(info, io.BytesIO(contents))


def named_files(archive, paths, directory, mode):
    filenames = []
    for path in paths:
        name = path.name
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.+-]*", name) or name in filenames:
            raise ValueError(f"unsafe or duplicate {directory} filename: {name!r}")
        file_entry(archive, path, f"kernel-test/{directory}/{name}", mode)
        filenames.append(name)
    return filenames


def module_release(modules):
    releases = set()
    for path in modules:
        magic = subprocess.check_output(["modinfo", "-F", "vermagic", path.resolve()], text=True).split()
        if not magic or not magic[0].isascii():
            raise ValueError(f"module has no usable vermagic: {path}")
        releases.add(magic[0])
    if len(releases) > 1:
        raise ValueError("supplied modules do not share one kernel release")
    return next(iter(releases), "")


def pack(args, rootfs, tmp):
    busybox = shutil.which(args.busybox)
    if not busybox:
        raise ValueError("provide an x86_64 static BusyBox with --busybox")
    elf = subprocess.check_output(["readelf", "-lhW", busybox], env={**os.environ, "LC_ALL": "C"})
    if b"INTERP" in elf or b"X86-64" not in elf:
        raise ValueError("guest control requires an x86_64 static BusyBox")
    overlay = tmp / "overlay.tar"
    with tarfile.open(overlay, "x") as archive:
        for directory in ("dev", "proc", "sys", "kernel-test", "kernel-test/modules", "kernel-test/input"):
            info = tarfile.TarInfo(directory)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(info)
        console = tarfile.TarInfo("dev/console")
        console.type, console.mode = tarfile.CHRTYPE, 0o600
        console.devmajor, console.devminor = 5, 1
        archive.addfile(console)
        file_entry(archive, HERE / "init.sh", "kernel-test/init", 0o755)
        file_entry(archive, Path(busybox), "kernel-test/busybox", 0o755)
        file_entry(archive, args.workload, "kernel-test/workload", 0o755)
        modules = args.module + args.late_module
        if any(path.suffix != ".ko" for path in modules):
            raise ValueError("guest modules must be uncompressed .ko files")
        names = named_files(archive, modules, "modules", 0o444)
        text_entry(archive, "kernel-test/module-release", [module_release(modules)])
        text_entry(archive, "kernel-test/preload", names[:len(args.module)])
        text_entry(archive, "kernel-test/late", names[len(args.module):])
        text_entry(archive, "kernel-test/mode", [args.mode])
        named_files(archive, args.payload, "input", 0o555)
    image = tmp / "initramfs.cpio.gz"
    # Convert archive to archive: no untrusted rootfs path is extracted on the host.
    subprocess.run(["bsdtar", "-czf", image, "--format=newc", f"@{rootfs}", f"@{overlay}"], check=True)
    return image


def channel(name, path):
    escaped = str(path).replace(",", ",,")
    return ["-chardev", f"file,id={name},path={escaped}",
            "-device", f"virtserialport,chardev={name},name=kernel-test.{name}"]


def vm_command(args, image, output):
    accel = args.accel
    if accel == "auto":
        accel = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
    command = [args.qemu, "-no-user-config", "-nodefaults", "-no-reboot", "-machine", "pc",
               "-accel", accel, "-m", str(args.memory), "-smp", str(args.cpus),
               "-nic", "none", "-display", "none", "-monitor", "none", "-serial", "stdio",
               "-device", "virtio-serial-pci", "-kernel", args.kernel, "-initrd", image,
               "-append", "console=ttyS0,115200 rdinit=/kernel-test/init panic=-1"]
    command += channel("status", output / "guest.status")
    if args.mode == "profile":
        command += channel("trace", output / "trace.pending")
    return command


def check_trace(path):
    with nonempty_file(path).open("rb") as source:
        if not re.fullmatch(rb"KCFG-TRACE\t1\t[0-9a-f]{64}\n", source.readline()):
            raise ValueError(f"invalid trace header: {path}")
        seen = set()
        for line in source:
            if line.startswith(b"END\t"):
                count = len(seen)
                if count and max(seen) == count and line == f"END\t{count}\n".encode("ascii") and not source.read(1):
                    return
                break
            row = re.fullmatch(rb"([1-9][0-9]{0,4})\t[01]\t[01]\t[01]\n", line)
            if not row:
                break
            number = int(row[1])
            if number > 65535 or number in seen:
                break
            seen.add(number)
    raise ValueError(f"malformed or incomplete trace: {path}")


def validate(output, args):
    status = (output / "guest.status").read_bytes().decode("ascii")
    rows = status.removesuffix("\n").split("\n")
    info = dict(line.split("=", 1) for line in rows)
    expected = {"distro": "ubuntu", "mode": args.mode, "ready": "yes",
                "late_modules": str(len(args.late_module))}
    if (not status.endswith("\nstatus=ok\n") or len(rows) != len(info)
            or not info.get("kernel") or not info.get("version")
            or any(info.get(key) != value for key, value in expected.items())):
        raise ValueError(f"guest did not finish ({info}); inspect {output}/console.log")
    if args.mode == "profile":
        trace = output / "trace.pending"
        check_trace(trace)
        trace.rename(output / "trace.tsv")
    return info


def run(args):
    if min(args.memory, args.cpus, args.timeout) <= 0:
        raise ValueError("memory, cpus and timeout must be positive")
    if not args.kernel.is_file() or not args.workload.is_file():
        raise ValueError("--kernel and --workload must name existing regular files")
    rootfs = nonempty_file(args.rootfs) if args.rootfs else fetch(args.cache)
    output = args.output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="image-", dir=output) as tmp:
        image = pack(args, rootfs, Path(tmp))
        with (output / "console.log").open("xb") as console:
            try:
                subprocess.run(vm_command(args, image, output), check=True, timeout=args.timeout,
                               stdin=subprocess.DEVNULL, stdout=console, stderr=subprocess.STDOUT)
            except subprocess.TimeoutExpired as error:
                raise ValueError(f"guest exceeded {args.timeout}s; inspect {console.name}") from error
            except subprocess.CalledProcessError as error:
                raise ValueError(f"QEMU exited {error.returncode}; inspect {console.name}") from error
    info = validate(output, args)
    print(f"Ubuntu {info['version']}, Linux {info['kernel']}: {args.mode} passed ({output})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("fetch", help="reuse or download the default Ubuntu Base rootfs")
    download.add_argument("--cache", type=Path, default=CACHE)
    boot = commands.add_parser("run", help="boot Ubuntu and check the workload's exit status")
    boot.add_argument("--kernel", required=True, type=Path, help="x86_64 bzImage")
    boot.add_argument("--workload", required=True, type=Path, help="guest executable or shebang script")
    boot.add_argument("--output", required=True, type=Path, help="new directory; never overwritten")
    boot.add_argument("--mode", required=True, choices=MODES)
    boot.add_argument("--module", type=Path, action="append", default=[], help="ordered guest insmod before workload")
    boot.add_argument("--late-module", type=Path, action="append", default=[], help="ordered guest insmod after readiness, just before workload")
    boot.add_argument("--payload", type=Path, action="append", default=[], help="file available under /kernel-test/input")
    boot.add_argument("--rootfs", type=Path, help="offline Ubuntu Base amd64 tar archive")
    boot.add_argument("--cache", type=Path, default=CACHE)
    boot.add_argument("--qemu", default="qemu-system-x86_64")
    boot.add_argument("--busybox", default="busybox", help="static x86_64 BusyBox for insmod and reboot")
    boot.add_argument("--accel", choices=("auto", "kvm", "tcg"), default="auto")
    boot.add_argument("--memory", type=int, default=1536, help="guest memory in MiB")
    boot.add_argument("--cpus", type=int, default=2)
    boot.add_argument("--timeout", type=int, default=180, help="guest boot and workload deadline in seconds")
    args = parser.parse_args()
    if args.command == "fetch":
        print(fetch(args.cache))
    else:
        run(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"guest: {error}")
