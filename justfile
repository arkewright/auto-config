set shell := ["bash", "-eu", "-o", "pipefail", "-c"]
set positional-arguments

default:
    @just --list

# Run component tests built with the selected host configuration.
check envfile=".env":
    ROOT="$PWD"; source "$(realpath -- "$1")"; LD_LIBRARY_PATH="$LLVM_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ctest --test-dir "$HOST_BUILD" --no-tests=error --output-on-failure

# Exercise real builds, collection and replay in four fresh x86_64 Ubuntu guests.
replay output envfile=".env":
    qemu/replay.sh "$1" "$2"
