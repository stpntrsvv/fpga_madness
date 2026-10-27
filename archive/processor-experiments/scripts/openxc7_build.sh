#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec sh "$root/scripts/build_profile.sh" default "$root/build/fpga_madness.bit"
