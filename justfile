set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Board operations and the optional QEMU recipes.
default:
    @./kcfg --help
    @just --list

# Prepare the sources used by the selected toolchain.
sources:
    ./kcfg fetch

# Build the host plugin and processor (and LLVM if needed).
host:
    ./kcfg tools

# Run component and workflow tests without touching a target board.
check: host
    LD_LIBRARY_PATH="${SDK:-${DEPS:-$PWD/.deps}/llvm}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ctest --test-dir "${HOST_BUILD:-build/host}" --output-on-failure

# Run fresh baseline, profile, candidate and negative-control Ubuntu boots.
demo output="build/demo": host
    tools/demo.py --out "{{output}}" --llvm "${SDK:-${DEPS:-.deps}/llvm}" --linux "${LINUX:-${DEPS:-.deps}/linux}" --host-build "${HOST_BUILD:-build/host}" --jobs "${JOBS:-8}"
