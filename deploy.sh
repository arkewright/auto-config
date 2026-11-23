#!/usr/bin/env bash
# Run on the Ubuntu board: sudo ./deploy.sh
set -euo pipefail
cd -- "$(dirname -- "$0")"
release=$(<release)

install -m 0644 -- "boot/vmlinuz-$release" "boot/config-$release" /boot/
if [[ -d boot/dtbs/$release ]]; then
    mkdir -p /boot/dtbs
    cp -a --no-preserve=ownership -- "boot/dtbs/$release" /boot/dtbs/
fi
if [[ -d lib/modules/$release ]]; then
    mkdir -p /lib/modules
    cp -a --no-preserve=ownership -- "lib/modules/$release" /lib/modules/
    depmod -a "$release"
fi

mode=-c
if [[ -e /boot/initrd.img-$release ]]; then mode=-u; fi
update-initramfs "$mode" -k "$release"
update-grub
