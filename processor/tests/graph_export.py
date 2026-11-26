"""Compare the shipped exporter + processor with the target kernel's conf --olddefconfig.

Run: uv run --no-project python -I processor/tests/graph_export.py KCFG_PLAN PATH_TO_CONF
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EXPORTER = Path(__file__).resolve().parents[2] / "scripts/export_graph.py"
KCONFIG = '''mainmenu "Dependency fixture"
config MODULES
	bool "Module support"
	modules
config LEFT
	tristate "Left"
config RIGHT
	tristate "Right"
config USED
	tristate "Used"
	depends on LEFT || RIGHT
config CONDITION
	bool "Exercise conditional rules"
config CONDITIONAL_DEP
	bool "Conditional dependent"
	depends on LEFT if CONDITION
config BOOL_ON_M
	bool "Boolean on a module"
	depends on RIGHT
config SELECTOR
	tristate "Conditional selector"
	select SELECTED if CONDITION
	imply WEAK
config SELECTED
	bool "Selected Boolean"
config WEAK
	bool "Implied Boolean"
config HINT
	bool "Suggest a helper"
	imply HELPER
config SHOW_HELPER
	bool "Expose helper in the menu"
config HELPER
	tristate "Optional helper" if SHOW_HELPER
	depends on RIGHT
config SCALAR
	string "Scalar"
	default "board name"
config MATCH
	bool "Matches recorded scalar"
	depends on SCALAR = "board name"
config NUMBER
	int "Numeric bound"
	default 1
config MIXED
	bool "Mixed scalar/switch comparison"
	depends on NUMBER >= RIGHT
config SIGNED_LIMIT
	int
	default -1
config UNSIGNED_LIMIT
	hex
	default 0xffffffffffffffff
config LIMITS_DIFFER
	bool "Signed/hex comparison"
	depends on SIGNED_LIMIT != UNSIGNED_LIMIT
config HAS_SIZE
	bool "Has buffer sizing"
config BUFFER_SIZE
	int "Buffer size"
	default 42
	depends on HAS_SIZE
choice
	prompt "Transport"
config FIRST
	bool "First"
config SECOND
	bool "Second" if CONDITION
endchoice
'''
BASE = '''CONFIG_MODULES=y
CONFIG_LEFT=y
CONFIG_RIGHT=m
CONFIG_USED=y
CONFIG_CONDITION=y
CONFIG_CONDITIONAL_DEP=y
CONFIG_BOOL_ON_M=y
CONFIG_SELECTOR=m
CONFIG_SELECTED=y
# CONFIG_WEAK is not set
CONFIG_SHOW_HELPER=y
# CONFIG_HELPER is not set
CONFIG_MATCH=y
CONFIG_HAS_SIZE=y
CONFIG_FIRST=y
# CONFIG_SECOND is not set
'''
CASES = [
    ({}, True),
    ({"LEFT": "n", "RIGHT": "y", "CONDITIONAL_DEP": "n"}, True),
    ({"LEFT": "n", "RIGHT": "n", "CONDITIONAL_DEP": "n", "BOOL_ON_M": "n"}, False),
    ({"LEFT": "n", "RIGHT": "m", "CONDITIONAL_DEP": "n"}, False),  # USED=y exceeds the m upper bound.
    ({"LEFT": "n", "RIGHT": "y", "CONDITION": "n", "SELECTED": "n"}, True),
    ({"SELECTED": "n"}, False),  # An m selector with a Boolean target forces it to y.
    ({"FIRST": "n"}, False),
    ({"SECOND": "y"}, False),
    ({"FIRST": "n", "SECOND": "y"}, True),
    ({"FIRST": "n", "SECOND": "y", "CONDITION": "n"}, False),
    ({"RIGHT": "y", "MODULES": "n", "SELECTOR": "y"}, True),
    ({"MODULES": "n"}, False),
    ({"MIXED": "y"}, True),
    ({"RIGHT": "y", "MIXED": "y"}, False),
    ({"LIMITS_DIFFER": "y"}, False),
    ({"HAS_SIZE": "n"}, False),  # Native Kconfig also removes the recorded BUFFER_SIZE assignment.
    ({"HINT": "y", "SHOW_HELPER": "n", "HELPER": "n"}, False),
    ({"HINT": "y", "SHOW_HELPER": "n", "HELPER": "m"}, True),  # Hidden imply capped by RIGHT=m.
    ({"HINT": "y", "SHOW_HELPER": "y", "HELPER": "n"}, True),  # Visible target honors its own n.
    ({"HINT": "y", "SHOW_HELPER": "n", "RIGHT": "n", "BOOL_ON_M": "n"}, True),
]


def values(config):
    out = {}
    for row in config.splitlines():
        if active := re.fullmatch(r"(CONFIG_\w+)=(.*)", row):
            out[active[1]] = active[2]
        elif off := re.fullmatch(r"# (CONFIG_\w+) is not set", row):
            out[off[1]] = "n"
    return out


def main(program, native):
    with tempfile.TemporaryDirectory(prefix="kcfg-graph-test-") as tmp:
        root = Path(tmp)
        (root / "Kconfig").write_text(KCONFIG)

        def normalize(path):
            subprocess.run([native, "--olddefconfig", root / "Kconfig"], cwd=root, check=True, capture_output=True,
                           env={**os.environ, "KCONFIG_CONFIG": str(path)})
            return values(path.read_text())

        campaign = root / "capture"
        campaign.mkdir()
        ident = "aa" * 32
        (campaign / "campaign.id").write_text(ident + "\n")
        (campaign / "scope.tsv").write_text("1\tCONFIG_USED\tused\tdrop\tdrivers/used.c\n")
        (campaign / "coverage.tsv").write_text("1\t1\n")
        trace = root / "trace.tsv"
        trace.write_text(f"KCFG-TRACE\t1\t{ident}\n1\t0\t1\t0\nEND\t1\n")
        baseline = campaign / "baseline.config"
        baseline.write_text(BASE)
        original = normalize(baseline)
        graph = campaign / "dependencies.tsv"
        subprocess.run(["uv", "run", "--script", EXPORTER, "--linux", root, "--config", baseline,
                        "--arch", "x86_64", "--output", graph], check=True)
        for index, (changes, valid) in enumerate(CASES):
            opinions = {"CONFIG_" + key: value for key, value in changes.items()}
            force = root / "opinion"
            force.write_text("".join(f"{key}={val}\n" for key, val in opinions.items()))
            planned = root / f"plan-{index}"
            process = subprocess.run([program, campaign, trace, planned, "--force", force, "--batch"],
                                     check=False, capture_output=True, text=True)
            wanted = original | opinions
            request = root / "native.config"
            request.write_text("".join(f"{key}={val}\n" for key, val in wanted.items()))
            actual = normalize(request)
            stable = all(wanted.get(k, "n") == actual.get(k, "n") for k in wanted.keys() | actual.keys())
            if stable != valid or (process.returncode == 0) != stable:
                raise AssertionError(f"{changes}: native stable={stable}, planner={process.returncode}: {process.stderr}")
            if stable and values((planned / "requested.config").read_text()) != wanted:
                raise AssertionError(f"plan failed to honor {changes}")
            if not stable and (planned.exists() or "cannot apply" not in process.stderr):
                raise AssertionError(f"wrong conflict diagnostic for {changes}: {process.stderr}")
        print(f"{len(CASES)} graph decisions matched native olddefconfig")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
