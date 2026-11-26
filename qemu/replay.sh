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
    printf '%s\n' 'ARCH=x86_64' 'KERNEL_CONFIG="$WORK/fixture/.config"' \
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
check_hits() {
    local id prefix
    id=$(<"$output/profile/campaign/by-source/$1")
    printf -v prefix '%s\t%s\t%s\t' "$id" "$2" "$3"
    rg -q "^${prefix}[01]$" "$output/guests/profile/trace.tsv" || { printf 'Unexpected hits: %s\n' "$1" >&2; return 1; }
}
check_hits fs/binfmt_elf.c 1 1
check_hits kernel/configs.c 0 1
check_hits fs/nls/nls_cp850.c 0 0
"${cli[@]}" --plan --batch
rg -q '^CONFIG_BINFMT_ELF\ty\ty\t1\t1\t' "$output/plan/report.tsv"
rg -Fxq $'CONFIG_IKCONFIG\ty\tm' "$output/plan/changes.tsv"
rg -Fxq $'CONFIG_NLS_CODEPAGE_850\ty\tn' "$output/plan/changes.tsv"
rg -Fxq configs "$output/plan/modules-load.late"
"${cli[@]}" --build-kernel candidate

# Resolve the actual package's late modules in dependency order; omit only configs in the control.
stage="$output/fixture/modules"
release=$(<"$output/candidate/kernel/include/config/kernel.release")
mkdir -- "$stage"
tar -xf "$output/packages/kernel-candidate.tar.gz" -C "$stage" --strip-components=1 kernel-candidate/lib/modules
depmod -b "$stage" "$release"
late=() control=()
declare -A added=()
while IFS= read -r module; do
    paths=$(modprobe -C /dev/null -d "$stage" -S "$release" --show-depends -- "$module")
    while IFS=' ' read -r action file; do
        [[ $action != builtin ]] || continue
        [[ $action == insmod && -f $file ]] || { printf 'Unexpected module dependency: %s %s\n' "$action" "$file" >&2; exit 1; }
        if [[ ! -v added["$file"] ]]; then
            added["$file"]=1
            late+=(--late-module "$file")
            if [[ ${file##*/} != configs.ko ]]; then control+=(--late-module "$file"); fi
        fi
    done <<< "$paths"
done < "$output/plan/modules-load.late"
boot candidate candidate "${late[@]}"
status=0
boot candidate without-module "${control[@]}" || status=$?
[[ $status == 1 ]] && rg -Fxq 'ready=yes' "$output/guests/without-module/guest.status" && \
    rg -Fxq 'status=failed' "$output/guests/without-module/guest.status" && rg -Fxq 'stage=workload' "$output/guests/without-module/guest.status" || {
        printf 'Negative control must boot and then fail in the workload.\n' >&2; exit 1;
    }
printf 'Ubuntu replay passed, including the negative control: %s\n' "$output"
