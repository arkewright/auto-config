#!/usr/bin/env bash
set -euo pipefail

kernel=("$PYTHON" -I "$ROOT/tools/kernel.py")
common=(--linux "$LINUX" --llvm "$SDK" --arch riscv --jobs "$JOBS")
if [[ -n ${KERNEL_CC:-} ]]; then common+=(--cc "$KERNEL_CC"); fi
if [[ -n ${HOSTCC:-} ]]; then common+=(--hostcc "$HOSTCC"); fi
if [[ -n ${HOSTCXX:-} ]]; then common+=(--hostcxx "$HOSTCXX"); fi
if [[ -n ${SYSROOT:-} ]]; then common+=(--host-sysroot "$SYSROOT"); fi
if [[ $1 == config ]]; then
    if [[ -n ${BOARD_CONFIG:-} ]]; then common+=(--fragment "$BOARD_CONFIG"); fi
    exec "${kernel[@]}" board-config "${common[@]}" --out "$WORK/config"
fi

common+=(--image-target "$IMAGE_TARGET")
baseline="$WORK/config/.config"
label=${WORK##*/}
export LOCALVERSION="-kcfg-${label//[^a-zA-Z0-9_-]/-}-$2"
case $2 in
    baseline)
        exec "${kernel[@]}" plain "${common[@]}" --baseline "$baseline" --out "$WORK/baseline/kernel" ;;
    profile)
        scope=${SCOPE:-}
        if [[ -z $scope ]]; then
            dir=$(mktemp -d "$WORK/.scope-XXXXXXXX"); trap 'rm -rf -- "$dir"' EXIT
            scope=$dir/scope.tsv
            read -r -a drop <<< "${DROP:-}"
            read -r -a defer <<< "${DEFER:-}"
            policy=()
            for key in "${drop[@]}"; do policy+=(--drop "$key"); done
            for key in "${defer[@]}"; do policy+=(--defer "$key"); done
            if (( ! ${#policy[@]} )); then printf 'No DROP or DEFER policy: observe direct bindings and keep all options.\n'; fi
            "${kernel[@]}" inventory --linux "$LINUX" --under . "${policy[@]}" --out "$scope"
        fi
        "${kernel[@]}" profile "${common[@]}" --baseline "$baseline" --scope "$scope" \
            --plugin "$HOST_BUILD/plugin/kcfg_frontend.so" --out "$WORK/profile" ;;
    candidate)
        exec "${kernel[@]}" candidate "${common[@]}" --campaign "$WORK/profile/campaign" \
            --plan "$WORK/plan" --out "$WORK/candidate" ;;
esac
