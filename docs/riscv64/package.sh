#!/usr/bin/env bash
# Package an existing board build. Run on x86_64; never install into the host.
set -euo pipefail
export LC_ALL=C
umask 022
fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# == 3 || $# == 4 ]] || fail 'usage: package.sh RUN_DIR SDK baseline|profile|candidate [final-boot-image]'
work=$(realpath -e "$1")
sdk=$(realpath -e "$2")
kind=$3
run=${work##*/}
[[ $run =~ ^[a-z0-9][a-z0-9-]{0,15}$ ]] || fail 'use a short lowercase run directory name, e.g. rvboard01'
repo=$(realpath "$(dirname "$0")/../..")
linux=$(realpath -e "${LINUX:-$repo/.deps/linux}")
export PATH="$sdk/bin:$PATH" LD_LIBRARY_PATH="$sdk/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cc="$sdk/bin/clang"
case $kind in
    baseline)  obj="$work/plain"; suffix="-$run-base" ;;
    candidate) obj="$work/candidate/kernel"; suffix="-$run-cand" ;;
    profile)
        obj="$work/profile/kernel"; cc=$(realpath -e "$work/profile/campaign/clang")
        id=$(<"$work/profile/campaign/campaign.id"); suffix="-kcfg-${id:0:16}" ;;
    *) fail 'expected baseline, profile or candidate' ;;
esac
obj=$(realpath -e "$obj")
rel=$(<"$obj/include/config/kernel.release")
[[ $rel == *"$suffix"* && $rel =~ ^[A-Za-z0-9._+-]+$ ]] || fail "unexpected kernel release: $rel"
image=$(realpath -e "${4:-$obj/arch/riscv/boot/Image}")
grub-file --is-riscv64-efi "$image" || fail 'expected an EFI-loadable riscv64 kernel image'
get_config() { "$linux/scripts/config" --file "$obj/.config" --state "$1"; }
if [[ $(get_config MODULE_SIG_FORCE) == y && $(get_config MODULE_SIG_ALL) != y ]]; then
    fail 'this recipe requires install-time signing for a signature-enforcing kernel'
fi
mkdir -p "$work/packages"
dest="$work/packages/$kind"
[[ ! -e $dest && ! -L $dest ]] || fail "package destination already exists: $dest"
tmp=$(mktemp -d "$work/packages/.stage-XXXXXXXX")
trap 'rm -rf -- "$tmp"' EXIT
stage="$tmp/root"; slot="$stage/boot/board-trial/$rel"; mods="$stage/lib/modules/$rel"
install -d "$stage/DEBIAN" "$slot" "$tmp/ship"
opts=(-s --no-print-directory -C "$linux" "O=$obj" ARCH=riscv "LLVM=$sdk/bin/" KBUILD_ABS_SRCTREE=1 "LOCALVERSION=$suffix")
[[ $(make "${opts[@]}" "CC=$cc" kernelrelease) == "$rel" ]] || fail 'Kbuild release changed'
inst=("INSTALL_MOD_PATH=$stage" "MODLIB=$mods" INSTALL_MOD_STRIP=1 DEPMOD=/bin/true modules_install)
make "${opts[@]}" "CC=$cc" "${inst[@]}"
if [[ $kind == profile ]]; then
    make "${opts[@]}" "CC=$sdk/bin/clang" "M=$repo/driver" "MO=$work/profile/driver" "${inst[@]}"
fi
for link in "$mods/build" "$mods/source"; do if [[ -L $link ]]; then unlink "$link"; fi; done
depmod -b "$stage" "$rel"
[[ -f $mods/modules.dep && -f $mods/modules.builtin ]] || fail 'incomplete module tree'
if [[ $kind == profile ]]; then
    info=(modinfo -b "$stage" -k "$rel")
    vm=$("${info[@]}" -F vermagic kcfg_collect)
    [[ ${vm%% *} == "$rel" ]] || fail 'collector release mismatch'
    if [[ $(get_config MODULE_SIG_ALL) == y ]]; then
        [[ -n $("${info[@]}" -F signer kcfg_collect) ]] || fail 'installed collector has no signer'
    fi
fi
install -m 0644 "$image" "$slot/linux"
install -m 0644 "$obj/.config" "$stage/boot/config-$rel"
install -m 0644 "$obj/System.map" "$stage/boot/System.map-$rel"
if [[ -n ${BOARD_BOOT_FILES:-} ]]; then
    install -d "$slot/board"; cp -a -- "$BOARD_BOOT_FILES/." "$slot/board/"
fi
if [[ $kind == candidate ]]; then
    install -m 0644 "$work/plan/modules-load.late" "$slot/modules-load.late"
else
    install -m 0644 /dev/null "$slot/modules-load.late"
fi
printf 'Package: board-kernel-%s-%s\nVersion: 1\nArchitecture: riscv64\nKernel-Release: %s\nMaintainer: Board Lab <root@localhost>\nDescription: Board validation kernel and runtime modules\n' "$run" "$kind" "$rel" > "$stage/DEBIAN/control"
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(stat -c %Y "$linux/Makefile")}
dpkg-deb --root-owner-group --threads-max=1 -Zxz -z6 --build "$stage" "$tmp/ship/kernel.deb"
[[ $(dpkg-deb -f "$tmp/ship/kernel.deb" Architecture) == riscv64 ]] || fail 'wrong package architecture'
printf '%s\n' "$rel" > "$tmp/ship/release"
mkdir "$dest"
mv -- "$tmp/ship/kernel.deb" "$tmp/ship/release" "$dest/"
printf '%s: %s (%s bytes)\n' "$kind" "$dest/kernel.deb" "$(stat -c %s "$dest/kernel.deb")"
