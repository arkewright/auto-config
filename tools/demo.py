#!/usr/bin/env -S uv run --no-project python -I
"""Run the x86_64 reference experiment with real builds and fresh Ubuntu boots."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "tools/kernel.py"
GUEST = ROOT / "qemu/guest.py"
IMAGE = Path("arch/x86/boot/bzImage")


def run(*args, expected=0):
    result = subprocess.run(list(map(str, args)), check=False)
    if result.returncode != expected:
        raise ValueError(f"{args[0]} returned {result.returncode}, expected {expected}")


def experiment(args):
    out = args.out.absolute()
    host = args.host_build.resolve(strict=True)
    plugin = (host / "plugin/kcfg_frontend.so").resolve(strict=True)
    planner = (host / "processor/kcfg-plan").resolve(strict=True)
    out.mkdir(parents=True, exist_ok=False)
    common = ["--linux", args.linux.resolve(), "--llvm", args.llvm.resolve(), "--jobs", args.jobs]
    board, baseline, profile, plan, candidate = (out / name for name in ("board", "baseline", "profile", "plan", "candidate"))

    def boot(mode, image, label, *extra, expected=0):
        run(sys.executable, "-I", GUEST, "run", "--mode", mode, "--kernel", image / IMAGE,
            "--workload", ROOT / "examples/workload.sh", "--output", out / label, "--accel", args.accel,
            *extra, expected=expected)
        release = (image / "include/config/kernel.release").read_text().strip()
        if f"kernel={release}" not in (out / label / "guest.status").read_text().splitlines():
            raise ValueError(f"{label}: guest release differs from the supplied build")

    # Start from a bootable board envelope. Only release the two audited optional
    # symbols when Kconfig chooses its broadest setting; do not enable unrelated hardware.
    run(sys.executable, "-I", KERNEL, "configure", *common, "--base", "allnoconfig", "--fragment", ROOT / "qemu/guest.config",
        "--fragment", ROOT / "examples/profile.config", "--out", board)
    run(sys.executable, "-I", KERNEL, "configure", *common, "--base", "allyesconfig", "--fragment", board / ".config",
        "--unpin", "CONFIG_IKCONFIG", "--unpin", "CONFIG_NLS_CODEPAGE_850", "--out", baseline)
    run(sys.executable, "-I", KERNEL, "plain", *common, "--baseline", baseline / ".config", "--out", out / "plain")
    boot("baseline", out / "plain", "baseline-guest")
    run(sys.executable, "-I", KERNEL, "profile", *common, "--baseline", baseline / ".config", "--scope", ROOT / "examples/qemu.scope.tsv",
        "--plugin", plugin, "--out", profile)
    boot("profile", profile / "kernel", "capture", "--module", profile / "driver/kcfg_collect.ko")
    run(planner, profile / "campaign", out / "capture/trace.tsv", plan)
    wanted = {"CONFIG_IKCONFIG\ty\tm", "CONFIG_NLS_CODEPAGE_850\ty\tn"}
    if set((plan / "changes.tsv").read_text().splitlines()) != wanted:
        raise ValueError(f"unexpected evidence from reference workload; inspect {plan / 'report.tsv'}")
    run(sys.executable, "-I", KERNEL, "candidate", *common, "--campaign", profile / "campaign", "--plan", plan, "--out", candidate)
    late = []
    for module in (candidate / "deferred-paths.txt").read_text().splitlines():
        late.extend(["--late-module", candidate / module])
    boot("candidate", candidate / "kernel", "candidate-guest", *late)
    boot("candidate", candidate / "kernel", "without-late-module", expected=1)
    negative = (out / "without-late-module/guest.status").read_text().splitlines()
    if "stage=workload" not in negative or "status=failed" not in negative:
        raise ValueError("negative control did not fail in the workload")
    result = {"baseline": "passed", "instrumented": "passed", "candidate": "passed", "without_deferred_module": "workload failed as expected",
              "baseline_image_bytes": (out / "plain" / IMAGE).stat().st_size,
              "candidate_image_bytes": (candidate / "kernel" / IMAGE).stat().st_size}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Ubuntu reference workload verified: {candidate / '.config'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new experiment directory")
    parser.add_argument("--llvm", type=Path, default=os.environ.get("SDK", ROOT / ".deps/llvm"))
    parser.add_argument("--linux", type=Path, default=os.environ.get("LINUX", ROOT / ".deps/linux"))
    parser.add_argument("--host-build", type=Path, default=ROOT / "build/host")
    parser.add_argument("--jobs", type=int, default=min(8, len(os.sched_getaffinity(0))))
    parser.add_argument("--accel", choices=("auto", "kvm", "tcg"), default="auto")
    experiment(parser.parse_args())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"demo: {error}")
