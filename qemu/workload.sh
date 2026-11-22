#!/bin/sh
# Runs inside the Ubuntu guest, not on the host.
set -eu
file=$(mktemp)
trap 'rm -f "$file"' EXIT
/usr/bin/dd if=/dev/zero of="$file" bs=4K count=16 status=none
[ "$(/usr/bin/wc -c < "$file")" -eq 65536 ]
/usr/bin/sha256sum "$file"
