#!/usr/bin/env bash
# Maintainer regression: build via the public CLI and replay in four fresh Ubuntu guests.
set -euo pipefail
[[ $# == 1 || $# == 2 ]] || { printf 'usage: %s NEW_OUTPUT_DIR [HOST_ENV]\n' "$0" >&2; exit 2; }
output=$(realpath -m -- "$1")
settings=$(realpath -- "${2:-.env}")
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
cd -- "$ROOT"
source "$settings"
for key in LINUX_SRC LLVM_PREFIX HOST_BUILD SYSROOT; do
    if [[ -n ${!key} ]]; then printf -v "$key" '%s' "$(realpath -m -- "${!key}")"; fi
done
for key in KERNEL_CC HOSTCC HOSTCXX; do
    if [[ ${!key} == */* && ${!key} != /* ]]; then printf -v "$key" '%s' "$ROOT/${!key}"; fi
done
export LC_ALL=C PATH="$LLVM_PREFIX/bin:$PATH" LD_LIBRARY_PATH="$LLVM_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

mkdir -p -- "$(dirname -- "$output")"
mkdir -- "$output"
mkdir -- "$output/fixture"
sort -u qemu/guest.config examples/profile.config > "$output/fixture/qemu.config"
host_flags=("HOSTCC=$HOSTCC" "HOSTCXX=$HOSTCXX")
if [[ -n $SYSROOT ]]; then
    for flag in HOSTCFLAGS HOSTCXXFLAGS HOSTLDFLAGS; do host_flags+=("$flag=--sysroot=$SYSROOT"); done
fi
make -s -C "$LINUX_SRC" "O=$output/fixture" ARCH=x86_64 "LLVM=$LLVM_PREFIX/bin/" "CC=$KERNEL_CC" \
    "${host_flags[@]}" KCONFIG_CONFIG=.config "KCONFIG_ALLCONFIG=$output/fixture/qemu.config" allnoconfig

# Reuse host tools, but isolate the fixture from the board's architecture, policy and package settings.
{
    printf 'source %q\n' "$ROOT/.env.example"
    for key in LINUX_SRC LLVM_PREFIX HOST_BUILD KERNEL_CC HOSTCC HOSTCXX SYSROOT PYTHON JOBS; do
        printf '%s=%q\n' "$key" "${!key}"
    done
    printf 'WORK=%q\n' "$output"
    printf '%s\n' 'ARCH=x86_64' 'KERNEL_CONFIG="$WORK/fixture/.config"' 'SCOPE="$ROOT/examples/qemu.scope.tsv"' \
        'TRACE="$WORK/guests/profile/trace.tsv"' 'PACKAGE_DIR="$WORK/packages"'
} > "$output/replay.env"
cli=("$ROOT/kcfg" --env "$output/replay.env")

boot() {
    local role=$1 name=$2 status=0
    shift 2
    local obj="$output/$role/kernel" guest="$output/guests/$name"
    local args=(--accel "${QEMU_ACCEL:-auto}" --busybox "${QEMU_BUSYBOX:-busybox}")
    if [[ -n ${QEMU_ROOTFS:-} ]]; then args+=(--rootfs "$QEMU_ROOTFS"); fi
    "$PYTHON" -I qemu/guest.py run --mode "$role" --kernel "$obj/arch/x86/boot/bzImage" \
        --workload examples/workload.sh --output "$guest" "${args[@]}" "$@" || status=$?
    rg -Fxq -- "kernel=$(<"$obj/include/config/kernel.release")" "$guest/guest.status" || return 2
    return "$status"
}

"${cli[@]}" --build-kernel baseline
boot baseline baseline
"${cli[@]}" --build-kernel profile
boot profile profile --module "$output/profile/driver/kcfg_collect.ko"
# ELF executes in both windows; the config reader only in the workload; CP850 in neither.
diff -u <(printf '1\t1\t1\n2\t0\t1\n3\t0\t0\n') \
    <(rg '^\d+\t' "$output/guests/profile/trace.tsv" | cut -f1-3 | sort -n)
"${cli[@]}" --plan
diff -u <(printf 'CONFIG_IKCONFIG\ty\tm\nCONFIG_NLS_CODEPAGE_850\ty\tn\n') <(sort "$output/plan/changes.tsv")
diff -u <(printf 'configs\n') "$output/plan/modules-load.late"
"${cli[@]}" --build-kernel candidate
boot candidate candidate --late-module "$output/candidate/kernel/kernel/configs.ko"
status=0
boot candidate without-module || status=$?
[[ $status == 1 ]] && rg -Fxq 'status=failed' "$output/guests/without-module/guest.status" && \
    rg -Fxq 'stage=workload' "$output/guests/without-module/guest.status" || {
        printf 'Negative control must boot and then fail in the workload.\n' >&2; exit 1;
    }
printf 'Ubuntu replay passed, including the negative control: %s\n' "$output"
