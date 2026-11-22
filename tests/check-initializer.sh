#!/usr/bin/env bash
# Exercise the real initcall boundary independently of workload collection.
set -euo pipefail
if (( $# != 2 )); then
    printf 'usage: %s PROFILE_DIR NEW_RESULT_DIR\n' "$0" >&2
    exit 2
fi
root=$(realpath "$(dirname "$0")/..")
profile=$(realpath "$1")
result=$(realpath -m "$2")
sdk=$(realpath "${SDK:-$root/.deps/llvm}")
linux=$(realpath "${LINUX:-$root/.deps/linux}")
export PATH="$sdk/bin:$PATH" LD_LIBRARY_PATH="$sdk/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cmp "$profile/campaign/scope.tsv" "$root/examples/qemu.scope.tsv"
read -r id < "$profile/campaign/campaign.id"
mkdir -- "$result"
make -s -C "$linux" O="$profile/kernel" LLVM="$sdk/bin/" LOCALVERSION="-kcfg-${id:0:16}" \
    M="$root/tests/initializer" MO="$result/module" KCFG_HEADER="$profile/campaign/bridge-config.h" modules
"$root/qemu/guest.py" run --mode profile --kernel "$profile/kernel/arch/x86/boot/bzImage" \
    --module "$profile/driver/kcfg_collect.ko" --late-module "$result/module/kcfg_initializer_test.ko" \
    --workload "$root/tests/window.sh" --output "$result/guest"
rg --quiet '^3\t0\t0\t1$' "$result/guest/trace.tsv"
printf 'Initializer helper was observed only in initialization context.\n'
