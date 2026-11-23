"""Build-time data for the Clang probe, trace processor and reviewed Kconfig changes."""

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
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[1] / "driver"
CAPTURE_CONFIG = {"CONFIG_MODULES": "y", "CONFIG_TRACEPOINTS": "y", "CONFIG_TRIM_UNUSED_KSYMS": "n"}
SYMBOL = r"CONFIG_[A-Za-z0-9_]+"
MODULE = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
SOURCE = r"(?:[A-Za-z0-9_+-]+/)+[A-Za-z0-9_+-]+\.c"
SUBSYSTEMS = ("drivers", "fs", "net", "sound")


@dataclass(frozen=True)
class Unit:
    symbol: str
    module: str
    policy: str
    source: str


def config_values(path):
    values = {}
    for line in path.read_text().splitlines():
        on = re.fullmatch(rf"({SYMBOL})=(.*)", line)
        off = re.fullmatch(rf"# ({SYMBOL}) is not set", line)
        if on or off:
            key = (on or off)[1]
            if key in values:
                raise ValueError(f"duplicate {key} in {path}")
            values[key] = on[2] if on else "n"
        elif line and not line.startswith("#"):
            raise ValueError(f"invalid configuration line in {path}: {line!r}")
    return values


def supported_source(source):
    return (re.fullmatch(SOURCE, source) and
            (source.partition("/")[0] in SUBSYSTEMS or source == "kernel/configs.c"))


def read_scope(path, linux):
    indexed, choices, owners, sources = {}, {}, {}, set()
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 5:
            raise ValueError(f"{path}:{lineno}: expected id, symbol, module, policy and source")
        number, symbol, module, policy, source = fields
        if not re.fullmatch(r"[1-9][0-9]{0,4}", number) or int(number) > 65535:
            raise ValueError(f"invalid probe id: {number}")
        if not re.fullmatch(SYMBOL, symbol) or policy not in {"keep", "drop", "defer"}:
            raise ValueError(f"invalid policy: {line}")
        if not re.fullmatch(MODULE, module) and not (module == "-" and policy != "defer"):
            raise ValueError(f"invalid module: {module}")
        if not supported_source(source) or not (linux / source).is_file():
            raise ValueError(f"unsupported or missing source: {source}")
        if int(number) in indexed or source in sources:
            raise ValueError(f"duplicate source or id: {line}")
        if choices.setdefault(symbol, (module, policy)) != (module, policy):
            raise ValueError(f"conflicting module or policy for {symbol}")
        if module != "-" and owners.setdefault(module.replace("-", "_"), symbol) != symbol:
            raise ValueError(f"ambiguous module: {module}")
        indexed[int(number)] = Unit(symbol, module, policy, source)
        sources.add(source)
    if not indexed or max(indexed) != len(indexed):
        raise ValueError("scope ids must be dense from 1")
    return [indexed[i] for i in range(1, len(indexed) + 1)]


def literal_bindings(directory):
    """Read direct, unconditional obj-$(CONFIG_...) bindings with a C source."""
    makefile = directory / "Kbuild"
    if not makefile.is_file():
        makefile = directory / "Makefile"
    if not makefile.is_file():
        return {}
    bindings = defaultdict(set)
    nesting = 0
    for line in makefile.read_text(errors="replace").replace("\\\n", " ").splitlines():
        rule = line.partition("#")[0].strip()
        if re.match(r"(?:ifeq|ifneq|ifdef|ifndef)\b", rule):
            nesting += 1
        elif rule == "endif":
            nesting = max(0, nesting - 1)
        elif not nesting and (m := re.fullmatch(rf"obj-\$\(({SYMBOL})\)\s*(?:\+=|:=|=)\s*(.+)", rule)):
            for name in m[2].split():
                if re.fullmatch(r"[\w+-]+\.o", name):
                    source = directory / (name[:-2] + ".c")
                    if source.is_file():
                        bindings[source].add(m[1])
    return bindings


def inventory(linux, drop, defer):
    drop, defer = set(drop), set(defer)
    if drop & defer:
        raise ValueError("conflicting policies: " + ", ".join(sorted(drop & defer)))
    directories = {linux / "kernel"}
    for subsystem in SUBSYSTEMS:
        for directory, _, files in os.walk(linux / subsystem):
            if "Makefile" in files or "Kbuild" in files:
                directories.add(Path(directory))
    bindings = []
    for directory in sorted(directories):
        for source, symbols in sorted(literal_bindings(directory).items()):
            path = source.relative_to(linux).as_posix()
            if len(symbols) == 1 and supported_source(path):
                symbol, = symbols
                bindings.append((symbol, source.stem.replace("-", "_"), path))
    counts = Counter(symbol for symbol, _, _ in bindings)
    owners = defaultdict(set)
    for symbol, module, _ in bindings:
        owners[module].add(symbol)
    selected = drop | defer
    if missing := selected - counts.keys():
        raise ValueError(f"no direct binding for {', '.join(sorted(missing))}; supply a scope file")
    units = []
    for symbol, module, path in bindings:
        if selected and symbol not in selected:
            continue
        name = module if counts[symbol] == 1 and len(owners[module]) == 1 and re.fullmatch(MODULE, module) else "-"
        policy = "drop" if symbol in drop else "defer" if symbol in defer else "keep"
        if policy == "defer" and name == "-":
            raise ValueError(f"no unique module for {symbol}; supply a scope file")
        units.append(Unit(symbol, name, policy, path))
    if not 0 < len(units) <= 65535:
        raise ValueError("no direct bindings or more than 65535 units; supply a scope file")
    return units


