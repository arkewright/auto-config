# Called after the selected Kbuild targets have finished.
package_kernel() (
    local release name tmp stage moddir trees target
    local -a modinst
    release=$(<"$obj/include/config/kernel.release")
    name=$PACKAGE_NAME-$role
    tmp=$(mktemp -d -- "$WORK/$role/.bundle-XXXXXXXX")
    trap 'rm -rf -- "$tmp"' EXIT
    stage=$tmp/tree
    moddir=$stage/lib/modules/$release
    trees=$stage/boot/dtbs/$release

    mkdir -p -- "$stage/boot" "$PACKAGE_DIR"
    install -m 0644 -- "$obj/$KERNEL_IMAGE" "$stage/boot/vmlinuz-$release"
    install -m 0644 -- "$obj/.config" "$stage/boot/config-$release"
    install -m 0755 -- "$DEPLOY_SCRIPT" "$stage/deploy.sh"
    printf '%s\n' "$release" > "$stage/release"
    for target in "${KERNEL_TARGETS[@]}"; do
        case $target in
            modules)
                modinst=("INSTALL_MOD_PATH=$stage" "MODLIB=$moddir" "INSTALL_MOD_STRIP=$MODULE_STRIP"
                         "KERNELRELEASE=$release" DEPMOD=/bin/true modules_install)
                kmake M= MO= "${modinst[@]}"
                if [[ $role == profile ]]; then
                    kmake "CC=$KERNEL_CC" "M=$ROOT/driver" "MO=$WORK/profile/driver" "${modinst[@]}"
                fi
                rm -f -- "$moddir/build" "$moddir/source" ;;
            dtbs)
                kmake M= MO= "KERNELRELEASE=$release" "INSTALL_DTBS_PATH=$trees" dtbs_install ;;
            *.dtb|*.dtbo)
                install -D -m 0644 -- "$obj/$boot_tree/dts/$target" "$trees/$target" ;;
        esac
    done
    if [[ $role == candidate ]]; then
        install -m 0644 -- "$WORK/plan/modules-load.late" "$stage/modules-load.late"
    fi

    mv -T -- "$stage" "$tmp/$name"
    tar --sort=name --owner=0 --group=0 --numeric-owner -C "$tmp" -cf - -- "$name" |
        gzip -n "-$GZIP_LEVEL" > "$tmp/archive.tar.gz"
    mv -fT -- "$tmp/archive.tar.gz" "$PACKAGE_DIR/$name.tar.gz"
    printf '%s\n' "$PACKAGE_DIR/$name.tar.gz"
)
