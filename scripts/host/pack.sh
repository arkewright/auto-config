#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
umask 022

die() { printf '%s\n' "$*" >&2; exit 1; }
absolute_file() { [[ $1 == /* && $1 != */ && $1 != *$'\n'* && ${1##*/} != . && ${1##*/} != .. ]]; }

[[ $# == 1 ]] || die 'usage: pack.sh baseline|profile|candidate'
kind=$1
case "$kind" in
    baseline|profile|candidate) ;;
    *) die 'expected baseline, profile or candidate' ;;
esac
: "${ROOT:?}" "${WORK:?}" "${LINUX:?}" "${SDK:?}"
export PATH="$SDK/bin:$PATH" LD_LIBRARY_PATH="$SDK/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

absolute_file "${GRUB_KERNEL:-}" || die 'GRUB_KERNEL must be an absolute Linux file path'
absolute_file "${GRUB_INITRD:-}" || die 'GRUB_INITRD must be an absolute Linux file path'
GRUB_DTB=${GRUB_DTB:-}
DTB=${DTB:-}
if [[ -n $GRUB_DTB || -n $DTB ]]; then
    absolute_file "$GRUB_DTB" || die 'GRUB_DTB must be an absolute Linux file path when DTB is set'
    [[ -n $DTB && $DTB != *$'\n'* && /$DTB/ != *//* && /$DTB/ != */./* && /$DTB/ != */../* ]] ||
        die 'DTB must name a file relative to the installed device tree'
fi
if [[ -n ${EFI_KEY:-} || -n ${EFI_CERT:-} ]]; then
    [[ -n ${EFI_KEY:-} && -n ${EFI_CERT:-} ]] || die 'set both EFI_KEY and EFI_CERT for local EFI signing'
fi

obj="$WORK/$kind/kernel"
release=$(<"$obj/include/config/kernel.release")
[[ $release =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || die 'invalid kernel release'
image="$obj/arch/riscv/boot/${IMAGE_TARGET:-Image}"
cc=${KERNEL_CC:-$SDK/bin/clang}
kernel_cc=$cc
if [[ $kind == profile ]]; then kernel_cc="$WORK/profile/campaign/clang"; fi

packages="$WORK/packages"
mkdir -p -- "$packages"
tmp=$(mktemp -d -- "$packages/.pack-XXXXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
stage="$tmp/$kind"
mkdir -p -- "$stage"/{boot,dtbs}

# Installation targets operate on the completed build; never request a build target.
kbuild=(make -s --no-print-directory -C "$LINUX" "O=$obj" ARCH=riscv "LLVM=$SDK/bin/"
        KBUILD_ABS_SRCTREE=1 "KERNELRELEASE=$release")
moddir="$stage/lib/modules/$release"
modinst=("INSTALL_MOD_PATH=$stage" "MODLIB=$moddir" INSTALL_MOD_STRIP=1 DEPMOD=/bin/true modules_install)
"${kbuild[@]}" "CC=$kernel_cc" M= MO= "${modinst[@]}"
if [[ $kind == profile ]]; then
    "${kbuild[@]}" "CC=$cc" "M=$ROOT/driver" "MO=$WORK/profile/driver" "${modinst[@]}"
fi
for link in "$moddir/build" "$moddir/source"; do
    if [[ -L $link ]]; then rm -- "$link"; fi
done
"${kbuild[@]}" "CC=$kernel_cc" M= MO= "INSTALL_DTBS_PATH=$stage/dtbs" dtbs_install
[[ -z $DTB || -f $stage/dtbs/$DTB ]] || die "DTB was not installed from this build: $DTB"

if [[ -n ${EFI_KEY:-} ]]; then
    sbsign --key "$EFI_KEY" --cert "$EFI_CERT" --output "$stage/boot/Image" "$image"
    chmod 0644 -- "$stage/boot/Image"
else
    install -m 0644 -- "$image" "$stage/boot/Image"
fi
install -m 0644 -- "$obj/.config" "$stage/boot/config"
install -m 0755 -- "$ROOT/scripts/target/"{install,run} "$stage/"
if [[ $kind == candidate ]]; then
    install -m 0644 -- "$WORK/plan/modules-load.late" "$stage/modules-load.late"
else
    : > "$stage/modules-load.late"
fi
printf '%s\n' "$release" > "$stage/release"
printf '%s\n' "$kind" > "$stage/kind"
printf '%s\n' "$GRUB_KERNEL" > "$stage/GRUB_KERNEL"
printf '%s\n' "$GRUB_INITRD" > "$stage/GRUB_INITRD"
printf '%s\n' "$GRUB_DTB" > "$stage/GRUB_DTB"
printf '%s\n' "$DTB" > "$stage/DTB"

epoch=${SOURCE_DATE_EPOCH:-$(stat -c %Y -- "$LINUX/Makefile")}
tar --sort=name --mtime="@$epoch" --owner=0 --group=0 --numeric-owner -czf "$tmp/archive" -C "$tmp" -- "$kind"
mv -fT -- "$tmp/archive" "$packages/$kind.tar.gz"
printf '%s\n' "$packages/$kind.tar.gz"