def probe_symbol(key):
    # BASIC_MODVERSIONS allows 55 characters plus NUL on 64-bit targets.
    return "kc_" + base64.b32encode(bytes.fromhex(key)).decode("ascii").rstrip("=").lower()


def read_campaign_id(directory):
    value = (directory / "campaign.id").read_bytes()
    if not re.fullmatch(rb"[0-9a-f]{64}\n", value):
        raise ValueError(f"invalid campaign.id in {directory}")
    return value[:-1].decode("ascii")


def compiler_wrapper(linux, cc, campaign, plugin, units):
    lookup = campaign / "by-source"
    for i, unit in enumerate(units, 1):
        entry = lookup / unit.source
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(f"{i}\n")
    def quote(value):
        return shlex.quote(str(value))
    # genksyms must see the bridge, but only an actual .o compile writes a receipt.
    wrapper = campaign / "clang"
    wrapper.write_text(f'''#!/bin/sh
src={quote(linux)}; map={quote(lookup)}; cc={quote(cc)}; conf={quote(campaign / 'bridge-config.h')}
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
bridge) exec "$cc" "$@" -include "$conf" -include {quote(DRIVER / 'bridge.c')} ;;
0) ;;
*) case "$compile:$obj" in
   1:*.o) exec "$cc" -include "$conf" -include {quote(DRIVER / 'probe.h')} -fplugin={quote(plugin)} \\
       "-fplugin-arg-kcfgtrace-id=$id" "-fplugin-arg-kcfgtrace-receipt="{quote(campaign / 'receipts')}/"$id" "$@" ;;
   esac ;;
esac
exec "$cc" "$@"
''')
    wrapper.chmod(0o755)


def prepare(args):
    values = config_values(args.config)
    for symbol, value in CAPTURE_CONFIG.items():
        if values.get(symbol, "n") != value:
            raise ValueError(f"profiling requires {symbol}={value}")
    linux, campaign = args.linux.resolve(), args.campaign.resolve()
    if args.scope and (args.drop or args.defer):
        raise ValueError("choose a scope file or drop/defer policies")
    units = read_scope(args.scope, linux) if args.scope else inventory(linux, args.drop, args.defer)
    campaign.mkdir(parents=True)
    (campaign / "receipts").mkdir()
    shutil.copyfile(args.config, campaign / "baseline.config")
    if args.scope:
        shutil.copyfile(args.scope, campaign / "scope.tsv")
    else:
        with (campaign / "scope.tsv").open("w") as out:
            print("# id\tKconfig symbol\tmodule\tpolicy\ttranslation unit", file=out)
            for i, unit in enumerate(units, 1):
                print(i, unit.symbol, unit.module, unit.policy, unit.source, sep="\t", file=out)
    key = secrets.token_hex(32)
    name = probe_symbol(key)
    (campaign / "campaign.id").write_text(key + "\n")
    (campaign / "bridge-config.h").write_text(f'#define KCFG_ID "{key}"\n#define KCFG_COUNT {len(units)}\n'
                                               f'#define KCFG_LINK_NAME "{name}"\n#define KCFG_LINK_SYMBOL {name}\n')
    compiler_wrapper(linux, args.cc, campaign, args.plugin.resolve(), units)


def compiled_owners(cmd):
    if not cmd.is_file():
        return ()
    with cmd.open(errors="replace") as stream:
        match = re.search(r'''-DKBUILD_MODNAME='?"([A-Za-z0-9_:-]+)"''', stream.readline())
    return tuple(name.replace("-", "_") for name in match[1].split(":")) if match else ()


def object_owner(target):
    names = compiled_owners(target.with_name(f".{target.name}.cmd"))
    return names[0] if len(names) == 1 else None


def omitted_members(linux, obj, units, bindings):
    # An unlisted compiled source with the same Kconfig or Kbuild module owner
    # disqualifies the whole option, even if it has a different object basename.
    symbols = {u.symbol for u in units if u.policy != "keep"}
    if not symbols:
        return {}
    listed = defaultdict(set)
    by_owner = defaultdict(set)
    for u in units:
        source = Path(u.source)
        listed[u.symbol].add(source)
        if u.symbol in symbols and (owner := object_owner(obj / source.with_suffix(".o"))):
            by_owner[owner].add(u.symbol)
    missing = {}
    for directory, _, files in os.walk(obj):
        targets = {f for f in files if f.endswith(".o")}
        targets.update(f[1:-4] for f in files if f.startswith(".") and f.endswith(".o.cmd"))
        for name in sorted(targets):
            if name.endswith(".mod.o"):
                continue
            target = Path(directory, name).relative_to(obj)
            source = target.with_suffix(".c")
            cmd = Path(directory, f".{name}.cmd")
            owners = {s for owner in compiled_owners(cmd) for s in by_owner.get(owner, ())}
            owners.update(bindings(linux / source) & symbols)
            for symbol in owners:
                if source not in listed[symbol]:
                    missing.setdefault(symbol, target)
    return missing


