#!/usr/bin/env bash
set -euo pipefail

fetch=()
if [[ ! -x $SDK/bin/llvm-config || ! -x $SDK/bin/clang || ! -x $SDK/bin/ld.lld ]]; then
    fetch+=(--llvm-source)
fi
"$PYTHON" -I "$ROOT/tools/sources.py" --cache "$DEPS" "${fetch[@]}"
[[ $1 == tools ]] || exit 0

sysroot=()
if [[ -n ${SYSROOT:-} ]]; then sysroot+=("-DCMAKE_SYSROOT=$SYSROOT"); fi
if (( ${#fetch[@]} )); then
    build=${LLVM_BUILD:-$DEPS/llvm-build}
    cmake -S "${LLVM_SOURCE:-$DEPS/llvm-project}/llvm" -B "$build" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$SDK" \
        -DCMAKE_C_COMPILER="${BOOTSTRAP_CC:-cc}" -DCMAKE_CXX_COMPILER="${BOOTSTRAP_CXX:-c++}" \
        '-DLLVM_ENABLE_PROJECTS=clang;lld' '-DLLVM_TARGETS_TO_BUILD=X86;RISCV;BPF' \
        -DLLVM_BUILD_LLVM_DYLIB=ON -DLLVM_LINK_LLVM_DYLIB=ON -DCLANG_LINK_CLANG_DYLIB=ON \
        -DLLVM_PARALLEL_LINK_JOBS=1 "${sysroot[@]}"
    cmake --build "$build" --parallel "$JOBS" --target install
fi

ftxui=${FTXUI_SOURCE_DIR:-}
if [[ -z $ftxui && -z ${FTXUI_DIR:-} ]]; then ftxui=$DEPS/ftxui; fi
llvm_dir=$("$SDK/bin/llvm-config" --cmakedir)
cmake -S "$ROOT" -B "$HOST_BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="${CC:-$SDK/bin/clang}" -DCMAKE_CXX_COMPILER="${CXX:-$SDK/bin/clang++}" \
    -DLLVM_DIR="$llvm_dir" -DClang_DIR="${llvm_dir%/*}/clang" \
    -DFTXUI_SOURCE_DIR="$ftxui" -Dftxui_DIR="${FTXUI_DIR:-}" "${sysroot[@]}"
cmake --build "$HOST_BUILD" --parallel "$JOBS"
