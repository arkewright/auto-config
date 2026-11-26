# /// script
# requires-python = ">=3.11"
# dependencies = ["kconfig-mcp==0.1.0"]
# ///
"""Export the configured architecture's Kconfig constraints for the host editor."""

import argparse
import operator
import os
import re
import sys
import tempfile
from contextlib import chdir
from functools import reduce
from pathlib import Path

import kconfiglib as kc
from kconfig_mcp.loader import LoadError, load

SWITCHES = {kc.BOOL, kc.TRISTATE}
TRI = ("n", "m", "y")
COMPARE = {kc.EQUAL: operator.eq, kc.UNEQUAL: operator.ne, kc.LESS: operator.lt, kc.LESS_EQUAL: operator.le,
           kc.GREATER: operator.gt, kc.GREATER_EQUAL: operator.ge}


def spelling(item):
    # Captured values include hidden compiler-version scalars whose defaults can change between builds.
    if item.orig_type not in SWITCHES and item.user_value is not None:
        return item.user_value
    return item.str_value or {kc.INT: "0", kc.HEX: "0x0"}.get(item.orig_type, "")


def as_number(item, text):
    if item.orig_type in SWITCHES:
        return item.tri_value
    digits = text.lstrip(" \t\r\n\f\v")
    negative = digits.startswith("-")
    if digits.startswith(("+", "-")):
        digits = digits[1:]
    if item.orig_type == kc.INT:
        base = 10
    elif item.orig_type == kc.HEX or digits.lower().startswith("0x"):
        base = 16
        if digits.lower().startswith("0x"):
            digits = digits[2:]
    else:
        base = 8 if digits.startswith("0") else 10
    if not re.fullmatch({8: "[0-7]+", 10: "[0-9]+", 16: "[0-9a-fA-F]+"}[base], digits):
        return None
    digits = digits.lstrip("0") or "0"
    if len(digits) > 22:
        return None  # More significant digits than a 64-bit octal, decimal or hexadecimal value.
    value = int(digits, base) * (-1 if negative else 1)
    if item.orig_type == kc.HEX:
        return value % (1 << 64) if abs(value) < 1 << 64 else None
    return value if -(1 << 63) <= value < 1 << 63 else None


def compare(op, left, right):
    # Native Kconfig uses strtoll/strtoull, including octal and signed-to-unsigned
    # promotion for hex comparisons. Python's unbounded signed int differs here.
    a, b = spelling(left), spelling(right)
    if left.orig_type != kc.STRING or right.orig_type != kc.STRING:
        x, y = as_number(left, a), as_number(right, b)
        if x is not None and y is not None:
            if kc.HEX in (left.orig_type, right.orig_type):
                x, y = x % (1 << 64), y % (1 << 64)
            return COMPARE[op](x, y)
    return COMPARE[op](a, b)


def name(symbol):
    return "CONFIG_" + symbol.name


def join(op, left, right):
    zero, one = ("n", "y") if op == "&&" else ("y", "n")
    if zero in (left, right):
        return zero
    if left == one or left == right:
        return right
    if right == one:
        return left
    return f"{left} {right} {op}"


def either(expressions):
    return reduce(lambda a, b: join("||", a, b), expressions, "n")


class Expressions:
    def __init__(self, tree):
        self.tree = tree

    def prompts(self, item, choice=None):
        return either(self.expression(node.prompt[1], choice) for node in item.nodes if node.prompt)

    def variable(self, item):
        return isinstance(item, kc.Choice) or (item.orig_type in SWITCHES and not item.is_constant)

    def comparison(self, expr, choice):
        op, left, right = expr
        lw, rw = self.variable(left), self.variable(right)
        if (lw and (rw or right.orig_type in SWITCHES)) or (rw and left.orig_type in SWITCHES):
            return f"{self.expression(left, choice)} {self.expression(right, choice)} {kc.REL_TO_STR[op]}"
        if not lw and not rw:
            return "y" if compare(op, left, right) else "n"
        # A mixed comparison can be numeric or lexical. Project the parser's
        # comparison onto the three possible values of its one editable input.
        variable = self.expression(left if lw else right, choice)
        allowed = []
        for v in (self.tree.n, self.tree.m, self.tree.y):
            args = (v, right) if lw else (left, v)
            if compare(op, *args):
                allowed.append(f"{variable} {v.name} =")
        return "y" if len(allowed) == 3 else either(allowed)

    def expression(self, expr, choice=None):
        if isinstance(expr, kc.Choice):
            if expr is choice:
                return "y"  # Membership and group visibility are exported as their own rule.
            enabled = either(name(member) for member in expr.syms)
            if expr.is_optional:
                return enabled
            visible = self.prompts(expr)
            nonzero = f"{visible} n !="
            if expr.orig_type == kc.BOOL or not self.tree.modules.nodes:
                return nonzero
            minimum = join("||", join("&&", "m", visible), join("&&", nonzero, f"{name(self.tree.modules)} n ="))
            return join("||", enabled, minimum)
        if not isinstance(expr, tuple):
            if self.variable(expr):
                return name(expr)
            return TRI[expr.tri_value]  # Scalars and undefined names in logical context have value n.
        op = expr[0]
        if op in (kc.AND, kc.OR):
            return join("&&" if op == kc.AND else "||", self.expression(expr[1], choice), self.expression(expr[2], choice))
        if op == kc.NOT:
            arg = self.expression(expr[1], choice)
            return TRI[2 - TRI.index(arg)] if arg in TRI else f"{arg} !"
        if op in kc.REL_TO_STR:
            return self.comparison(expr, choice)
        raise ValueError(f"unsupported Kconfig expression: {expr}")


