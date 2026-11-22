#!/usr/bin/env bash
set -euo pipefail
clang=$1
plugin=$2
here=$(cd -- "$(dirname -- "$0")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
cd "$tmp"
flags=(-fplugin="$plugin" -fplugin-arg-kcfgtrace-id=65535 -fplugin-arg-kcfgtrace-receipt="$tmp/count"
       -include "$here/../../driver/probe.h" '-DKCFG_LINK_NAME="kcfg_hit_test"')

for level in -O0 -O2; do
  "$clang" "${flags[@]}" "$level" -Werror -c "$here/sample.c" -o sample.o
  [[ $(< count) == 2 ]]
  "$clang" "$level" "$here/runtime.c" sample.o -o sample
  ./sample
  "$clang" "${flags[@]}" "$level" -S -emit-llvm "$here/sample.c" -o sample.ll
  rg -q 'call void @kcfg_hit_test\(i32[^)]* 65535\)' sample.ll
  rm -- count
done

printf 'static inline int never(void) { return 42; }\n' > empty.c
"$clang" "${flags[@]}" -c empty.c -o empty.o
[[ $(< count) == 0 ]]
rm -- count

reject() {
  if "$@" > diagnostics 2>&1; then
    printf 'unexpected success: %s\n' "$*" >&2
    exit 1
  fi
  [[ ! -e count ]]
}
for id in 0 -1 65536 0xff 1abc; do
  reject "$clang" -fplugin="$plugin" -fplugin-arg-kcfgtrace-id="$id" \
    -fplugin-arg-kcfgtrace-receipt="$tmp/count" -c empty.c
done
reject "$clang" -fplugin="$plugin" -fplugin-arg-kcfgtrace-id=1 -c empty.c
reject "$clang" "${flags[@]}" -fplugin-arg-kcfgtrace-id=2 -c empty.c
reject "$clang" "${flags[@]}" -x c++ -c empty.c
reject "$clang" -fplugin="$plugin" -fplugin-arg-kcfgtrace-id=1 \
  -fplugin-arg-kcfgtrace-receipt="$tmp/count" -c empty.c
printf 'extern int kcfg_hit(unsigned int);\n' > wrong.h
reject "$clang" -fplugin="$plugin" -fplugin-arg-kcfgtrace-id=1 \
  -fplugin-arg-kcfgtrace-receipt="$tmp/count" -include wrong.h -c empty.c
reject "$clang" -fplugin="$plugin" -fplugin-arg-kcfgtrace-id=1 \
  -fplugin-arg-kcfgtrace-receipt="$tmp/missing-dir/count" -include "$here/../../driver/probe.h" \
  '-DKCFG_LINK_NAME="kcfg_hit_test"' -c empty.c
printf 'int bad(void) { return syntax error; }\n' > broken.c
reject "$clang" "${flags[@]}" -c broken.c
printf 'void bad(void) { __asm__(".error \\"bad insn\\""); }\n' > bad-asm.c
reject "$clang" "${flags[@]}" -c bad-asm.c
