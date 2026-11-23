#!/usr/bin/env -S uv run --no-project python -I
"""Configure, instrument, or validate a kernel in a new, user-owned build tree."""

import argparse
import base64
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "driver"
CAPTURE_CONFIG = {"CONFIG_MODULES": "y", "CONFIG_TRACEPOINTS": "y", "CONFIG_TRIM_UNUSED_KSYMS": "n"}
SYMBOL = r"CONFIG_[A-Za-z0-9_]+"
MODULE = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
SOURCE = r"(?:[A-Za-z0-9_+-]+/)+[A-Za-z0-9_+-]+\.c"


def require(test, message):
    if not test:
        raise ValueError(message)


def text(command, env=None):
    return subprocess.check_output(command, env=env, text=True).strip()


def fresh(path):
    path = path.absolute()
    path.mkdir(parents=True, exist_ok=False)
    return path.resolve()


def config_values(path):
    result = {}
    for line in path.read_text().splitlines():
        on = re.fullmatch(rf"({SYMBOL})=(.+)", line)
        off = re.fullmatch(rf"# ({SYMBOL}) is not set", line)
        if on or off:
            key = (on or off)[1]
            require(key not in result, f"duplicate {key} in {path}")
            result[key] = on[2] if on else "n"
        elif line and not line.startswith("#"):
            raise ValueError(f"invalid configuration line in {path}: {line!r}")
    return result


def same_config(want, got, context):
    changes = [f"{s}: {want.get(s, 'n')} -> {got.get(s, 'n')}" for s in sorted(want.keys() | got.keys())
               if want.get(s, "n") != got.get(s, "n")]
    require(not changes, f"{context}:\n" + "\n".join(changes))


def supported_source(path):
    return bool(re.fullmatch(SOURCE, path)) and (path.startswith(("drivers/", "fs/", "net/", "sound/")) or path == "kernel/configs.c")


