set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List available project operations.
default:
    @just --list

# Reuse or fetch the pinned Linux and FTXUI sources and Ubuntu userspace.
sources:
    tools/sources.py
    qemu/guest.py fetch

# Use an ABI-matched SDK. Set SDK, CC, CXX, SYSROOT or HOST_BUILD before invoking.
host:
    #!/usr/bin/env bash
    set -euo pipefail
    sdk=$(realpath "${SDK:-.deps/llvm}")
    export PATH="$sdk/bin:$PATH" LD_LIBRARY_PATH="$sdk/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    opts=()
    if [[ -n ${SYSROOT:-} ]]; then opts+=(-DCMAKE_SYSROOT="$SYSROOT"); fi
    cmake -S . -B "${HOST_BUILD:-build/host}" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER="${CC:-$sdk/bin/clang}" -DCMAKE_CXX_COMPILER="${CXX:-$sdk/bin/clang++}" \
        -DLLVM_DIR="$sdk/lib/cmake/llvm" -DClang_DIR="$sdk/lib/cmake/clang" \
        -DFTXUI_SOURCE_DIR="${FTXUI_SOURCE_DIR:-$PWD/.deps/ftxui}" "${opts[@]}"
    cmake --build "${HOST_BUILD:-build/host}" --parallel "${JOBS:-8}"

# Run component and host-boundary tests, without booting a VM.
check: host
    LD_LIBRARY_PATH="${SDK:-$PWD/.deps/llvm}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ctest --test-dir "${HOST_BUILD:-build/host}" --output-on-failure

# Run fresh baseline, profile, candidate and negative-control Ubuntu boots.
demo output="build/demo": host
    tools/demo.py --out "{{output}}" --llvm "${SDK:-.deps/llvm}" --linux "${LINUX:-.deps/linux}" --host-build "${HOST_BUILD:-build/host}" --jobs "${JOBS:-8}"
