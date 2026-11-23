#!/usr/bin/env -S uv run --no-project python -I
"""Reuse local source trees or clone a chosen tag or branch into an empty cache slot."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPOS = {
    "linux": ("torvalds/linux", "v7.4-rc3", "Makefile"),
    "ftxui": ("ArthurSonzogni/FTXUI", "v6.1.9", "CMakeLists.txt"),
    "llvm-project": ("llvm/llvm-project", "llvmorg-23.1.4", "llvm/CMakeLists.txt"),
}


def fetch(cache, name, ref, source=None):
    repo, _, entry = REPOS[name]
    target = source or cache / name
    if target.exists() or (source is not None and target.resolve() != (cache / name).resolve()):
        if not (target / entry).is_file():
            raise ValueError(f"{target}: missing {entry}; use a source tree or a new cache")
        print(f"Using existing {name}: {target}")
        return
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as stage:
        checkout = Path(stage) / "checkout"
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "--branch", ref,
                        f"https://github.com/{repo}.git", checkout], check=True)
        checkout.rename(target)
    print(f"Cloned {name} {ref}: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path(__file__).resolve().parents[1] / ".deps")
    parser.add_argument("--linux", dest="linux_tree", type=Path, default=os.environ.get("LINUX"),
                        help="reuse an existing Linux source directory instead of fetching it (default: $LINUX)")
    parser.add_argument("--llvm-source", action="store_true", help="also prepare sources to build a private SDK")
    parser.add_argument("--llvm-tree", type=Path, default=os.environ.get("LLVM_SOURCE"), help="existing llvm-project source tree")
    parser.add_argument("--ftxui-source", type=Path, default=os.environ.get("FTXUI_SOURCE_DIR"), help="existing FTXUI source tree")
    parser.add_argument("--ftxui-dir", type=Path, default=os.environ.get("FTXUI_DIR"), help="use an installed FTXUI CMake package")
    for name, (_, default, _) in REPOS.items():
        option = "llvm" if name == "llvm-project" else name
        env = f"{option.upper()}_REF"
        parser.add_argument(f"--{option}-ref", dest=name, metavar="REF", default=os.environ.get(env, default),
                            help=f"tag or branch for a new {name} clone (default: ${env} or {default})")
    args = parser.parse_args()
    fetch(args.cache, "linux", args.linux, args.linux_tree)
    if args.ftxui_dir and not args.ftxui_source:
        print(f"Using installed FTXUI: {args.ftxui_dir}")
    else:
        fetch(args.cache, "ftxui", args.ftxui, args.ftxui_source)
    if args.llvm_source:
        fetch(args.cache, "llvm-project", vars(args)["llvm-project"], args.llvm_tree)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"sources: {exc}")
