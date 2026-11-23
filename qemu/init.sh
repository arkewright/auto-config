#!/bin/sh
# PID 1 in the throwaway Ubuntu guest. No host directory is shared with it.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export KERNEL_TEST_MODULES=/kernel-test/modules KERNEL_TEST_INPUT=/kernel-test/input
control=/kernel-test/busybox
status_ready=0
stage=mount

finish() {
    result=$?
    trap - EXIT
    if [ "$status_ready" = 1 ]; then
        if [ "$result" = 0 ]; then
            printf 'status=ok\n' >&3
        else
            printf 'status=failed\nstage=%s\nexit=%s\n' "$stage" "$result" >&3
        fi
    fi
    printf 'guest: %s exit=%s\n' "$stage" "$result" >/dev/console
    "$control" reboot -f
    exit "$result"
}
trap finish EXIT
mount -t devtmpfs devtmpfs /dev
exec </dev/null >/dev/console 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys

port() {
    attempt=0
    while [ "$attempt" -lt 100 ]; do
        for device in /sys/class/virtio-ports/vport*; do
            [ -r "$device/name" ] || continue
            IFS= read -r name < "$device/name"
            if [ "$name" = "kernel-test.$1" ] && [ -c "/dev/${device##*/}" ]; then
                printf '/dev/%s\n' "${device##*/}"
                return
            fi
        done
        sleep .1
        attempt=$((attempt + 1))
    done
    printf 'virtio port not available: %s\n' "$1" >&2
    return 1
}

stage=identify
status=$(port status)
exec 3>"$status"
status_ready=1
. /etc/os-release
[ "$ID" = ubuntu ]
IFS= read -r mode < /kernel-test/mode
release=$(uname -r)
printf 'distro=%s\nversion=%s\nkernel=%s\nmode=%s\n' "$ID" "$VERSION_ID" "$release" "$mode" >&3
stage=module-release
IFS= read -r expected < /kernel-test/module-release
[ -z "$expected" ] || [ "$expected" = "$release" ] || exit 1
stage=preload
while IFS= read -r module; do
    "$control" insmod "$KERNEL_TEST_MODULES/$module"
done < /kernel-test/preload
printf 'ready=yes\n' >&3

if [ "$mode" = profile ]; then
    stage=start
    trace_port=$(port trace)
    recorder=/dev/kconfig-trace
    [ -c "$recorder" ]
    printf 'start\n' > "$recorder"
fi
stage=late-load
late_count=0
while IFS= read -r module; do
    printf 'guest: loading %s after readiness\n' "$module"
    "$control" insmod "$KERNEL_TEST_MODULES/$module"
    late_count=$((late_count + 1))
done < /kernel-test/late
printf 'late_modules=%s\n' "$late_count" >&3
stage=workload
/kernel-test/workload 3>&-
if [ "$mode" = profile ]; then
    stage=stop
    printf 'stop\n' > "$recorder"
    cat "$recorder" > /kernel-test/trace
    stage=export
    [ -s /kernel-test/trace ]
    cat /kernel-test/trace > "$trace_port"
fi
stage=complete
