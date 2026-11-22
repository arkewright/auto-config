#!/bin/sh
# Executed only in a disposable guest while the outer runner owns the active window.
set -eu
recorder=/dev/kconfig-trace
if printf 'start\n' > "$recorder"; then exit 1; fi
if printf 'junks\n' > "$recorder"; then exit 1; fi
if cat "$recorder" > /dev/null; then exit 1; fi