def records(tree):
    for warning in tree.warnings:
        if "'modules' option is not supported" in warning:
            raise ValueError(warning)
    yield ("KCFG-GRAPH", "1")
    expr = Expressions(tree)
    switches = [s for s in tree.unique_defined_syms if s.orig_type in SWITCHES]
    for s in switches:
        declaration = ("symbol", name(s), kc.TYPE_TO_STR[s.orig_type])
        yield declaration if s.visibility else (*declaration, "hidden")
    if tree.modules.nodes:
        yield ("modules", name(tree.modules))
    absent = set()
    for s in switches:
        dep = expr.expression(s.direct_dep, s.choice)
        if s.choice:
            dep = join("&&", dep, expr.prompts(s, s.choice))
        if dep != "y":
            yield ("depends", name(s), dep)
        for kind, properties in (("select", s.selects), ("imply", s.implies)):
            for target, condition in properties:
                if target.orig_type == kc.UNKNOWN:
                    if target not in absent:
                        yield ("absent", name(target))  # A shared default can have no type on this architecture.
                        absent.add(target)
                elif target.orig_type not in SWITCHES:
                    raise ValueError(f"{s.name}: {kind} target {target.name} is not a bool or tristate")
                if target.choice:
                    raise ValueError(f"unsupported {kind} of choice member {target.name} by {s.name}")
                row = (kind, name(s), name(target), expr.expression(condition))
                if kind == "imply":
                    visible = expr.prompts(target)
                    if visible != "y":
                        limit = expr.expression(target.direct_dep) if target.orig_type in SWITCHES else "n"
                        row += (visible, limit)
                yield row
    for s in tree.unique_defined_syms:
        if s.orig_type in SWITCHES or s.orig_type == kc.UNKNOWN:
            continue
        if s.orig_type not in (kc.INT, kc.HEX, kc.STRING) or s.selects or s.implies:
            raise ValueError(f"unsupported scalar declaration: {s.name_and_loc}")
        if s.user_value is not None:
            dep = expr.expression(s.direct_dep)
            if dep != "y":
                yield ("scalar", name(s), dep)
    for group in tree.unique_choices:
        if group.orig_type not in SWITCHES or not group.syms:
            raise ValueError(f"unsupported choice: {group.name_and_loc}")
        visible = join("&&", expr.prompts(group), either(expr.prompts(s, group) for s in group.syms))
        yield ("choice", kc.TYPE_TO_STR[group.orig_type], "optional" if group.is_optional else "required",
               visible, " ".join(name(s) for s in group.syms))


def environment(arch, command_file):
    aliases = {"i386": "x86", "x86_64": "x86", "sparc32": "sparc", "sparc64": "sparc", "parisc64": "parisc"}
    env = {"ARCH": arch, "SRCARCH": os.getenv("SRCARCH", aliases.get(arch, arch)),
           **{key: os.getenv(key, default) for key, default in {
               "CC": "cc", "LD": "ld", "AR": "ar", "NM": "nm", "OBJCOPY": "objcopy", "PYTHON3": sys.executable,
               "RUSTC": "rustc", "BINDGEN": "bindgen",
           }.items()}}
    if command_file:
        for line in command_file.read_text().splitlines():
            match = re.fullmatch(r'ifneq "\$\((\w+)\)" "(.*)"', line) or re.fullmatch(r"ifneq \(\$\((\w+)\),(.*)\)", line)
            if match:
                env[match[1]] = match[2]
        if env["ARCH"] != arch:
            raise ValueError(f"Kbuild environment is for {env['ARCH']}, not {arch}")
    env["CONFIG_"] = "CONFIG_"
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--kbuild-env", type=Path, help="profile kernel's include/config/auto.conf.cmd (compiler probe environment)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, config = args.linux.resolve() / "Kconfig", args.config.resolve()
    env = environment(args.arch, args.kbuild_env)
    with tempfile.TemporaryDirectory(prefix="kcfg-probes-") as work, chdir(work):
        tree = load(str(root), str(config), env, shell="run").kconf
        contents = "".join("\t".join(row) + "\n" for row in records(tree))
    # Concurrent planners sharing a campaign must see one complete export.
    with tempfile.TemporaryDirectory(dir=args.output.parent) as stage:
        staged = Path(stage) / "dependencies.tsv"
        staged.write_text(contents, encoding="utf-8")
        staged.replace(args.output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, LoadError, kc.KconfigError) as exc:
        sys.exit(f"dependency export: {exc}")
