#!/bin/sh
set -eu
# Read real kernel configuration through the same interface in both images.
[ -s /proc/config.gz ]
gzip -t /proc/config.gz
gzip -dc /proc/config.gz | grep -q '^CONFIG_IKCONFIG_PROC=y$'
printf 'kernel configuration is readable\n'