def read_scope(path, linux):
    rows, sources, choices, owners = {}, set(), {}, {}
    for no, line in enumerate(path.read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        require(len(fields) == 5, f"scope line {no}: expected five tab-separated fields")
        number, symbol, module, policy, src = fields
        require(re.fullmatch(r"[1-9][0-9]{0,4}", number) and int(number) <= 65535, f"invalid id: {number}")
        i = int(number)
        require(re.fullmatch(SYMBOL, symbol) and policy in {"keep", "defer", "drop"}, f"invalid policy: {line}")
        require(re.fullmatch(MODULE, module) or (module == "-" and policy != "defer"), f"invalid module: {module}")
        require(re.fullmatch(SOURCE, src) and (linux / src).is_file() and not (linux / src).is_symlink(), f"invalid source: {src}")
        require(supported_source(src), f"outside supported subsystem scope: {src}")
        require(i not in rows and src not in sources, f"duplicate source or id: {line}")
        require(choices.setdefault(symbol, (policy, module)) == (policy, module), f"conflicting owners for {symbol}")
        if module != "-":
            require(owners.setdefault(module.replace("-", "_"), symbol) == symbol, f"ambiguous module: {module}")
        rows[i] = (symbol, module, policy, src)
        sources.add(src)
    require(rows and set(rows) == set(range(1, len(rows) + 1)), "scope ids must be dense from 1")
    return rows


class Toolchain:
    def __init__(self, args):
        self.linux = args.linux.resolve(strict=True)
        self.env = dict(os.environ, LC_ALL="C", KCONFIG_CONFIG=".config")
        # An inherited fragment or compiler override must not leak into Kconfig.
        for name in ("KCONFIG_ALLCONFIG", "KBUILD_OUTPUT", "KBUILD_EXTMOD", "KBUILD_EXTMOD_OUTPUT", "MAKEFLAGS", "MFLAGS"):
            self.env.pop(name, None)
        sdk = args.llvm.resolve() if args.llvm else None
        if sdk:
            for var, leaf in (("PATH", "bin"), ("LD_LIBRARY_PATH", "lib")):
                self.env[var] = str(sdk / leaf) + (":" + self.env[var] if self.env.get(var) else "")
        compiler = args.cc or (str(sdk / "bin/clang") if sdk else "clang")
        cc = shutil.which(compiler, path=self.env["PATH"])
        require(cc, f"Clang not found: {compiler}")
        self.cc = str(Path(cc).absolute())  # preserve clang invocation name
        require((self.linux / "Makefile").is_file(), f"missing kernel Makefile: {self.linux}")
        self.arch = args.arch
        require(re.fullmatch(r"[A-Za-z0-9_]+", args.arch) and args.jobs > 0, "invalid architecture or jobs")
        self.flags = [f"ARCH={args.arch}", f"LLVM={sdk / 'bin'}/" if sdk else "LLVM=1", f"-j{args.jobs}", "KBUILD_ABS_SRCTREE=1"]
        if args.hostcc:
            self.flags.append(f"HOSTCC={args.hostcc}")
        if args.hostcxx:
            self.flags.append(f"HOSTCXX={args.hostcxx}")
        if args.host_sysroot:
            for flag in ("HOSTCFLAGS", "HOSTCXXFLAGS", "HOSTLDFLAGS"):
                self.flags.append(f"{flag}=--sysroot={args.host_sysroot.resolve(strict=True)}")
        self.nm = (sdk / "bin/llvm-nm") if sdk else "llvm-nm"

    def kernel_version(self):
        return text(["make", "-s", "--no-print-directory", "-C", self.linux, "kernelversion"], self.env)

    def make(self, output, *targets, cc=None):
        subprocess.run(["make", "-s", "--no-print-directory", "-C", self.linux, f"O={output}", *self.flags,
                        f"CC={cc or self.cc}", *targets], check=True, env=self.env)


def apply_values(path, values):
    changes = {key: f"# {key} is not set" if value == "n" else f"{key}={value}" for key, value in values.items()}
    lines = path.read_text().splitlines()
    for n, line in enumerate(lines):
        match = re.match(rf"(?:# )?({SYMBOL})(?:=| is not set$)", line)
        if match and match[1] in changes:
            lines[n] = changes.pop(match[1])
    path.write_text("\n".join([*lines, *changes.values()]) + "\n")


def board_config(args, kit):
    out = fresh(args.out)
    cfg = out / ".config"
    kit.make(out, "allyesconfig")
    shutil.copyfile(cfg, out / "native.config")
    native = config_values(cfg)
    wanted = config_values(args.fragment) if args.fragment else {}
    for key, value in CAPTURE_CONFIG.items():
        require(wanted.setdefault(key, value) == value, f"profiling requires {key}={value}")
    apply_values(cfg, {k: v for k, v in wanted.items() if native.get(k, "n") != v})
    kit.make(out, "olddefconfig")
    actual = config_values(cfg)
    same_config(wanted, {k: actual.get(k, "n") for k in wanted}, "Kconfig rejected board requirements")
    with (out / "changes.tsv").open("w") as diff:
        for key in sorted(native.keys() | actual.keys()):
            before, after = native.get(key, "n"), actual.get(key, "n")
            if before != after:
                print(key, before, after, sep="\t", file=diff)
    print(f"Board baseline: {cfg}; changes from native allyesconfig: {out / 'changes.tsv'}")


def configure(args, kit):
    fragments = {}
    for path in args.fragment:
        for key, value in config_values(path).items():
            require(fragments.setdefault(key, value) == value, f"conflicting fragments: {key}")
    if args.unpin:
        require(args.base in {"allyesconfig", "allmodconfig"}, "--unpin is for broad baseline targets")
        for key in args.unpin:
            require(key in fragments, f"cannot unpin absent option: {key}")
            del fragments[key]
    out = fresh(args.out)
    if args.base in {"allyesconfig", "allnoconfig", "alldefconfig", "allmodconfig"}:
        overrides = []
        if fragments:
            fragment = out / "board.fragment"
            fragment.touch()
            apply_values(fragment, fragments)
            overrides.append(f"KCONFIG_ALLCONFIG={fragment}")
        kit.make(out, *overrides, args.base)
    else:
        require(re.fullmatch(r"(?:[\w-]+_)?defconfig", args.base), "invalid base target")
        kit.make(out, args.base)
        apply_values(out / ".config", fragments)
        kit.make(out, "olddefconfig")
    actual = config_values(out / ".config")
    same_config(fragments, {s: actual.get(s, "n") for s in fragments}, "Kconfig rejected board requirements")
    print(out / ".config")


def compiler_wrapper(kit, campaign, plugin, rows):
    q = lambda value: shlex.quote(str(value))
    conf, receipts = campaign / "bridge-config.h", campaign / "receipts"
    lookup = fresh(campaign / "by-source")
    for i, (_, _, _, source) in rows.items():
        entry = lookup / source
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(f"{i}\n")
    # Constant-size wrapper, even when the board covers thousands of files.
    # Only the normal .o compile may publish a receipt; genksyms/preprocessing must still see the bridge.
    wrapper = campaign / "clang"
    wrapper.write_text(f'''#!/bin/sh
src={q(kit.linux)}; map={q(lookup)}
id=0; obj=; next=0; compile=0
for a do
    if [ "$next" = 1 ]; then obj=$a; next=0; continue; fi
    case "$a" in
    -o) next=1 ;; -c) compile=1 ;;
    "$src/init/init_task.c") id=bridge ;;
    "$src/"*.c) file=$map/${{a#"$src/"}}; if [ -f "$file" ]; then IFS= read -r id < "$file" || exit 1; fi ;;
    esac
done
case "$id" in
bridge) exec {q(kit.cc)} "$@" -include {q(conf)} -include {q(DRIVER / 'bridge.c')} ;;
0) ;;
*) case "$compile:$obj" in
   1:*.o) exec {q(kit.cc)} -include {q(conf)} -include {q(DRIVER / 'probe.h')} -fplugin={q(plugin)} \\
       "-fplugin-arg-kcfgtrace-id=$id" "-fplugin-arg-kcfgtrace-receipt="{q(receipts)}/"$id" "$@" ;;
   esac ;;
esac
exec {q(kit.cc)} "$@"
''')
    wrapper.chmod(0o755)
    return wrapper


def read_campaign_id(directory):
    value = (directory / "campaign.id").read_bytes()
    require(re.fullmatch(rb"[0-9a-f]{64}\n", value), f"invalid campaign.id in {directory}")
    return value[:-1].decode("ascii")


def compiled_owners(cmd):
    if not cmd.is_file():
        return ()
    with cmd.open(errors="replace") as stream:
        # Kbuild quotes the C string for the shell; no saved command is executed.
        match = re.search(r'''-DKBUILD_MODNAME='?"([A-Za-z0-9_:-]+)"''', stream.readline())
    return tuple(name.replace("-", "_") for name in match[1].split(":")) if match else ()


def object_owner(target):
    names = compiled_owners(target.with_name(f".{target.name}.cmd"))
    return names[0] if len(names) == 1 else None


def omitted_members(obj, rows):
    # A known Kbuild sibling outside the manifest disqualifies the entire option.
    # A basename collision can retain too much, never discard an observed sibling.
    expected = {}
    for symbol, _, policy, source in rows.values():
        target = (obj / source).with_suffix(".o")
        owner = object_owner(target)
        if owner and policy != "keep":
            expected.setdefault(owner, {}).setdefault(symbol, set()).add(target)
    missing = {}
    if expected:
        for cmd in obj.rglob(".*.o.cmd"):
            if cmd.name.endswith(".mod.o.cmd"):
                continue
            target = cmd.with_name(cmd.name[1:-4])
            for owner in compiled_owners(cmd):
                for symbol, listed in expected.get(owner, {}).items():
                    if target not in listed:
                        missing.setdefault(symbol, target.relative_to(obj))
    return missing


def literal_bindings(directory):
    """Only direct, unconditional obj-$(CONFIG_...) rules are a source of new suggestions."""
    makefile = directory / "Kbuild"
    if not makefile.is_file():
        makefile = directory / "Makefile"
    if not makefile.is_file():
        return {}
    rules = makefile.read_text(errors="replace").replace("\\\n", " ").splitlines()
    bindings = {}
    nesting = 0
    for line in rules:
        rule = line.split("#", 1)[0].strip()
        if re.match(r"(?:ifeq|ifneq|ifdef|ifndef)\b", rule):
            nesting += 1
        elif rule == "endif":
            nesting = max(0, nesting - 1)
        elif not nesting and (m := re.fullmatch(rf"obj-\$\(({SYMBOL})\)\s*(?:\+=|:=|=)\s*(.+)", rule)):
            for name in m[2].split():
                if re.fullmatch(r"[\w+-]+\.o", name):
                    src = directory / (name[:-2] + ".c")
                    if src.is_file():
                        bindings.setdefault(src, set()).add(m[1])
    return bindings


def inventory(args):
    linux = args.linux.resolve(strict=True)
    base = (linux / args.under).resolve(strict=True)
    require(base.is_relative_to(linux) and base.is_dir(), "--under must name a Linux source directory")
    directories = {p.parent for name in ("Makefile", "Kbuild") for p in base.rglob(name)}
    drop, defer = set(args.drop), set(args.defer)
    require(not drop & defer, f"conflicting policies: {', '.join(sorted(drop & defer))}")
    selected = set(args.symbol) | drop | defer
    rows = []
    for directory in sorted(directories):
        for src, symbols in sorted(literal_bindings(directory).items()):
            path = src.relative_to(linux).as_posix()
            if len(symbols) != 1 or not supported_source(path):
                continue
            symbol, = symbols
            rows.append((symbol, src.stem.replace("-", "_"), path))
    counts, owners = {}, {}
    for symbol, module, _ in rows:
        counts[symbol] = counts.get(symbol, 0) + 1
        owners.setdefault(module, set()).add(symbol)
    if selected:
        missing = selected - counts.keys()
        require(not missing, f"no direct binding for {', '.join(sorted(missing))}; supply an audited scope instead")
        rows = [row for row in rows if row[0] in selected]
    require(0 < len(rows) <= 65535, "no direct bindings or inventory too large; choose a subsystem")
    output = ["# id\tKconfig symbol\tmodule\tpolicy\ttranslation unit\n"]
    for i, (symbol, module, path) in enumerate(rows, 1):
        name = module if counts[symbol] == 1 and len(owners[module]) == 1 else "-"
        policy = "drop" if symbol in drop else "defer" if symbol in defer else "keep"
        require(policy != "defer" or name != "-", f"no unique module for {symbol}; supply an audited scope instead")
        output.append(f"{i}\t{symbol}\t{name}\t{policy}\t{path}\n")
    with args.out.open("x") as stream:
        stream.writelines(output)


def attest_objects(kit, campaign, obj, rows, values, symbol):
    missing = omitted_members(obj, rows)
    sources = {kit.linux / src for _, _, _, src in rows.values()}
    scoped = {opt for opt, _, policy, _ in rows.values() if policy != "keep"}
    bindings = {src: opts for directory in {src.parent for src in sources}
                for src, opts in literal_bindings(directory).items()}
    for src, opts in bindings.items():
        target = src.relative_to(kit.linux).with_suffix(".o")
        if src not in sources and (obj / target).is_file():
            for opt in scoped & opts:
                missing.setdefault(opt, target)
    for opt, target in sorted(missing.items()):
        print(f"keeping {opt}: Kbuild also compiled unlisted member {target}", file=sys.stderr)
    cover, objects = [], ["id\tsource\tkbuild_module\tverified_sites\n"]
    for i, (opt, declared, _, source) in sorted(rows.items()):
        receipt = campaign / "receipts" / str(i)
        target = (obj / source).with_suffix(".o")
        owner = object_owner(target)
        direct = bindings.get(kit.linux / source)
        count = 0
        if opt not in missing and owner and receipt.is_file() and target.is_file():
            raw = receipt.read_text()
            require(re.fullmatch(r"(?:0|[1-9][0-9]*)\n", raw), f"invalid compiler receipt: {source}")
            undefined = text([kit.nm, "--undefined-only", target], kit.env)
            owner_ok = declared == "-" or declared.replace("-", "_") == owner.replace("-", "_")
            if owner_ok and (direct is None or direct == {opt}) and any(line.split()[-1:] == [symbol] for line in undefined.splitlines()):
                count = int(raw)
        if count == 0 and values.get(opt, "n") != "n":
            print(f"keeping {opt}: could not attest probe and ownership of {source}", file=sys.stderr)
        cover.append(f"{i}\t{count}\n")
        objects.append(f"{i}\t{source}\t{owner or '-'}\t{count}\n")
    (campaign / "objects.tsv").write_text("".join(objects))
    (campaign / "coverage.tsv").write_text("".join(cover))


def build_targets(arch, image):
    if arch in {"riscv", "arm64", "arm"}:
        return (image or ("zImage" if arch == "arm" else "Image"), "modules", "dtbs")
    return (image or "bzImage", "modules")


def profile(args, kit):
    rows = read_scope(args.scope, kit.linux)
    plugin = args.plugin.resolve(strict=True)
    out = fresh(args.out)
    campaign, obj = fresh(out / "campaign"), fresh(out / "kernel")
    fresh(campaign / "receipts")
    shutil.copyfile(args.baseline, obj / ".config")
    kit.make(obj, "olddefconfig")
    values = config_values(obj / ".config")
    same_config(config_values(args.baseline), values, "baseline is not normalized for this target")
    for sym, val in CAPTURE_CONFIG.items():
        require(values.get(sym, "n") == val, f"profiling requires {sym}={val}")
    shutil.copyfile(obj / ".config", campaign / "baseline.config")
    shutil.copyfile(args.scope, campaign / "scope.tsv")
    key = secrets.token_hex(32)
    (campaign / "campaign.id").write_text(key + "\n")
    # BASIC_MODVERSIONS allows 55 characters plus NUL on 64-bit targets.
    symbol = "kc_" + base64.b32encode(bytes.fromhex(key)).decode("ascii").rstrip("=").lower()
    (campaign / "bridge-config.h").write_text(f'#define KCFG_ID "{key}"\n#define KCFG_COUNT {len(rows)}\n'
                                               f'#define KCFG_LINK_NAME "{symbol}"\n#define KCFG_LINK_SYMBOL {symbol}\n')
    wrapper = compiler_wrapper(kit, campaign, plugin, rows)
    release_suffix = f"LOCALVERSION=-kcfg-{key[:16]}"
    kit.make(obj, release_suffix, "init/init_task.o", cc=wrapper)
    kit.make(obj, release_suffix, *build_targets(kit.arch, args.image_target), cc=wrapper)
    mod = fresh(out / "driver")
    kit.make(obj, release_suffix, f"M={DRIVER}", f"MO={mod}", "modules")
    require((mod / "kcfg_collect.ko").is_file(), "collector not built")
    require(any(line.split()[1:2] == [symbol] for line in (obj / "Module.symvers").read_text().splitlines()), "built-in probe not exported")
    attest_objects(kit, campaign, obj, rows, values, symbol)
    build = {"id": key, "arch": kit.arch, "kernel_version": kit.kernel_version(),
             "release": (obj / "include/config/kernel.release").read_text().strip()}
    (campaign / "build.json").write_text(json.dumps(build, indent=2) + "\n")
    print(f"Profile kernel: {obj}; collector: {mod / 'kcfg_collect.ko'}; campaign: {campaign}")


def plain(args, kit):
    obj = fresh(args.out)
    shutil.copyfile(args.baseline, obj / ".config")
    kit.make(obj, "olddefconfig")
    same_config(config_values(args.baseline), config_values(obj / ".config"), "baseline changed")
    kit.make(obj, *build_targets(kit.arch, args.image_target))
    print(f"Pristine baseline build: {obj}")


def requested_values(campaign, plan, rows):
    baseline = config_values(campaign / "baseline.config")
    wanted = baseline.copy()
    allowed = {symbol: policy for symbol, _, policy, _ in rows.values()}
    coverage = {int(i): int(n) for i, n in (s.split("\t") for s in (campaign / "coverage.tsv").read_text().splitlines())}
    for i, (symbol, _, _, _) in rows.items():
        if coverage.get(i, 0) <= 0:
            allowed[symbol] = "keep"
    seen = set()
    for line in (plan / "changes.tsv").read_text().splitlines():
        key, old, new = line.split("\t")
        require(key not in seen and baseline.get(key) == old, f"stale proposal: {key}")
        require((allowed.get(key), old, new) in {("defer", "y", "m"), ("drop", "y", "n"), ("drop", "m", "n")}, f"unauthorized change: {key}")
        seen.add(key)
        wanted[key] = new
    same_config(wanted, config_values(plan / "requested.config"), "proposal differs from approved changes")
    return wanted


def module_paths(obj, modules):
    normalize = lambda s: s.replace("-", "_")
    paths = {}
    for item in (obj / "modules.order").read_text().splitlines():
        relative = Path(item).with_suffix(".ko")
        require(not relative.is_absolute() and ".." not in relative.parts, "invalid module path")
        paths.setdefault(normalize(relative.stem), []).append(relative)
    pending, result = set(), []

    def visit(name):
        choices = paths.get(normalize(name), [])
        require(len(choices) == 1, f"missing or ambiguous built module: {name}")
        relative = choices[0]
        if relative in result:
            return
        require(relative not in pending, f"cyclic module dependency: {relative}")
        pending.add(relative)
        deps = text(["modinfo", "-F", "depends", obj / relative])
        for dep in filter(None, deps.split(",")):
            visit(dep)
        pending.remove(relative)
        result.append(relative)

    for module in modules:
        visit(module)
    return result


def candidate(args, kit):
    campaign, plan = args.campaign.resolve(), args.plan.resolve()
    rows = read_scope(campaign / "scope.tsv", kit.linux)
    build = json.loads((campaign / "build.json").read_text())
    key = read_campaign_id(campaign)
    require(build["id"] == key, "mismatched campaign inputs")
    require(read_campaign_id(plan) == key, "proposal is for a different capture campaign")
    require(build["arch"] == kit.arch, "target architecture changed")
    if "kernel_version" in build:
        require(build["kernel_version"] == kit.kernel_version(), "kernel version changed since capture")
    wanted = requested_values(campaign, plan, rows)
    modules = (plan / "modules-load.late").read_text().splitlines()
    baseline = config_values(campaign / "baseline.config")
    eligible = {m for sym, m, policy, _ in rows.values() if policy == "defer" and wanted.get(sym) == "m"}
    converted = {m for sym, m, _, _ in rows.values() if wanted.get(sym) == "m" and baseline.get(sym) == "y"}
    require(len(set(modules)) == len(modules) and converted <= set(modules) <= eligible, "invalid deferred-module list")
    out = fresh(args.out)
    obj = fresh(out / "kernel")
    shutil.copyfile(plan / "requested.config", obj / ".config")
    kit.make(obj, "olddefconfig")
    same_config(wanted, config_values(obj / ".config"), "Kconfig coerced the proposal; no final configuration published")
    kit.make(obj, *build_targets(kit.arch, args.image_target))
    same_config(wanted, config_values(obj / ".config"), "configuration changed during build")
    paths = module_paths(obj, modules)
    (out / "deferred-paths.txt").write_text("".join(f"kernel/{p}\n" for p in paths))
    result = out / ".config.pending"
    shutil.copyfile(obj / ".config", result)
    result.rename(out / ".config")
    print(f"Built uninstrumented candidate: {out / '.config'} (boot and workload replay still required)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    scan = sub.add_parser("inventory", help="map selected symbols to files using literal Kbuild rules")
    scan.add_argument("--drop", action="append", default=[], metavar="CONFIG_NAME")
    scan.add_argument("--defer", action="append", default=[], metavar="CONFIG_NAME")
    linux = os.environ.get("LINUX", ROOT / ".deps/linux")
    scan.add_argument("--linux", type=Path, default=linux, help="Linux source tree (default: $LINUX or .deps/linux)")
    scan.add_argument("--under", required=True, help="subsystem directory relative to Linux")
    scan.add_argument("--symbol", action="append", default=[], help="optional Kconfig symbol(s) of interest")
    scan.add_argument("--out", type=Path, required=True, help="new TSV file")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--linux", type=Path, default=linux, help="Linux source tree (default: $LINUX or .deps/linux)")
    common.add_argument("--llvm", type=Path, default=os.environ.get("SDK"),
                        help="LLVM SDK prefix (bin/ and lib/); default: $SDK or tools on PATH")
    common.add_argument("--cc", help="absolute kernel Clang executable or command on SDK PATH")
    common.add_argument("--hostcc", help="Kbuild host C compiler")
    common.add_argument("--hostcxx", help="Kbuild host C++ compiler")
    common.add_argument("--host-sysroot", type=Path, help="complete libc sysroot for host utilities, never the freestanding kernel")
    common.add_argument("--arch", default="x86_64")
    common.add_argument("--jobs", default=max(1, min(8, len(os.sched_getaffinity(0)))), type=int)
    common.add_argument("--out", required=True, type=Path, help="new output directory")
    native = sub.add_parser("board-config", parents=[common], help="start with native allyesconfig, then apply board requirements")
    native.add_argument("--fragment", type=Path, help="optional board adjustments applied after native allyesconfig")
    conf = sub.add_parser("configure", parents=[common])
    conf.add_argument("--base", default="defconfig", help="allyesconfig, allnoconfig, alldefconfig, allmodconfig or architecture defconfig")
    conf.add_argument("--fragment", action="append", type=Path, default=[])
    conf.add_argument("--unpin", action="append", default=[], help="let allyesconfig/allmodconfig choose this board-baseline symbol")
    prof = sub.add_parser("profile", parents=[common])
    prof.add_argument("--scope", type=Path, required=True)
    prof.add_argument("--baseline", type=Path, required=True)
    prof.add_argument("--plugin", type=Path, required=True)
    reference = sub.add_parser("plain", parents=[common], help="build a pristine uninstrumented baseline")
    reference.add_argument("--baseline", type=Path, required=True)
    final = sub.add_parser("candidate", parents=[common])
    final.add_argument("--campaign", type=Path, required=True)
    final.add_argument("--plan", type=Path, required=True)
    for cmd in (prof, final, reference):
        cmd.add_argument("--image-target", choices=("bzImage", "Image", "Image.gz", "Image.lz4", "vmlinuz.efi", "zImage"),
                         help="Kbuild boot image (default: Image on RISC-V/arm64, zImage on ARM, bzImage on x86)")
    args = parser.parse_args()
    if args.mode == "inventory":
        inventory(args)
        return
    actions = {"board-config": board_config, "configure": configure, "profile": profile, "candidate": candidate, "plain": plain}
    actions[args.mode](args, Toolchain(args))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        sys.exit(f"kernel: {exc}")