def coverage(args):
    linux, campaign, obj = args.linux.resolve(), args.campaign, args.obj
    units = read_scope(campaign / "scope.tsv", linux)
    key = read_campaign_id(campaign)
    name = probe_symbol(key)
    directories = {}
    def bindings(source):
        if source.parent not in directories:
            directories[source.parent] = literal_bindings(source.parent)
        return directories[source.parent].get(source, set())
    missing = omitted_members(linux, obj, units, bindings)
    for opt, target in sorted(missing.items()):
        print(f"keeping {opt}: Kbuild also compiled unlisted member {target}", file=sys.stderr)
    with (campaign / "coverage.tsv").open("w") as counts, (campaign / "objects.tsv").open("w") as objects:
        print("id\tsource\tkbuild_module\tverified_sites", file=objects)
        for i, u in enumerate(units, 1):
            receipt = campaign / "receipts" / str(i)
            target = (obj / u.source).with_suffix(".o")
            owner = object_owner(target)
            direct = bindings(linux / u.source)
            count = 0
            if (u.symbol not in missing and owner and (u.module == "-" or u.module.replace("-", "_") == owner) and
                    (not direct or direct == {u.symbol}) and receipt.is_file() and target.is_file()):
                sites = int(receipt.read_text())
                if sites > 0:
                    undefined = subprocess.check_output([args.nm, "--undefined-only", target], text=True)
                    if any(line.split()[-1:] == [name] for line in undefined.splitlines()):
                        count = sites
            print(i, count, sep="\t", file=counts)
            print(i, u.source, owner or "-", count, sep="\t", file=objects)
    release = (obj / "include/config/kernel.release").read_text().strip()
    (campaign / "build.json").write_text(json.dumps({"arch": args.arch, "release": release}, indent=2) + "\n")


def candidate(args):
    campaign, plan = args.campaign, args.plan
    if read_campaign_id(campaign) != read_campaign_id(plan):
        raise ValueError("proposal is for a different capture campaign")
    if json.loads((campaign / "build.json").read_text())["arch"] != args.arch:
        raise ValueError("target architecture changed since capture")
    units = read_scope(campaign / "scope.tsv", args.linux)
    baseline = config_values(campaign / "baseline.config")
    allowed = {u.symbol: u.policy for u in units}
    sites = {int(i): int(n) for i, n in (row.split("\t") for row in (campaign / "coverage.tsv").read_text().splitlines())}
    for i, u in enumerate(units, 1):
        if sites.get(i, 0) <= 0 or u.symbol == "CONFIG_MODULES":
            allowed[u.symbol] = "keep"

    expected = baseline.copy()
    for row in (plan / "changes.tsv").read_text().splitlines():
        key, old, new = row.split("\t")
        if expected.get(key) != old or (allowed.get(key), old, new) not in {
                ("defer", "y", "m"), ("drop", "y", "n"), ("drop", "m", "n")}:
            raise ValueError(f"unauthorized configuration change: {row}")
        expected[key] = new
    actual = config_values(args.config)
    for key in sorted(expected.keys() | actual.keys()):
        want, got = expected.get(key, "n"), actual.get(key, "n")
        if want != got:
            raise ValueError(f"normalized configuration differs from reviewed changes: {key}: {want} -> {got}")
    eligible = {u.module for u in units if allowed[u.symbol] == "defer" and actual.get(u.symbol) == "m"}
    converted = {u.module for u in units if baseline.get(u.symbol) == "y" and actual.get(u.symbol) == "m"}
    late = (plan / "modules-load.late").read_text().splitlines()
    if len(late) != len(set(late)) or not converted <= set(late) <= eligible:
        raise ValueError("invalid deferred-module list")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--linux", required=True, type=Path)
    common.add_argument("--campaign", required=True, type=Path)
    start = sub.add_parser("prepare", parents=[common])
    start.add_argument("--config", required=True, type=Path)
    start.add_argument("--cc", required=True)
    start.add_argument("--plugin", required=True, type=Path)
    start.add_argument("--scope", type=Path)
    start.add_argument("--drop", action="append", default=[])
    start.add_argument("--defer", action="append", default=[])
    finish = sub.add_parser("coverage", parents=[common])
    finish.add_argument("--obj", required=True, type=Path)
    finish.add_argument("--nm", required=True)
    finish.add_argument("--arch", required=True)
    review = sub.add_parser("candidate", parents=[common])
    review.add_argument("--config", required=True, type=Path)
    review.add_argument("--plan", required=True, type=Path)
    review.add_argument("--arch", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "coverage": coverage, "candidate": candidate}[args.action](args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        sys.exit(f"capture: {exc}")
