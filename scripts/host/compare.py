"""Compare board archives and successful kernel-to-ready boot samples."""

import argparse
import re
import stat
import statistics
from pathlib import Path

BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def read_samples(path, kind, release, seen):
    times = []
    with path.open(encoding="ascii") as samples:
        for number, line in enumerate(samples, 1):
            fields = line.removesuffix("\n").split("\t")
            if len(fields) != 4:
                raise ValueError(f"{path}:{number}: expected role, release, boot ID and ready_us")
            role, version, boot_id, ready_us = fields
            if (role, version) != (kind, release):
                raise ValueError(f"{path}:{number}: expected {kind} release {release}")
            if not BOOT_ID.fullmatch(boot_id):
                raise ValueError(f"{path}:{number}: invalid boot ID")
            if boot_id in seen:
                raise ValueError(f"{path}:{number}: duplicate boot ID {boot_id}")
            if not re.fullmatch(r"[1-9][0-9]{0,19}", ready_us):
                raise ValueError(f"{path}:{number}: invalid ready_us")
            seen.add(boot_id)
            times.append(int(ready_us))
    if not times:
        raise ValueError(f"{path}: no successful boot samples")
    return times


def measure(work, kind, seen):
    release_file = work / kind / "kernel/include/config/kernel.release"
    release = release_file.read_text(encoding="ascii").removesuffix("\n")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", release):
        raise ValueError(f"{release_file}: invalid kernel release")
    archive = work / "packages" / f"{kind}.tar.gz"
    info = archive.stat()
    if not stat.S_ISREG(info.st_mode) or not info.st_size:
        raise ValueError(f"{archive}: expected a nonempty archive file")
    samples = read_samples(work / f"{kind}.tsv", kind, release, seen)
    return info.st_size, len(samples), statistics.median(samples)


def compare(work):
    seen = set()
    baseline_bytes, baseline_count, baseline_us = measure(work, "baseline", seen)
    candidate_bytes, candidate_count, candidate_us = measure(work, "candidate", seen)
    print(f"package bytes: baseline={baseline_bytes} candidate={candidate_bytes} "
          f"reduction={(baseline_bytes - candidate_bytes) / baseline_bytes:+.3%}")
    print(f"kernel-to-ready median (seconds): baseline={baseline_us / 1_000_000:.7f} (n={baseline_count}) "
          f"candidate={candidate_us / 1_000_000:.7f} (n={candidate_count}) "
          f"reduction={(baseline_us - candidate_us) / baseline_us:+.3%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", metavar="WORK", type=Path)
    args = parser.parse_args()
    try:
        compare(args.work)
    except (OSError, ValueError) as error:
        parser.exit(1, f"compare: {error}\n")


if __name__ == "__main__":
    main()
