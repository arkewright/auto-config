#!/usr/bin/env bash
# Exercise the real initcall boundary independently of workload collection.
set -euo pipefail
if (( $# != 2 )); then
    printf 'usage: %s QEMU_REPLAY_DIR NEW_RESULT_DIR\n' "$0" >&2
    exit 2
fi
ROOT=$(realpath "$(dirname "$0")/..")
replay=$(realpath -- "$1")
result=$(realpath -m -- "$2")
cd -- "$ROOT"
source "$replay/replay.env"
export PATH="$LLVM_PREFIX/bin:$PATH" LD_LIBRARY_PATH="$LLVM_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
profile="$replay/profile"
release=$(<"$profile/kernel/include/config/kernel.release")
# Borrow an uncompiled codepage's slot so only the test module can set its bits.
id=$(<"$profile/campaign/by-source/fs/nls/nls_cp852.c")
[[ ! -e $profile/kernel/fs/nls/nls_cp852.o ]]
mkdir -- "$result"
host_flags=("HOSTCC=$HOSTCC" "HOSTCXX=$HOSTCXX")
if [[ -n $SYSROOT ]]; then
    for flag in HOSTCFLAGS HOSTCXXFLAGS HOSTLDFLAGS; do host_flags+=("$flag=--sysroot=$SYSROOT"); done
fi
make -s -C "$LINUX_SRC" "O=$profile/kernel" "ARCH=$ARCH" "LLVM=$LLVM_PREFIX/bin/" "CC=$KERNEL_CC" \
    "${host_flags[@]}" "KERNELRELEASE=$release" "M=$ROOT/tests/initializer" \
    "MO=$result/module" "KCFG_HEADER=$profile/campaign/bridge-config.h" "KCFG_TEST_ID=$id" modules
args=(--accel "${QEMU_ACCEL:-auto}" --busybox "${QEMU_BUSYBOX:-busybox}")
if [[ -n ${QEMU_ROOTFS:-} ]]; then args+=(--rootfs "$QEMU_ROOTFS"); fi
"$PYTHON" -I qemu/guest.py run --mode profile --kernel "$profile/kernel/arch/x86/boot/bzImage" \
    --module "$profile/driver/kcfg_collect.ko" --late-module "$result/module/kcfg_initializer_test.ko" \
    --workload tests/window.sh --output "$result/guest" "${args[@]}"
rg -Fxq "$id"$'\t0\t0\t1' "$result/guest/trace.tsv"
printf 'Initializer helper was observed only in initialization context.\n'
