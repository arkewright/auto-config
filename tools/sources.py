#!/usr/bin/env -S uv run --no-project python -I
"""Fetch exact upstream checkouts into a user-owned cache; never reset an existing checkout."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPOS = {
    "linux": ("torvalds/linux", "v7.4-rc3", "aebb7274476403040d582c61478df8f4a7bf9cd3"),
    "ftxui": ("ArthurSonzogni/FTXUI", "v6.1.9", "5cfed50702f52d51c1b189b5f97f8beaf5eaa2a6"),
    "llvm-project": ("llvm/llvm-project", "llvmorg-23.1.4", "7a941dfb2877a0b8cf30323fefa85ad019e0b0f5"),
}


def verify(path, revision):
    git = ["git", "-C", path]
    head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    changes = subprocess.check_output([*git, "status", "--porcelain", "--untracked-files=normal"])
    if head != revision or changes:
        raise ValueError(f"{path} is not a clean pinned checkout; use a new directory")


def fetch(cache, name):
    repo, tag, revision = REPOS[name]
    target = cache / name
    if target.exists():
        verify(target, revision)
    else:
        with tempfile.TemporaryDirectory(dir=cache) as stage:
            checkout = Path(stage) / "checkout"
            subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "--branch", tag,
                            f"https://github.com/{repo}.git", checkout], check=True)
            verify(checkout, revision)
            checkout.rename(target)
    print(f"{name} {tag}: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path(__file__).resolve().parents[1] / ".deps")
    parser.add_argument("--llvm-source", action="store_true", help="also fetch sources to build a private SDK")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    for name in REPOS:
        if name != "llvm-project" or args.llvm_source:
            fetch(args.cache, name)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"sources: {exc}")
